"""Helper de ciclo de vida de ChatSession para el endpoint /v1/chat.

``resolve_chat_session`` resuelve (crea o valida) la sesión activa del
usuario antes de invocar el router LLM. NO commitea: el commit lo hace el
endpoint al final del request, después de que route() devuelve (fix del
bug de commit-temprano documentado en las decisiones de diseño M9).

Una sesión cerrada (``ended_at``) o inactiva (``SESSION_IDLE_CLOSE_MINUTES`` sin
turnos) no recibe más turnos: el mensaje abre una sesión nueva y su id vuelve al
cliente en la respuesta (``session_id`` del JSON o del evento ``done``), que el
cliente adopta. La vieja la cierra el beat ``close_idle_sessions``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Mode
from app.models.session import ChatSession
from app.services.session_idle import is_idle


async def resolve_chat_session(
    session: AsyncSession,
    *,
    user_id: UUID,
    session_id: UUID | None,
    mode: Mode,
    now: datetime | None = None,
    idle_minutes: int | None = None,
) -> ChatSession:
    """Crea o valida la ChatSession del usuario para el turno de chat.

    - ``session_id`` es ``None`` → crea una nueva ChatSession, hace
      ``flush()`` para que el ORM asigne el id (sin commit; el commit lo
      hace el endpoint al final del request).
    - ``session_id`` presente → busca la sesión; lanza 404 si no existe o
      pertenece a otro usuario (sin dar oráculo de existencia ajena).
    - Si la sesión ya terminó (cerrada, o sin turnos hace
      ``SESSION_IDLE_CLOSE_MINUTES``) → crea una nueva en el ``mode`` pedido, igual
      que con ``session_id`` ``None``. No la cierra acá: lo hace el beat, que
      además encola la consolidación episódica post-commit.
    - Sesión abierta con otro modo → 409.

    ``now`` e ``idle_minutes`` son inyectables para tests (default: reloj real y
    ``SESSION_IDLE_CLOSE_MINUTES``).

    No propaga errores del ORM; si hay un problema de infraestructura el
    ``get_db()`` wrapper hace rollback.
    """
    if session_id is None:
        return await _new_session(session, user_id=user_id, mode=mode)

    cs = await session.get(ChatSession, session_id)

    if cs is None or cs.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="sesion no encontrada",
        )

    current = now or datetime.now(UTC)
    if cs.ended_at is not None or await is_idle(
        session, cs, now=current, idle_minutes=idle_minutes
    ):
        return await _new_session(session, user_id=user_id, mode=mode)

    if cs.mode != mode:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="no se puede cambiar de modo en una sesion abierta",
        )

    return cs


async def _new_session(session: AsyncSession, *, user_id: UUID, mode: Mode) -> ChatSession:
    """Crea una ChatSession y hace ``flush()`` para que el ORM asigne el id (sin commit)."""
    cs = ChatSession(user_id=user_id, mode=mode)
    session.add(cs)
    await session.flush()
    return cs
