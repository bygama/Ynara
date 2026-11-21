"""Cierre de los clientes que arma cada intento de consolidacion (UNIT).

Cada intento corre en su propio event loop (``asyncio.run``) y arma clientes nuevos
(LLM, embeddings, reranker): al terminar, ande o no, cierra los que armo. Los
inyectados son del caller y no se tocan. Sin DB ni red: los builders devuelven fakes
con un ``aclose`` espiable y ``worker_session`` entrega una sesion mock.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.reranker import FakeReranker
from app.llm.errors import LlmRateLimitError, LlmTransientError
from app.llm.schemas import CompletionResult
from app.workflows import consolidation
from app.workflows._turn_marker import TurnClaim
from app.workflows.consolidation import _async_consolidate, _async_consolidate_session

USER_ID = str(uuid4())
SESSION_ID = str(uuid4())
_OPS = json.dumps([{"op": "ADD", "layer": "semantic", "content": "El usuario vive en Rosario."}])


@dataclass
class _Clients:
    """El trio de clientes de un intento, cada uno con su ``aclose`` espiable."""

    llm: FakeLlmClient
    embedder: FakeEmbeddingClient
    reranker: FakeReranker
    llm_aclose: AsyncMock
    embedder_aclose: AsyncMock
    reranker_aclose: AsyncMock

    @property
    def closers(self) -> tuple[AsyncMock, AsyncMock, AsyncMock]:
        return (self.llm_aclose, self.embedder_aclose, self.reranker_aclose)


def _clients() -> _Clients:
    clients = _Clients(
        llm=FakeLlmClient(models=frozenset({"agente"})),
        embedder=FakeEmbeddingClient(),
        reranker=FakeReranker(),
        llm_aclose=AsyncMock(),
        embedder_aclose=AsyncMock(),
        reranker_aclose=AsyncMock(),
    )
    # El embedder y el reranker fake no tienen ``aclose``; los reales por API si.
    for client, aclose in zip(
        (clients.llm, clients.embedder, clients.reranker), clients.closers, strict=True
    ):
        client.aclose = aclose  # type: ignore[union-attr, method-assign]
    return clients


def _result(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=20,
        model_name="agente",
        latency_ms=1.0,
    )


@asynccontextmanager
async def _mock_worker_session(_settings: Settings) -> AsyncIterator[AsyncSession]:
    yield MagicMock(spec=AsyncSession)


@pytest.fixture
def built() -> Iterator[_Clients]:
    """Los clientes que arma la task: los builders los devuelven (sin DB ni red)."""
    clients = _clients()
    semantic = AsyncMock()
    semantic.add = AsyncMock(return_value=MagicMock(id=uuid4()))
    with (
        patch.object(consolidation, "_build_consolidation_llm", return_value=clients.llm),
        patch.object(consolidation, "_build_embedder", return_value=clients.embedder),
        patch.object(consolidation, "_build_reranker", return_value=clients.reranker),
        patch.object(consolidation, "worker_session", _mock_worker_session),
        patch.object(
            consolidation,
            "load_turn_pair",
            AsyncMock(return_value=("vivo en Rosario", "anotado")),
        ),
        patch.object(consolidation, "claim_turn", AsyncMock(return_value=TurnClaim.CLAIMED)),
        patch.object(consolidation, "SemanticMemoryStore", return_value=semantic),
        patch.object(consolidation, "ProceduralMemoryStore", return_value=AsyncMock()),
        patch.object(consolidation, "AuditStore", return_value=AsyncMock()),
    ):
        yield clients


async def _consolidate_turn(**overrides: object) -> int:
    kwargs: dict[str, object] = {
        "user_id": USER_ID,
        "session_id": SESSION_ID,
        "mode": "vida",
        "turn_seq": 0,
    }
    kwargs.update(overrides)
    return await _async_consolidate(**kwargs)  # type: ignore[arg-type]


async def _consolidate_session(session_id: str = SESSION_ID) -> int:
    return await _async_consolidate_session(user_id=USER_ID, session_id=session_id, mode="vida")


# ---------------------------------------------------------------------------
# consolidate_turn
# ---------------------------------------------------------------------------


async def test_turn_closes_the_clients_it_built(built: _Clients) -> None:
    built.llm.queue_result(_result(_OPS))

    assert await _consolidate_turn() == 1

    for aclose in built.closers:
        aclose.assert_awaited_once()


async def test_turn_closes_the_clients_when_the_attempt_fails(built: _Clients) -> None:
    """Un 429 corta el intento (y la task lo reintenta): los clientes se cierran igual."""
    built.llm.queue_error(LlmRateLimitError("HTTP 429"))

    with pytest.raises(LlmTransientError):
        await _consolidate_turn()

    for aclose in built.closers:
        aclose.assert_awaited_once()


async def test_turn_closes_only_the_clients_it_built(built: _Clients) -> None:
    """Un cliente inyectado es del caller: se cierran solo los que armo la task."""
    injected = _clients()
    injected.llm.queue_result(_result(_OPS))

    assert await _consolidate_turn(llm_client=injected.llm) == 1

    injected.llm_aclose.assert_not_awaited()
    built.llm_aclose.assert_not_awaited()
    built.embedder_aclose.assert_awaited_once()
    built.reranker_aclose.assert_awaited_once()


async def test_a_failing_close_does_not_change_the_result(
    built: _Clients, caplog: pytest.LogCaptureFixture
) -> None:
    """Si un cliente no cierra, el intento vale igual y los demas se cierran."""
    built.llm.queue_result(_result(_OPS))
    built.embedder_aclose.side_effect = RuntimeError("socket roto")

    with caplog.at_level(logging.WARNING, logger="app.workflows.consolidation"):
        assert await _consolidate_turn() == 1

    built.llm_aclose.assert_awaited_once()
    built.reranker_aclose.assert_awaited_once()
    assert "consolidation: no se pudo cerrar un cliente: RuntimeError" in caplog.messages


# ---------------------------------------------------------------------------
# consolidate_session
# ---------------------------------------------------------------------------


async def test_session_closes_the_clients_it_built(built: _Clients) -> None:
    with patch.object(consolidation, "_consolidate_session_in_db", AsyncMock(return_value=1)):
        assert await _consolidate_session() == 1

    for aclose in built.closers:
        aclose.assert_awaited_once()


async def test_session_closes_the_clients_when_the_attempt_fails(built: _Clients) -> None:
    body = AsyncMock(side_effect=LlmTransientError("HTTP 503"))

    with (
        patch.object(consolidation, "_consolidate_session_in_db", body),
        pytest.raises(LlmTransientError),
    ):
        await _consolidate_session()

    for aclose in built.closers:
        aclose.assert_awaited_once()


async def test_session_with_a_garbage_id_builds_no_client(built: _Clients) -> None:
    """Sin un session_id valido no hay episodio posible: no se arma ni se cierra nada."""
    with patch.object(consolidation, "_build_consolidation_llm") as build_llm:
        assert await _consolidate_session(session_id="no-es-un-uuid") == 0

    build_llm.assert_not_called()
    for aclose in built.closers:
        aclose.assert_not_awaited()
