"""Tests del worker de retención de turnos crudos (``purge_stale_turns``).

Dos niveles:

- **Unit (sin DB)**: la ventana es de 8 días, el task wrapper corre el cuerpo async y
  es fail-open, y el beat lo agenda una vez por día.
- **Integración** (``integration``): siembra sesiones con turnos y, con ``now``
  inyectado, verifica que borra SOLO los turnos de sesiones cerradas hace más de la
  ventana; que respeta el borde exacto, las cerradas dentro de la ventana y las
  abiertas, y que el borrado por lotes converge.

Las fechas sembradas son del año 2000 y ``now`` se inyecta cerca de esa fecha, así el
scan GLOBAL difícilmente alcanza filas que otro test haya dejado commiteadas. Igual, los
conteos globales se asiertan con ``>=`` y lo que importa se chequea por sesión. El
``content`` es ``BYTEA`` opaco sintético: el worker nunca lo lee.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Mode, TurnRole
from app.models.conversation_turn import ConversationTurn
from app.models.session import ChatSession
from app.models.user import User
from app.services.session_idle import CONSOLIDATION_MAX_AGE
from app.workflows.turn_retention import (
    TURN_RETENTION,
    _async_purge_stale_turns,
    purge_stale_turns,
)

_NOW = datetime(2000, 3, 1, 12, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Unit (sin DB)
# ---------------------------------------------------------------------------


def test_retention_is_consolidation_limit_plus_one_day() -> None:
    assert TURN_RETENTION == CONSOLIDATION_MAX_AGE + timedelta(days=1)
    assert TURN_RETENTION == timedelta(days=8)


class TestPurgeStaleTurnsTaskWrapper:
    """``purge_stale_turns`` corre el cuerpo async y es fail-open."""

    def test_success_runs_async_and_returns_none(self) -> None:
        with patch(
            "app.workflows.turn_retention._async_purge_stale_turns", new_callable=AsyncMock
        ) as mock_async:
            mock_async.return_value = 3
            assert purge_stale_turns() is None
            mock_async.assert_awaited_once()

    def test_does_not_propagate_if_async_raises(self) -> None:
        with (
            patch(
                "app.workflows.turn_retention._async_purge_stale_turns", new_callable=AsyncMock
            ) as mock_async,
            patch("app.workflows.turn_retention.logger.error") as mock_log,
        ):
            mock_async.side_effect = RuntimeError("DB caida")
            assert purge_stale_turns() is None
            # logger.error (NO .exception): regla #4, sin traceback con posible contenido.
            mock_log.assert_called_once()
            # Solo el tipo de error: el mensaje de la excepción no llega al log.
            assert "RuntimeError" in mock_log.call_args.args
            assert "DB caida" not in str(mock_log.call_args)

    def test_logs_batches_committed_before_a_cut(self) -> None:
        """Un corte a mitad de camino loguea cuántos turnos alcanzó a borrar."""

        async def _cut_after_two_batches(*, on_batch: Callable[[int], None]) -> int:
            on_batch(1000)
            on_batch(1000)
            raise SoftTimeLimitExceeded()

        with (
            patch(
                "app.workflows.turn_retention._async_purge_stale_turns",
                side_effect=_cut_after_two_batches,
            ),
            patch("app.workflows.turn_retention.logger.error") as mock_log,
        ):
            assert purge_stale_turns() is None
            mock_log.assert_called_once()
            assert mock_log.call_args.args[1:] == ("SoftTimeLimitExceeded", 2000)


def test_beat_schedule_runs_daily() -> None:
    from app.workers.celery_app import celery_app

    entry = celery_app.conf.beat_schedule["purge-stale-turns-daily"]
    assert entry["task"] == "workflows.purge_stale_turns"
    assert entry["schedule"] == timedelta(days=1)


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
    ended_at: datetime | None,
    turns: int = 2,
) -> ChatSession:
    started_at = _NOW - timedelta(days=60)
    cs = ChatSession(user_id=user_id, mode=Mode.VIDA, started_at=started_at, ended_at=ended_at)
    session.add(cs)
    await session.flush()
    for seq in range(turns):
        session.add(
            ConversationTurn(
                user_id=user_id,
                session_id=cs.id,
                role=TurnRole.USER if seq % 2 == 0 else TurnRole.MODEL,
                content=b"sintetico",
                seq=seq,
                created_at=started_at,
            )
        )
    await session.flush()
    return cs


async def _turns_of(session: AsyncSession, cs: ChatSession) -> int:
    stmt = (
        select(func.count())
        .select_from(ConversationTurn)
        .where(ConversationTurn.session_id == cs.id)
    )
    return (await session.execute(stmt)).scalar_one()


@pytest.mark.integration
async def test_purges_only_sessions_closed_past_retention(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    stale = await _seed_session(
        db_session, user_id=user.id, ended_at=_NOW - TURN_RETENTION - timedelta(minutes=1)
    )
    # En el borde exacto: el predicado es estricto, queda.
    edge = await _seed_session(db_session, user_id=user.id, ended_at=_NOW - TURN_RETENTION)
    # Cerrada hace 7 días y medio: pasó el límite de consolidación pero no el margen.
    in_grace = await _seed_session(
        db_session,
        user_id=user.id,
        ended_at=_NOW - CONSOLIDATION_MAX_AGE - timedelta(hours=12),
    )
    # Abierta con turnos viejos: la cierra el beat de inactividad, no este job.
    still_open = await _seed_session(db_session, user_id=user.id, ended_at=None)

    deleted = await _async_purge_stale_turns(session=db_session, now=_NOW)

    assert deleted >= 2
    assert await _turns_of(db_session, stale) == 0
    assert await _turns_of(db_session, edge) == 2
    assert await _turns_of(db_session, in_grace) == 2
    assert await _turns_of(db_session, still_open) == 2


@pytest.mark.integration
async def test_keeps_the_session_row(db_session: AsyncSession) -> None:
    """Borra los turnos, no la sesión: el panel de admin la sigue contando."""
    user = await _seed_user(db_session)
    stale = await _seed_session(db_session, user_id=user.id, ended_at=_NOW - timedelta(days=30))

    await _async_purge_stale_turns(session=db_session, now=_NOW)

    row = await db_session.execute(select(ChatSession.id).where(ChatSession.id == stale.id))
    assert row.scalar_one_or_none() == stale.id


@pytest.mark.integration
async def test_batches_converge(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    stale = await _seed_session(
        db_session, user_id=user.id, ended_at=_NOW - timedelta(days=30), turns=5
    )

    batches: list[int] = []
    deleted = await _async_purge_stale_turns(
        session=db_session, now=_NOW, batch_size=2, on_batch=batches.append
    )

    assert deleted >= 5
    # Cada lote reporta sus filas y la suma coincide con el total devuelto.
    assert len(batches) >= 3
    assert sum(batches) == deleted
    assert await _turns_of(db_session, stale) == 0


@pytest.mark.integration
async def test_keeps_recently_closed(db_session: AsyncSession) -> None:
    user = await _seed_user(db_session)
    recent = await _seed_session(db_session, user_id=user.id, ended_at=_NOW - timedelta(days=1))

    await _async_purge_stale_turns(session=db_session, now=_NOW)
    assert await _turns_of(db_session, recent) == 2
