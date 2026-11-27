"""Tests del worker que cierra sesiones inactivas (``close_idle_sessions``).

Dos niveles:

- **Unit (sin DB)**: el task wrapper corre el cuerpo async y es fail-open, y el beat lo
  agenda cada 5 minutos.
- **Integración** (``integration``): siembra sesiones con turnos de distinta antigüedad y,
  con ``now`` inyectado, verifica que cierra SOLO las que pasaron el umbral + el margen,
  con ``ended_at`` en su última actividad; que encola ``consolidate_session`` con los
  mismos kwargs que el cierre manual, salvo las vacías y las de más de 7 días; que el
  UPDATE re-chequea cada candidata; que no toca las ya cerradas; que respeta el tope
  por corrida, y que no deshace el cierre si el broker cae.

``consolidate_session.delay`` va SIEMPRE mockeado: un encolado real podría llegar a un
worker local que llame al proveedor de IA.

Las sesiones sembradas arrancan en el año 2000, así un scan GLOBAL las encuentra antes que
cualquier sesión que otro test haya dejado commiteada; las aserciones van por id. El umbral
sale de la config (``idle_threshold()``), no de un literal.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Mode, TurnRole
from app.models.conversation_turn import ConversationTurn
from app.models.session import ChatSession
from app.models.user import User
from app.services.session_idle import (
    CLOSE_GRACE,
    CONSOLIDATION_MAX_AGE,
    WORST_TURN,
    idle_threshold,
)
from app.workflows.session_idle_close import (
    _async_close_idle_sessions,
    _close_sessions,
    close_idle_sessions,
)

_LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)
_DELAY = "app.workflows.session_idle_close.consolidate_session.delay"

# ---------------------------------------------------------------------------
# Unit: task wrapper y beat
# ---------------------------------------------------------------------------


class TestCloseIdleTaskWrapper:
    def test_success_runs_async_and_returns_none(self) -> None:
        with patch(
            "app.workflows.session_idle_close._async_close_idle_sessions",
            new_callable=AsyncMock,
        ) as mock_async:
            mock_async.return_value = 2
            assert close_idle_sessions() is None
            mock_async.assert_awaited_once()

    def test_does_not_propagate_if_async_raises(self) -> None:
        with (
            patch(
                "app.workflows.session_idle_close._async_close_idle_sessions",
                new_callable=AsyncMock,
            ) as mock_async,
            patch("app.workflows.session_idle_close.logger.error") as mock_log,
        ):
            mock_async.side_effect = RuntimeError("DB caida")
            assert close_idle_sessions() is None
            mock_log.assert_called_once()


class TestIdleThreshold:
    def test_uses_the_setting_by_default(self) -> None:
        with patch("app.services.session_idle.get_settings") as mock_settings:
            mock_settings.return_value.session_idle_close_minutes = 45
            assert idle_threshold() == timedelta(minutes=45)

    def test_explicit_minutes_win(self) -> None:
        assert idle_threshold(10) == timedelta(minutes=10)

    def test_grace_covers_the_slowest_turn(self) -> None:
        # 6 llamadas al modelo (MAX_TOOL_ITERATIONS + la final) de 90 s cada una.
        assert WORST_TURN == timedelta(minutes=9)
        assert CLOSE_GRACE > WORST_TURN

    def test_default_setting_is_30_minutes(self) -> None:
        from app.core.config import Settings

        assert Settings.model_fields["session_idle_close_minutes"].default == 30


def test_beat_schedule_runs_every_five_minutes() -> None:
    from app.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["close-idle-sessions-every-5-minutes"]
    assert entry["task"] == "workflows.close_idle_sessions"
    assert entry["schedule"] == timedelta(minutes=5)


# ---------------------------------------------------------------------------
# Integración
# ---------------------------------------------------------------------------


async def _seed_user(session: AsyncSession) -> User:
    user = User()
    session.add(user)
    await session.flush()
    return user


async def _seed_session(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    mode: Mode = Mode.VIDA,
    started_at: datetime = _LONG_AGO,
    ended_at: datetime | None = None,
    turns_at: tuple[datetime, ...] = (),
) -> ChatSession:
    cs = ChatSession(user_id=user_id, mode=mode, started_at=started_at, ended_at=ended_at)
    session.add(cs)
    await session.flush()
    for seq, created_at in enumerate(turns_at, start=1):
        session.add(
            ConversationTurn(
                user_id=user_id,
                session_id=cs.id,
                role=TurnRole.USER if seq % 2 else TurnRole.MODEL,
                content=b"sintetico",
                seq=seq,
                created_at=created_at,
            )
        )
    await session.flush()
    return cs


def _enqueued_ids(mock_delay: MagicMock) -> set[str]:
    return {call.kwargs["session_id"] for call in mock_delay.call_args_list}


def _cutoff(now: datetime) -> datetime:
    return now - idle_threshold() - CLOSE_GRACE


@pytest.mark.integration
async def test_closes_only_sessions_past_threshold_and_grace(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    cutoff = _cutoff(now)
    user = await _seed_user(db_session)

    past_last_turn = cutoff - timedelta(minutes=5)
    past = await _seed_session(
        db_session,
        user_id=user.id,
        mode=Mode.BIENESTAR,
        turns_at=(cutoff - timedelta(minutes=10), past_last_turn),
    )
    exactly = await _seed_session(db_session, user_id=user.id, turns_at=(cutoff,))
    # Pasó el umbral pero no el margen: todavía no se cierra.
    within_grace = await _seed_session(
        db_session, user_id=user.id, turns_at=(cutoff + timedelta(minutes=1),)
    )
    # Arrancó hace mucho, pero su último turno es reciente.
    recent = await _seed_session(
        db_session,
        user_id=user.id,
        turns_at=(cutoff - timedelta(hours=1), now - timedelta(minutes=2)),
    )
    closed_before = now - timedelta(days=1)
    already_closed = await _seed_session(
        db_session, user_id=user.id, ended_at=closed_before, turns_at=(cutoff,)
    )

    with patch(_DELAY) as mock_delay:
        closed = await _async_close_idle_sessions(session=db_session, now=now)

    for cs in (past, exactly, within_grace, recent, already_closed):
        await db_session.refresh(cs)

    # ended_at = última actividad, no la hora del scan.
    assert past.ended_at == past_last_turn
    assert exactly.ended_at == cutoff
    assert within_grace.ended_at is None
    assert recent.ended_at is None
    assert already_closed.ended_at == closed_before

    enqueued = _enqueued_ids(mock_delay)
    assert {str(past.id), str(exactly.id)} <= enqueued
    assert not {str(within_grace.id), str(recent.id), str(already_closed.id)} & enqueued
    assert closed >= 2

    call = next(c for c in mock_delay.call_args_list if c.kwargs["session_id"] == str(past.id))
    assert call.kwargs == {
        "user_id": str(user.id),
        "session_id": str(past.id),
        "mode": Mode.BIENESTAR.value,
        "enqueued_at": past_last_turn.isoformat(),
    }


@pytest.mark.integration
async def test_closes_empty_and_old_sessions_without_consolidating(
    db_session: AsyncSession,
) -> None:
    """Sin turnos no hay qué resumir; una charla de hace más de 7 días no vuelve como nueva."""
    now = datetime.now(UTC)
    user = await _seed_user(db_session)
    empty = await _seed_session(db_session, user_id=user.id)
    old_turn = now - CONSOLIDATION_MAX_AGE - timedelta(hours=1)
    old = await _seed_session(db_session, user_id=user.id, turns_at=(old_turn,))
    young_turn = now - CONSOLIDATION_MAX_AGE + timedelta(hours=1)
    young = await _seed_session(db_session, user_id=user.id, turns_at=(young_turn,))

    with patch(_DELAY) as mock_delay:
        await _async_close_idle_sessions(session=db_session, now=now)

    for cs in (empty, old, young):
        await db_session.refresh(cs)

    assert empty.ended_at == _LONG_AGO
    assert old.ended_at == old_turn
    assert young.ended_at == young_turn
    enqueued = _enqueued_ids(mock_delay)
    assert str(young.id) in enqueued
    assert not {str(empty.id), str(old.id)} & enqueued


@pytest.mark.integration
async def test_close_step_rechecks_each_session(db_session: AsyncSession) -> None:
    """El UPDATE solo cierra las candidatas que siguen abiertas e inactivas.

    Simula lo que pasa entre el SELECT de candidatas y el UPDATE: una recibió un turno
    nuevo y otra se cerró a mano. Ninguna de las dos se toca.
    """
    now = datetime.now(UTC)
    cutoff = _cutoff(now)
    user = await _seed_user(db_session)
    idle = await _seed_session(db_session, user_id=user.id, turns_at=(cutoff,))
    revived = await _seed_session(
        db_session, user_id=user.id, turns_at=(cutoff, now - timedelta(minutes=1))
    )
    closed_before = now - timedelta(minutes=3)
    closed_meanwhile = await _seed_session(
        db_session, user_id=user.id, ended_at=closed_before, turns_at=(cutoff,)
    )

    rows = await _close_sessions(
        db_session, ids=[idle.id, revived.id, closed_meanwhile.id], cutoff=cutoff
    )

    for cs in (idle, revived, closed_meanwhile):
        await db_session.refresh(cs)
    assert [(row[0], row[4]) for row in rows] == [(idle.id, True)]
    assert idle.ended_at == cutoff
    assert revived.ended_at is None
    assert closed_meanwhile.ended_at == closed_before


@pytest.mark.integration
async def test_respects_batch_size_oldest_first(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    turn_at = now - timedelta(days=1)
    user = await _seed_user(db_session)
    oldest = await _seed_session(
        db_session, user_id=user.id, started_at=_LONG_AGO, turns_at=(turn_at,)
    )
    middle = await _seed_session(
        db_session,
        user_id=user.id,
        started_at=_LONG_AGO + timedelta(days=1),
        turns_at=(turn_at,),
    )
    newest = await _seed_session(
        db_session,
        user_id=user.id,
        started_at=_LONG_AGO + timedelta(days=2),
        turns_at=(turn_at,),
    )

    with patch(_DELAY) as mock_delay:
        closed = await _async_close_idle_sessions(session=db_session, now=now, batch_size=2)

    for cs in (oldest, middle, newest):
        await db_session.refresh(cs)

    assert closed == 2
    assert oldest.ended_at == turn_at
    assert middle.ended_at == turn_at
    assert newest.ended_at is None
    assert _enqueued_ids(mock_delay) == {str(oldest.id), str(middle.id)}


@pytest.mark.integration
async def test_broker_down_keeps_sessions_closed(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    turn_at = now - timedelta(days=1)
    user = await _seed_user(db_session)
    cs = await _seed_session(db_session, user_id=user.id, turns_at=(turn_at,))

    with (
        patch(_DELAY, side_effect=ConnectionError("broker caido")),
        patch("app.workflows.session_idle_close.logger.warning") as mock_warn,
    ):
        closed = await _async_close_idle_sessions(session=db_session, now=now, batch_size=1)

    await db_session.refresh(cs)
    assert closed == 1
    assert cs.ended_at == turn_at
    mock_warn.assert_called_once()
    # regla #4: solo el tipo de error, nunca el mensaje.
    assert "broker caido" not in str(mock_warn.call_args)


@pytest.mark.integration
async def test_nothing_idle_returns_zero(db_session: AsyncSession) -> None:
    # ``now`` en el año 2000: ninguna sesión (ni sembrada ni de otro test) está inactiva.
    user = await _seed_user(db_session)
    cs = await _seed_session(db_session, user_id=user.id, started_at=_LONG_AGO)

    with patch(_DELAY) as mock_delay:
        closed = await _async_close_idle_sessions(session=db_session, now=_LONG_AGO)

    await db_session.refresh(cs)
    assert closed == 0
    assert cs.ended_at is None
    mock_delay.assert_not_called()
