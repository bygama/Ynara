"""Tests de ciclo de vida de ChatSession via resolve_chat_session.

Todos los tests son ``integration`` y usan ``db_session`` (rollback al
final; nunca persisten entre tests). Se necesita TEST_DATABASE_URL apuntando
a la DB de tests dedicada.

Casos cubiertos:
- Crear nueva sesión (session_id None) → ChatSession con user_id+mode, id asignado.
- Lookup OK (mismo user) → devuelve la misma instancia (mismo id).
- session_id de otro user → 404 (aislamiento, sin oráculo de existencia ajena).
- session_id inexistente → 404.
- mode mismatch → 409.
- Sesión cerrada o inactiva → abre una nueva (aunque cambie el modo); la ajena
  cerrada sigue dando 404.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1._sessions import resolve_chat_session
from app.enums import Mode, TurnRole
from app.models.conversation_turn import ConversationTurn
from app.models.session import ChatSession
from app.models.user import User


async def _seed_user(session: AsyncSession) -> User:
    """Inserta un User mínimo y hace flush para que tenga id asignado."""
    user = User()
    session.add(user)
    await session.flush()
    return user


# ---------- crear sesión nueva ----------


@pytest.mark.integration
async def test_create_session_when_session_id_is_none(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)

    cs = await resolve_chat_session(
        db_session,
        user_id=user.id,
        session_id=None,
        mode=Mode.PRODUCTIVIDAD,
    )

    assert cs.id is not None
    assert cs.user_id == user.id
    assert cs.mode == Mode.PRODUCTIVIDAD


# ---------- lookup OK ----------


@pytest.mark.integration
async def test_lookup_existing_session_same_user(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)

    cs_created = await resolve_chat_session(
        db_session,
        user_id=user.id,
        session_id=None,
        mode=Mode.ESTUDIO,
    )

    cs_looked_up = await resolve_chat_session(
        db_session,
        user_id=user.id,
        session_id=cs_created.id,
        mode=Mode.ESTUDIO,
    )

    assert cs_looked_up.id == cs_created.id


# ---------- aislamiento: sesión de otro usuario ----------


@pytest.mark.integration
async def test_session_of_other_user_raises_404(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session)
    intruder = await _seed_user(db_session)

    cs = await resolve_chat_session(
        db_session,
        user_id=owner.id,
        session_id=None,
        mode=Mode.BIENESTAR,
    )

    with pytest.raises(HTTPException) as exc_info:
        await resolve_chat_session(
            db_session,
            user_id=intruder.id,
            session_id=cs.id,
            mode=Mode.BIENESTAR,
        )

    assert exc_info.value.status_code == 404


# ---------- sesión inexistente ----------


@pytest.mark.integration
async def test_nonexistent_session_raises_404(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    nonexistent_id = uuid.uuid4()

    with pytest.raises(HTTPException) as exc_info:
        await resolve_chat_session(
            db_session,
            user_id=user.id,
            session_id=nonexistent_id,
            mode=Mode.VIDA,
        )

    assert exc_info.value.status_code == 404


# ---------- mode mismatch ----------


@pytest.mark.integration
async def test_mode_mismatch_raises_409(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)

    cs = await resolve_chat_session(
        db_session,
        user_id=user.id,
        session_id=None,
        mode=Mode.PRODUCTIVIDAD,
    )

    with pytest.raises(HTTPException) as exc_info:
        await resolve_chat_session(
            db_session,
            user_id=user.id,
            session_id=cs.id,
            mode=Mode.ESTUDIO,
        )

    assert exc_info.value.status_code == 409


# ---------- sesión terminada: cerrada o inactiva ----------

_IDLE_MINUTES = 30


async def _seed_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    mode: Mode,
    started_at: datetime,
    ended_at: datetime | None = None,
    last_turn_at: datetime | None = None,
) -> ChatSession:
    cs = ChatSession(user_id=user_id, mode=mode, started_at=started_at, ended_at=ended_at)
    session.add(cs)
    await session.flush()
    if last_turn_at is not None:
        session.add(
            ConversationTurn(
                user_id=user_id,
                session_id=cs.id,
                role=TurnRole.USER,
                content=b"sintetico",
                seq=1,
                created_at=last_turn_at,
            )
        )
        await session.flush()
    return cs


async def _resolve(
    session: AsyncSession, *, user_id: uuid.UUID, cs: ChatSession, mode: Mode, now: datetime
) -> ChatSession:
    return await resolve_chat_session(
        session,
        user_id=user_id,
        session_id=cs.id,
        mode=mode,
        now=now,
        idle_minutes=_IDLE_MINUTES,
    )


@pytest.mark.integration
async def test_closed_session_opens_a_new_one(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    now = datetime.now(UTC)
    closed = await _seed_session(
        db_session,
        user_id=user.id,
        mode=Mode.VIDA,
        started_at=now - timedelta(minutes=10),
        ended_at=now - timedelta(minutes=1),
        last_turn_at=now - timedelta(minutes=2),
    )

    cs = await _resolve(db_session, user_id=user.id, cs=closed, mode=Mode.VIDA, now=now)

    assert cs.id != closed.id
    assert cs.user_id == user.id
    assert cs.mode == Mode.VIDA
    assert cs.ended_at is None


@pytest.mark.integration
async def test_closed_session_with_other_mode_opens_new_instead_of_409(
    db_session: AsyncSession,
) -> None:
    user = await _seed_user(db_session)
    now = datetime.now(UTC)
    closed = await _seed_session(
        db_session,
        user_id=user.id,
        mode=Mode.ESTUDIO,
        started_at=now - timedelta(minutes=10),
        ended_at=now - timedelta(minutes=1),
    )

    cs = await _resolve(db_session, user_id=user.id, cs=closed, mode=Mode.BIENESTAR, now=now)

    assert cs.id != closed.id
    assert cs.mode == Mode.BIENESTAR


@pytest.mark.integration
async def test_idle_session_opens_a_new_one(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    now = datetime.now(UTC)
    idle = await _seed_session(
        db_session,
        user_id=user.id,
        mode=Mode.PRODUCTIVIDAD,
        started_at=now - timedelta(hours=2),
        last_turn_at=now - timedelta(minutes=_IDLE_MINUTES),
    )

    cs = await _resolve(db_session, user_id=user.id, cs=idle, mode=Mode.PRODUCTIVIDAD, now=now)

    assert cs.id != idle.id
    # resolve no cierra la vieja: eso lo hace el beat, que encola la consolidación.
    await db_session.refresh(idle)
    assert idle.ended_at is None


@pytest.mark.integration
async def test_idle_session_without_turns_opens_a_new_one(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    now = datetime.now(UTC)
    idle = await _seed_session(
        db_session, user_id=user.id, mode=Mode.VIDA, started_at=now - timedelta(hours=1)
    )

    cs = await _resolve(db_session, user_id=user.id, cs=idle, mode=Mode.VIDA, now=now)

    assert cs.id != idle.id


@pytest.mark.integration
async def test_recent_turn_keeps_the_session(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    now = datetime.now(UTC)
    active = await _seed_session(
        db_session,
        user_id=user.id,
        mode=Mode.VIDA,
        started_at=now - timedelta(hours=3),
        last_turn_at=now - timedelta(minutes=_IDLE_MINUTES - 1),
    )

    cs = await _resolve(db_session, user_id=user.id, cs=active, mode=Mode.VIDA, now=now)

    assert cs.id == active.id


@pytest.mark.integration
async def test_closed_session_of_other_user_still_raises_404(db_session: AsyncSession) -> None:
    owner = await _seed_user(db_session)
    intruder = await _seed_user(db_session)
    now = datetime.now(UTC)
    closed = await _seed_session(
        db_session,
        user_id=owner.id,
        mode=Mode.VIDA,
        started_at=now - timedelta(minutes=10),
        ended_at=now - timedelta(minutes=1),
    )

    with pytest.raises(HTTPException) as exc_info:
        await _resolve(db_session, user_id=intruder.id, cs=closed, mode=Mode.VIDA, now=now)

    assert exc_info.value.status_code == 404
