"""Worker de retención de ``conversation_turns``: borra los turnos crudos huérfanos.

``conversation_turns`` es un buffer transitorio: la consolidación episódica
(``consolidate_session``) lee los turnos de una sesión cerrada, escribe el episodio y los
purga. Pero no toda sesión cerrada se consolida:

- el beat ``close_idle_sessions`` no encola las que terminaron hace más de
  ``CONSOLIDATION_MAX_AGE`` (7 días): el episodio se fecharía hoy;
- una consolidación puede fallar del todo (broker caído, reintentos agotados, un
  transcript que no entra en el contexto del modelo).

Sin este worker, esos turnos (texto crudo de la conversación, cifrado) quedaban para
siempre. Pasado ``CONSOLIDATION_MAX_AGE`` ya no se van a consolidar ni a reencolar (el
backlog de admin tampoco las cuenta), así que no sirven para nada.

Qué borra: los turnos de las sesiones cerradas cuyo ``ended_at`` quedó hace más de
``TURN_RETENTION`` (7 días más 1 de margen). Ojo: cuando cierra el beat de inactividad,
``ended_at`` es la última actividad, no la hora del cierre; en un cierre manual es la
hora del cierre. Las sesiones abiertas (``ended_at`` NULL) no se tocan: el beat de
inactividad las cierra primero.

Qué cubre el margen: una consolidación encolada justo antes del límite que todavía
esté reintentando (sus reintentos suman menos de una hora). No cubre una que espere
más de un día en la cola: esa encuentra la sesión sin turnos y termina sin llamar al
modelo. El único cruce posible es una consolidación así de atrasada que lea los
turnos justo mientras corre la purga: los lotes no van por sesión, así que podría
resumir un transcript a medias.

Mismo patrón que ``episodic_retention.py``: job GLOBAL por tiempo, sin filtro
``user_id``; ``DELETE`` por lotes (``delete_in_batches``); no lee ni descifra el
``content`` (regla #4: solo viajan conteos); ``now`` y ``session`` inyectables para
tests; fail-open (un fallo no tumba el worker y se loguea solo el tipo de error).
``conversation_turns`` es operativa, no sagrada (regla #3), y no hace falta migración:
el filtro usa ``sessions.ended_at`` y el ``session_id`` de cada turno.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.conversation_turn import ConversationTurn
from app.models.session import ChatSession
from app.services.session_idle import CONSOLIDATION_MAX_AGE
from app.workers.celery_app import celery_app
from app.workflows._engine import DELETE_BATCH_SIZE, delete_in_batches, worker_session

logger = logging.getLogger(__name__)

__all__ = ["TURN_RETENTION", "purge_stale_turns"]

# Cuánto viven los turnos de una sesión cerrada que no se consolidó: el límite para
# consolidarla más un día de margen para los reintentos de una consolidación en curso.
TURN_RETENTION = CONSOLIDATION_MAX_AGE + timedelta(days=1)


def _stale_predicate(now: datetime) -> ColumnElement[bool]:
    """``True`` para los turnos de sesiones con ``ended_at`` de hace más de ``TURN_RETENTION``.

    Predicado ``<`` estricto: una sesión cerrada justo en el borde conserva sus turnos.
    Una sesión abierta tampoco matchea: ``NULL < x`` no es verdadero en SQL.
    """
    stale_sessions = select(ChatSession.id).where(
        ChatSession.ended_at < now - TURN_RETENTION,
    )
    return ConversationTurn.session_id.in_(stale_sessions)


async def _async_purge_stale_turns(
    *,
    session: AsyncSession | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    batch_size: int = DELETE_BATCH_SIZE,
    on_batch: Callable[[int], None] | None = None,
) -> int:
    """Borra los turnos de sesiones cerradas hace más de ``TURN_RETENTION``. Devuelve cuántos.

    Con ``session`` inyectada (tests de integración) la usa y NO commitea (el fixture
    hace rollback). Sin ella (worker en prod) arma el engine ``NullPool`` y commitea por
    lote, igual que ``episodic_retention.py``. ``on_batch`` recibe las filas de cada
    lote después de su commit (o de su ``flush`` con la sesión inyectada; ver
    ``delete_in_batches``).
    """
    stale = _stale_predicate(now or datetime.now(UTC))

    if session is not None:
        return await delete_in_batches(
            session,
            ConversationTurn,
            stale,
            batch_size=batch_size,
            commit=False,
            on_batch=on_batch,
        )

    cfg = settings or get_settings()
    async with worker_session(cfg) as db_session:
        return await delete_in_batches(
            db_session,
            ConversationTurn,
            stale,
            batch_size=batch_size,
            commit=True,
            on_batch=on_batch,
        )


@celery_app.task(bind=True, name="workflows.purge_stale_turns")
def purge_stale_turns(self) -> None:  # bind=True, self no se usa (sin retry)
    """Task Celery: borra los turnos crudos de sesiones cerradas que ya no se consolidan.

    Job GLOBAL sin argumentos. Fail-open: un fallo no tumba el worker y se loguea solo
    el conteo o el tipo de error, sin datos de usuario (regla #4).

    Si la corrida falla (por ejemplo, el soft time limit con un backlog grande), los
    lotes ya commiteados quedan borrados y el log de error dice cuántos fueron, como
    mínimo: si el fallo cae entre el commit de un lote y su conteo, ese lote no suma.
    """
    committed = 0

    def _count(deleted: int) -> None:
        nonlocal committed
        committed += deleted

    try:
        deleted = asyncio.run(_async_purge_stale_turns(on_batch=_count))
        logger.info("purge_stale_turns: deleted=%d", deleted)
    except Exception as exc:
        # logger.error (NO logger.exception): el traceback podría arrastrar contenido.
        logger.error(
            "purge_stale_turns: fallo al purgar conversation_turns: %s, al menos %d "
            "borrados antes del fallo (sin datos de usuario)",
            type(exc).__name__,
            committed,
        )
