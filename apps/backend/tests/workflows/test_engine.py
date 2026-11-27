"""Tests de ``app/workflows/_engine.py``.

`normalize_db_url` se testea unit (sin DB). `worker_session` se testea integración
contra la DB de tests: cubre el **path de producción** de los workers Celery (engine
NullPool efímero + commit al salir + rollback ante error), que los tests de los
workflows NO ejercen (inyectan la sesión del fixture). Era un gap real: los 4 workers
dependen de este context manager y nunca se probaba con un engine real.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.config import Settings
from app.models.conversation_turn import ConversationTurn
from app.models.user import User
from app.workflows._engine import delete_in_batches, normalize_db_url, worker_session


def _settings(db_url: str) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_URL=db_url,
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET="x" * 40,
    )


def test_normalize_db_url_converts_sync_to_asyncpg() -> None:
    assert normalize_db_url("postgresql://u:p@h/d") == "postgresql+asyncpg://u:p@h/d"


def test_normalize_db_url_leaves_asyncpg_intact() -> None:
    assert normalize_db_url("postgresql+asyncpg://u:p@h/d") == "postgresql+asyncpg://u:p@h/d"


async def test_delete_in_batches_reports_each_batch_after_its_commit() -> None:
    """``on_batch`` llega después del commit de su lote: el conteo nunca sobreestima.

    Sesión mockeada, sin DB: lo que importa es el orden de las llamadas.
    """
    events: list[str] = []
    session = AsyncMock()
    session.execute.side_effect = [SimpleNamespace(rowcount=2), SimpleNamespace(rowcount=1)]
    session.commit.side_effect = lambda: events.append("commit")

    total = await delete_in_batches(
        session,
        ConversationTurn,
        ConversationTurn.seq >= 0,
        batch_size=2,
        commit=True,
        on_batch=lambda deleted: events.append(f"batch:{deleted}"),
    )

    assert total == 3
    assert events == ["commit", "batch:2", "commit", "batch:1"]


@pytest.mark.integration
async def test_worker_session_commits_on_clean_exit(db_url: str) -> None:
    """Al salir limpio del bloque, ``worker_session`` commitea (no solo flush)."""
    settings = _settings(db_url)
    async with worker_session(settings) as session:
        user = User()
        session.add(user)
        await session.flush()
        uid = user.id

    # Engine NUEVO: si la fila se ve desde otra sesión, el CM commiteó de verdad.
    async with worker_session(settings) as verify:
        found = await verify.get(User, uid)
        assert found is not None
        await verify.delete(found)  # cleanup, commiteado al salir

    async with worker_session(settings) as confirm:
        assert await confirm.get(User, uid) is None


@pytest.mark.integration
async def test_worker_session_rolls_back_on_error(db_url: str) -> None:
    """Si el bloque levanta, NO se commitea (la sesión hace rollback)."""
    settings = _settings(db_url)
    uid = uuid.uuid4()
    with pytest.raises(RuntimeError, match="boom"):
        async with worker_session(settings) as session:
            session.add(User(id=uid))
            await session.flush()
            raise RuntimeError("boom")

    async with worker_session(settings) as verify:
        assert await verify.get(User, uid) is None
