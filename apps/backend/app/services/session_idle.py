"""Inactividad de una ``ChatSession``: cuándo una conversación se da por terminada.

Una sesión de chat queda inactiva cuando pasaron ``SESSION_IDLE_CLOSE_MINUTES`` desde su
última actividad. La última actividad es el ``created_at`` del turno más reciente en
``conversation_turns`` o, si la sesión no tiene turnos (recién creada, o con turnos
degradados que no se persisten), su ``started_at``. ``sessions`` no se actualiza en cada
turno, así que el turno es la única marca de actividad confiable.

Lo usan dos lugares, con el mismo criterio:

- ``resolve_chat_session``: un turno que llega a una sesión inactiva abre una nueva
  (la conversación anterior ya terminó, aunque el beat todavía no la haya cerrado).
- ``close_idle_sessions`` (beat): cierra las inactivas y encola su consolidación
  episódica, con un margen extra (``CLOSE_GRACE``) para no cerrar una sesión con un
  turno todavía en vuelo.

Sin migraciones: la subquery usa el índice ``(user_id, session_id, seq)`` existente.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.llm.clients.factory import PROVIDER_TOTAL_BUDGET_S
from app.llm.tool_loop import MAX_TOOL_ITERATIONS
from app.models.conversation_turn import ConversationTurn
from app.models.session import ChatSession

# Margen del beat por encima del umbral de inactividad. ``resolve_chat_session`` deja de
# usar una sesión apenas vence el umbral, pero un turno aceptado justo antes puede tardar
# en commitear sus filas (llamada al modelo + tools). El beat espera este margen extra para
# no cerrar, y consolidar, una sesión cuyo último turno todavía no se escribió.
#
# El peor caso de un turno son MAX_TOOL_ITERATIONS + 1 llamadas al modelo (las vueltas de
# tools más la respuesta final), cada una acotada por PROVIDER_TOTAL_BUDGET_S con
# reintentos y fallback incluidos: 6 x 90 s = 9 minutos. La holgura cubre la recuperación
# de memoria (embeddings) y la escritura del turno.
WORST_TURN = timedelta(seconds=(MAX_TOOL_ITERATIONS + 1) * PROVIDER_TOTAL_BUDGET_S)
CLOSE_GRACE = WORST_TURN + timedelta(minutes=6)

# Antigüedad máxima de una sesión cerrada para consolidarla. El episodio nace con fecha de
# hoy y su retención se cuenta desde que se crea: resumir una conversación de hace meses
# la haría volver como reciente y vivir otra ventana de retención entera. El beat no
# encola las más viejas y el backlog de admin no las cuenta (no hay que reencolarlas).
CONSOLIDATION_MAX_AGE = timedelta(days=7)


def idle_threshold(idle_minutes: int | None = None) -> timedelta:
    """Umbral de inactividad (``SESSION_IDLE_CLOSE_MINUTES`` si no se pasa)."""
    if idle_minutes is None:
        idle_minutes = get_settings().session_idle_close_minutes
    return timedelta(minutes=idle_minutes)


def last_activity_expr() -> ColumnElement[datetime]:
    """Expresión SQL de la última actividad de cada fila de ``sessions``.

    Subquery correlacionada: el ``created_at`` del turno de mayor ``seq`` de la sesión
    (índice ``(user_id, session_id, seq)``), o ``started_at`` si no tiene turnos.
    """
    latest_turn = (
        select(ConversationTurn.created_at)
        .where(
            ConversationTurn.user_id == ChatSession.user_id,
            ConversationTurn.session_id == ChatSession.id,
        )
        .order_by(ConversationTurn.seq.desc())
        .limit(1)
        .correlate(ChatSession)
        .scalar_subquery()
    )
    return func.coalesce(latest_turn, ChatSession.started_at)


def has_turns_expr() -> ColumnElement[bool]:
    """``EXISTS`` correlacionado: la fila de ``sessions`` tiene turnos sin consolidar."""
    return (
        exists()
        .where(
            ConversationTurn.user_id == ChatSession.user_id,
            ConversationTurn.session_id == ChatSession.id,
        )
        .correlate(ChatSession)
    )


async def last_activity(session: AsyncSession, chat_session: ChatSession) -> datetime:
    """Última actividad de ``chat_session``: su turno más reciente o su ``started_at``.

    Se resuelve en SQL (misma expresión que el beat) y no leyendo ``started_at`` del
    ORM: con ``server_default`` el atributo puede no estar cargado tras el ``flush``.
    """
    stmt = select(last_activity_expr()).where(ChatSession.id == chat_session.id)
    return (await session.execute(stmt)).scalar_one()


async def is_idle(
    session: AsyncSession,
    chat_session: ChatSession,
    *,
    now: datetime,
    idle_minutes: int | None = None,
) -> bool:
    """``True`` si pasó el umbral de inactividad desde la última actividad."""
    return await last_activity(session, chat_session) <= now - idle_threshold(idle_minutes)


__all__ = [
    "CLOSE_GRACE",
    "CONSOLIDATION_MAX_AGE",
    "WORST_TURN",
    "has_turns_expr",
    "idle_threshold",
    "is_idle",
    "last_activity",
    "last_activity_expr",
]
