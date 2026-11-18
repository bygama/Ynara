"""Fallas transitorias del proveedor en el ``MemoryEngine`` y ``apply_ops`` (UNIT).

Una falla transitoria (``LlmTransientError``: 429, 5xx, timeout, breaker abierto o una
respuesta degradada) se propaga, asi el worker de consolidacion reintenta el turno en vez
de perderlo como si no hubiera nada que guardar. Lo permanente (credencial rechazada,
request invalido) se sigue descartando con un log que lleva solo el tipo (regla #4a).
Sin DB ni red: ``FakeLlmClient`` programable y stores mockeados.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import ClientPool, FirstHealthy
from app.llm.clients.resilient import ResilientClient
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmTransientError,
    degraded_response,
)
from app.llm.memory_engine import AgentMemoryEngine, MemoryOp, SessionSummary, apply_ops

_SECRET = "mi diagnostico es privado"


def _llm() -> FakeLlmClient:
    return FakeLlmClient(models=frozenset({"agente"}))


def _engine(client: object) -> AgentMemoryEngine:
    return AgentMemoryEngine(llm_client=client, model="agente")  # type: ignore[arg-type]


async def _consolidate(engine: AgentMemoryEngine) -> list[MemoryOp]:
    return await engine.consolidate(user_msg=_SECRET, model_response="anotado", mode="vida")


async def _summarize(engine: AgentMemoryEngine) -> SessionSummary:
    return await engine.summarize(transcript=f"Usuario: {_SECRET}", mode="vida")


async def _no_sleep(_delay: float) -> None:
    return None


def _worker_client(fake: FakeLlmClient) -> ResilientClient:
    """El ``ResilientClient`` como lo arma el worker: sin degradar, sin dormir de verdad."""
    return ResilientClient(ClientPool([fake], FirstHealthy()), degrade=False, sleep=_no_sleep)


# ---------------------------------------------------------------------------
# consolidate
# ---------------------------------------------------------------------------


async def test_consolidate_propagates_a_transient_error() -> None:
    client = _llm()
    client.queue_error(LlmRateLimitError("HTTP 429"))

    with pytest.raises(LlmTransientError):
        await _consolidate(_engine(client))


async def test_consolidate_raises_transient_on_a_degraded_response() -> None:
    """La respuesta degradada no es "nada que guardar": es una falla transitoria."""
    client = _llm()
    client.queue_result(degraded_response(text="probá en un ratito", model_name="agente"))

    with pytest.raises(LlmTransientError):
        await _consolidate(_engine(client))


async def test_consolidate_discards_a_permanent_error_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _llm()
    client.queue_error(LlmAuthError("HTTP 401"))

    with caplog.at_level(logging.WARNING, logger="app.llm.memory_engine"):
        ops = await _consolidate(_engine(client))

    assert ops == []
    assert "memory_engine: extraccion descartada (LlmAuthError)" in caplog.messages
    assert _SECRET not in caplog.text


async def test_consolidate_discards_a_request_error() -> None:
    client = _llm()
    client.queue_error(LlmBadRequestError("HTTP 400"))

    assert await _consolidate(_engine(client)) == []


async def test_consolidate_with_the_worker_client_raises_on_a_sustained_429() -> None:
    """429 en todos los intentos del cliente del worker: transitorio, se reintenta despues."""
    client = _llm()
    for _ in range(3):
        client.queue_error(LlmRateLimitError("HTTP 429"))

    with pytest.raises(LlmTransientError):
        await _consolidate(_engine(_worker_client(client)))

    assert len(client.complete_calls) == 3


async def test_consolidate_with_the_worker_client_discards_a_401_without_retrying() -> None:
    """Credencial rechazada con el cliente del worker: permanente, una sola llamada."""
    client = _llm()
    client.queue_error(LlmAuthError("HTTP 401"))

    assert await _consolidate(_engine(_worker_client(client))) == []
    assert len(client.complete_calls) == 1


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


async def test_summarize_propagates_a_transient_error() -> None:
    client = _llm()
    client.queue_error(LlmTimeoutError("timeout HTTP"))

    with pytest.raises(LlmTransientError):
        await _summarize(_engine(client))


async def test_summarize_raises_transient_on_a_degraded_response() -> None:
    client = _llm()
    client.queue_result(degraded_response(text="probá en un ratito", model_name="agente"))

    with pytest.raises(LlmTransientError):
        await _summarize(_engine(client))


async def test_summarize_discards_a_permanent_error_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client = _llm()
    client.queue_error(LlmAuthError("HTTP 401"))

    with caplog.at_level(logging.WARNING, logger="app.llm.memory_engine"):
        summary = await _summarize(_engine(client))

    assert summary == SessionSummary()
    assert "memory_engine: resumen descartado (LlmAuthError)" in caplog.messages
    assert _SECRET not in caplog.text


# ---------------------------------------------------------------------------
# apply_ops
# ---------------------------------------------------------------------------


def _semantic_add(content: str) -> MemoryOp:
    return MemoryOp(op="ADD", layer="semantic", content=content)


async def test_apply_ops_propagates_a_transient_embedder_error() -> None:
    """Un 429/timeout del embedder corta el turno entero: saltear la op perdia el hecho."""
    semantic = AsyncMock()
    semantic.add = AsyncMock(side_effect=[MagicMock(id=uuid4()), LlmTimeoutError("timeout")])
    procedural = AsyncMock()
    ops = [
        _semantic_add("hecho uno"),
        _semantic_add("hecho dos"),
        MemoryOp(op="ADD", layer="procedural", key="idioma", value={"lang": "es-AR"}),
    ]

    with pytest.raises(LlmTransientError):
        await apply_ops(ops, semantic_store=semantic, procedural_store=procedural)

    assert semantic.add.await_count == 2
    procedural.upsert.assert_not_awaited()


async def test_apply_ops_still_skips_a_non_transient_error() -> None:
    """Un error que no es transitorio sigue salteando solo esa op (comportamiento previo)."""
    semantic = AsyncMock()
    semantic.add = AsyncMock(side_effect=[RuntimeError("op mala"), MagicMock(id=uuid4())])

    applied = await apply_ops(
        [_semantic_add("hecho uno"), _semantic_add("hecho dos")],
        semantic_store=semantic,
        procedural_store=AsyncMock(),
    )

    assert applied == 1


async def test_apply_ops_propagates_through_the_per_op_savepoint() -> None:
    """Con sesion, el savepoint de la op se cierra y la falla igual llega al caller."""
    session = MagicMock()
    semantic = AsyncMock()
    semantic.add = AsyncMock(side_effect=LlmRateLimitError("HTTP 429"))

    with pytest.raises(LlmTransientError):
        await apply_ops(
            [_semantic_add(json.dumps({"x": 1}))],
            session=session,
            semantic_store=semantic,
            procedural_store=AsyncMock(),
        )

    session.begin_nested.assert_called_once()
