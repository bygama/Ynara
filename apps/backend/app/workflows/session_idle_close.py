"""Worker que cierra las sesiones de chat inactivas y encola su memoria episódica.

Hasta este worker, la consolidación episódica (``consolidate_session``) solo corría si
el cliente llamaba ``POST /v1/sessions/{id}/close``, y ningún cliente lo hace. Una
conversación se da por terminada cuando pasan ``SESSION_IDLE_CLOSE_MINUTES`` sin turnos
(ver ``app/services/session_idle.py``); este scan GLOBAL por tiempo (igual que
``dispatch_due_reminders``) las cierra y encola su consolidación.

Reglas:

1. UPDATE de ``sessions.ended_at`` (tabla operativa, no sagrada); sin cambios de schema.
   La última actividad sale del turno más reciente (índice ``(user_id, session_id,
   seq)``) o de ``started_at``.
2. Cierre CONDICIONAL: ``UPDATE ... WHERE ended_at IS NULL AND <sigue inactiva>
   RETURNING``. Un cierre manual concurrente o un turno nuevo entre el SELECT y el
   UPDATE dejan la fila afuera, así no se encola dos veces ni se cierra una sesión
   que volvió a tener actividad.
3. Umbral del beat = ``SESSION_IDLE_CLOSE_MINUTES`` + ``CLOSE_GRACE``: un turno aceptado
   justo antes del umbral tiene ese margen para commitear sus filas antes del cierre (la
   consolidación purga los turnos de la sesión, y uno que llegara después quedaría
   fuera del episodio).
4. Tope de ``CLOSE_BATCH_SIZE`` sesiones por corrida: el primer scan tras el deploy
   encuentra todas las sesiones abiertas históricas, y cada una es una llamada al
   modelo en el worker de consolidación. El tope reparte esa carga entre corridas.
5. ``ended_at`` = la última actividad (el último turno, o ``started_at``), no la hora
   del scan: la conversación terminó ahí, y así la duración de las métricas de admin
   (``ended_at - started_at``) no suma los minutos de espera del cierre.
6. Encolado POST-commit con los mismos kwargs que ``close_session``, fail-open: si el
   broker está caído la sesión queda cerrada igual y se loguea solo el tipo de error
   (regla #4: sin contenido de usuario). Igual que en el cierre manual, un encolado
   perdido deja esa sesión sin episodio.
7. Solo se encola si la sesión tiene turnos (sin turnos no hay nada que resumir) y si
   terminó hace menos de ``CONSOLIDATION_MAX_AGE``. El episodio nace con fecha de hoy y
   su retención se cuenta desde que se crea: consolidar una conversación de hace meses
   la haría volver como reciente y vivir otra ventana de retención entera. Las más
   viejas se cierran sin episodio; sus turnos los borra ``purge_stale_turns``
   (``turn_retention.py``) 8 días después de su ``ended_at``.

CADENCIA (beat): cada 5 minutos (ver ``celery_app.py``). Con el umbral por default, una
sesión se cierra entre 45 y 50 minutos después de su último turno.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Row, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.enums import Mode
from app.models.session import ChatSession
from app.services.session_idle import (
    CLOSE_GRACE,
    CONSOLIDATION_MAX_AGE,
    has_turns_expr,
    idle_threshold,
    last_activity_expr,
)
from app.workers.celery_app import celery_app
from app.workflows._engine import worker_session
from app.workflows.consolidation import consolidate_session

logger = logging.getLogger(__name__)

# Sesiones cerradas como máximo por corrida (ver regla 4 del docstring).
CLOSE_BATCH_SIZE = 100


async def _close_sessions(
    db: AsyncSession, *, ids: list[UUID], cutoff: datetime
) -> list[Row[tuple[UUID, UUID, Mode, datetime, bool]]]:
    """Cierra las sesiones de ``ids`` que SIGUEN abiertas e inactivas al ``cutoff``.

    ``UPDATE`` condicional: entre el ``SELECT`` de candidatas y acá pudo llegar un
    cierre manual o un turno nuevo, y esas filas no matchean (ni se cierran ni se
    encolan). ``ended_at`` queda en la última actividad. Devuelve, por sesión cerrada,
    ``(id, user_id, mode, ended_at, has_turns)``.
    """
    stmt = (
        update(ChatSession)
        .where(
            ChatSession.id.in_(ids),
            ChatSession.ended_at.is_(None),
            last_activity_expr() <= cutoff,
        )
        .values(ended_at=last_activity_expr())
        .returning(
            ChatSession.id,
            ChatSession.user_id,
            ChatSession.mode,
            ChatSession.ended_at,
            has_turns_expr(),
        )
        .execution_options(synchronize_session=False)
    )
    return list((await db.execute(stmt)).all())


async def _async_close_idle_sessions(
    *,
    session: AsyncSession | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
    batch_size: int = CLOSE_BATCH_SIZE,
) -> int:
    """Cierra hasta ``batch_size`` sesiones inactivas y encola su consolidación.

    Retorna cuántas cerró. ``now`` es inyectable (tests deterministas). Si ``session``
    se inyecta (tests) se usa directamente y NO se commitea (el fixture controla el
    rollback); el encolado corre igual, así los tests lo verifican con un mock. Si es
    ``None`` (worker en prod) se abre un engine ``NullPool``, se commitea y recién
    después se encola.
    """
    cfg = settings or get_settings()
    current = now or datetime.now(UTC)
    cutoff = current - idle_threshold(cfg.session_idle_close_minutes) - CLOSE_GRACE

    async def _run(db: AsyncSession, *, commit: bool) -> int:
        candidates = (
            select(ChatSession.id)
            .where(ChatSession.ended_at.is_(None), last_activity_expr() <= cutoff)
            .order_by(ChatSession.started_at.asc())
            .limit(batch_size)
        )
        ids = list((await db.execute(candidates)).scalars().all())
        if not ids:
            return 0

        closed = await _close_sessions(db, ids=ids, cutoff=cutoff)
        if commit:
            await db.commit()

        oldest_to_consolidate = current - CONSOLIDATION_MAX_AGE
        for session_id, user_id, mode, ended_at, has_turns in closed:
            if not has_turns or ended_at < oldest_to_consolidate:
                continue
            try:
                # Mismos kwargs que close_session: enqueued_at = ended_at. El guard de
                # borrado (was_wiped_after) descarta el episodio si el usuario borró su
                # memoria después de que terminó la conversación.
                consolidate_session.delay(
                    user_id=str(user_id),
                    session_id=str(session_id),
                    mode=mode.value,
                    enqueued_at=ended_at.isoformat(),
                )
            except Exception as exc:
                # fail-open: la sesión ya quedó cerrada. regla #4: solo el tipo de error.
                logger.warning(
                    "close_idle_sessions: no se pudo encolar consolidate_session session=%s: %s",
                    session_id,
                    type(exc).__name__,
                )
        return len(closed)

    if session is not None:
        return await _run(session, commit=False)

    async with worker_session(cfg) as db_session:
        return await _run(db_session, commit=True)


@celery_app.task(bind=True, name="workflows.close_idle_sessions")
def close_idle_sessions(self) -> None:  # bind=True, self no se usa (sin retry)
    """Task Celery: cierra las sesiones inactivas (cada 5 minutos vía beat).

    Job GLOBAL de mantenimiento, sin argumentos. El cuerpo async corre con
    ``asyncio.run`` (worker prefork). Un fallo NO tumba el worker y se loguea solo el
    tipo de error, sin datos de usuario (regla #4). Sin retry: la próxima corrida del
    beat retoma las que hayan quedado abiertas.
    """
    try:
        closed = asyncio.run(_async_close_idle_sessions())
        logger.info("close_idle_sessions: closed=%d", closed)
    except Exception as exc:
        logger.error(
            "close_idle_sessions: fallo al cerrar sesiones inactivas: %s (sin datos de usuario)",
            type(exc).__name__,
        )
