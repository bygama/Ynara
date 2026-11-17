"""Marca de turno consolidado en ``conversation_turns`` (idempotencia de ``consolidate_turn``).

``consolidate_turn`` ackea tarde y reintenta (ver ``consolidation.py``): si el worker
muere despues del commit y antes del ack, el broker re-entrega el mensaje y el mismo turno
se consolidaria dos veces (hechos y auditoria duplicados). Para que esa re-entrega no
escriba nada, la consolidacion marca el turno del usuario en la MISMA transaccion que
escribe la memoria: el turno queda marcado si y solo si sus ops se commitearon.

La marca usa una columna que ya existe, sin migracion: el ``updated_at`` del turno del
usuario. El contenido de un turno nunca se edita, asi que ``updated_at`` nace igual a
``created_at`` (los dos salen del ``now()`` del INSERT); la consolidacion lo pisa con
``clock_timestamp()``, la hora real del momento, que siempre difiere. ``conversation_turns``
es operativa, no sagrada (regla #3), y la consulta vive aca, en la capa de workflows y
contra el modelo: los stores de ``app/memory/`` no cambian.

Concurrencia: ``SELECT ... FOR UPDATE`` sobre la fila. Dos entregas simultaneas del mismo
turno se serializan: la segunda espera el commit de la primera y ve la marca; si la primera
revierte (una falla transitoria), la segunda encuentra el turno pendiente y lo toma.

Si la fila no esta (el cierre de la sesion ya purgo los turnos, o el turno no se pudo
persistir) no hay donde marcar y se consolida igual, porque saltearlo perderia el turno.
En ese caso una re-entrega podria duplicar: hace falta que el worker muera entre el commit
y el ack, y que los turnos ya no existan.
"""

from __future__ import annotations

import enum
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import TurnRole
from app.models.conversation_turn import ConversationTurn

__all__ = ["TurnClaim", "claim_turn"]


class TurnClaim(enum.Enum):
    """Resultado de ``claim_turn``."""

    CLAIMED = "claimed"
    """El turno estaba pendiente y quedo marcado en esta transaccion."""

    ALREADY_CONSOLIDATED = "already_consolidated"
    """Otra entrega ya lo consolido y commiteo: no hay que volver a escribir."""

    MISSING = "missing"
    """No hay fila del turno (purgada o nunca persistida): se consolida sin marca."""


async def claim_turn(
    session: AsyncSession, *, user_id: UUID, session_id: UUID, seq: int
) -> TurnClaim:
    """Marca como consolidado el turno del usuario ``(session_id, seq)`` si estaba pendiente.

    Corre dentro de la transaccion de la consolidacion: si despues algo falla y se
    revierte, la marca se revierte con las ops y el reintento vuelve a encontrar el turno
    pendiente. Filtra por ``user_id`` (aislamiento por usuario) y por el rol ``user``. El
    lock de la fila dura hasta el commit o el rollback de esa transaccion.
    """
    row = (
        await session.execute(
            select(
                ConversationTurn.id,
                (ConversationTurn.updated_at != ConversationTurn.created_at).label("consolidated"),
            )
            .where(
                ConversationTurn.user_id == user_id,
                ConversationTurn.session_id == session_id,
                ConversationTurn.seq == seq,
                ConversationTurn.role == TurnRole.USER,
            )
            .with_for_update()
        )
    ).one_or_none()
    if row is None:
        return TurnClaim.MISSING
    if row.consolidated:
        return TurnClaim.ALREADY_CONSOLIDATED
    await session.execute(
        update(ConversationTurn)
        .where(ConversationTurn.id == row.id)
        # clock_timestamp() y no now(): now() es el inicio de la transaccion, que en una
        # misma transaccion (los tests con la sesion inyectada) coincide con created_at.
        .values(updated_at=func.clock_timestamp())
        .execution_options(synchronize_session=False)
    )
    return TurnClaim.CLAIMED
