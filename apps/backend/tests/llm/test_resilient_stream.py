"""Respaldo del ``ResilientClient`` en ``stream`` antes del primer chunk (ADR-028).

Complementa los tests de streaming de ``test_resilient.py``. Si un candidato falla antes
de emitir nada (caída, credencial rechazada, 4xx propio, 429), el stream pasa al
siguiente; un error del request se propaga sin probar el respaldo, un corte después del
primer chunk se propaga tal cual y, si fallan todos, sale el error del último. Cada
candidato que se prueba resuelve su breaker, y ``allow()`` no se le pide a uno que no
se usa (no se le consume la prueba HALF_OPEN).

Mismos dobles que el resto de los tests del cliente resiliente: ``FakeLlmClient``
programable y un reloj fake para el breaker.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import pytest

from app.llm.clients.circuit import CircuitBreaker, CircuitState
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import ClientPool, FirstHealthy
from app.llm.clients.resilient import ResilientClient
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from app.llm.schemas import ChatMessage, CompletionChunk, ModelHealth

_MODEL = "agente"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _NamedFake(FakeLlmClient):
    """``FakeLlmClient`` con ``name``, como ``OpenAICompatClient``."""

    def __init__(self, name: str) -> None:
        super().__init__(models=frozenset({_MODEL}))
        self.name = name


class _FailsMidStream:
    """Cliente cuyo stream emite un chunk y después se corta."""

    def __init__(self) -> None:
        self.stream_calls = 0

    def serves_model(self, model: str) -> bool:
        return model == _MODEL

    async def stream(self, **_kwargs: object) -> AsyncIterator[CompletionChunk]:
        self.stream_calls += 1
        yield CompletionChunk(delta_text="Hola")
        raise LlmUnavailableError("transport error")

    async def health(self) -> ModelHealth:
        return ModelHealth(model_name=_MODEL, healthy=True)


def _fake() -> FakeLlmClient:
    return FakeLlmClient(models=frozenset({_MODEL}))


def _resilient(clients: list[object], *, breaker_clock: _Clock | None = None) -> ResilientClient:
    return ResilientClient(
        ClientPool(list(clients), FirstHealthy()),  # type: ignore[arg-type]
        breaker_factory=lambda: CircuitBreaker(
            failure_threshold=1, recovery_timeout_s=30.0, clock=breaker_clock or _Clock()
        ),
    )


def _state(resilient: ResilientClient, client: object) -> CircuitState:
    return resilient._breakers[id(client)].state


async def _stream_text(resilient: ResilientClient) -> str:
    messages = [ChatMessage(role="user", content="x")]
    chunks = [c async for c in resilient.stream(model=_MODEL, messages=messages)]
    return "".join(c.delta_text for c in chunks)


# ---------- respaldo antes del primer chunk ----------


@pytest.mark.parametrize(
    ("error", "primary_state"),
    [
        (LlmTimeoutError("timeout HTTP"), CircuitState.OPEN),
        (LlmUnavailableError("HTTP 503"), CircuitState.OPEN),
        (LlmAuthError("HTTP 401"), CircuitState.CLOSED),
        (LlmProviderRejectedError("HTTP 404"), CircuitState.CLOSED),
        (LlmRateLimitError("HTTP 429", retry_after_s=2.0), CircuitState.CLOSED),
    ],
)
async def test_stream_uses_secondary_when_primary_fails_before_first_chunk(
    error: LlmError, primary_state: CircuitState
) -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_stream_error(error)
    secondary.queue_chunks([CompletionChunk(delta_text="desde el respaldo")])
    resilient = _resilient([primary, secondary])

    assert await _stream_text(resilient) == "desde el respaldo"
    assert len(primary.stream_calls) == 1  # sin reintentos contra el mismo
    assert _state(resilient, primary) is primary_state  # solo una caída lo abre
    assert _state(resilient, secondary) is CircuitState.CLOSED


async def test_stream_failover_is_logged_without_content(caplog: pytest.LogCaptureFixture) -> None:
    primary, secondary = _NamedFake("principal"), _fake()
    primary.queue_stream_error(LlmUnavailableError("HTTP 503"))
    secondary.queue_chunks([CompletionChunk(delta_text="ok")])
    resilient = _resilient([primary, secondary])

    with caplog.at_level(logging.WARNING, logger="app.llm.clients.resilient"):
        assert await _stream_text(resilient) == "ok"

    assert "llm: candidato principal descartado (LlmUnavailableError)" in caplog.messages


async def test_stream_request_error_is_not_sent_to_secondary() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_stream_error(LlmBadRequestError("HTTP 400"))
    resilient = _resilient([primary, secondary])

    with pytest.raises(LlmBadRequestError):
        await _stream_text(resilient)

    assert secondary.stream_calls == []
    assert _state(resilient, primary) is CircuitState.CLOSED


async def test_stream_raises_last_error_when_every_candidate_fails() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_stream_error(LlmUnavailableError("HTTP 503"))
    secondary.queue_stream_error(LlmAuthError("HTTP 401"))
    resilient = _resilient([primary, secondary])

    with pytest.raises(LlmAuthError):
        await _stream_text(resilient)

    assert _state(resilient, primary) is CircuitState.OPEN
    assert _state(resilient, secondary) is CircuitState.CLOSED


# ---------- después del primer chunk no hay respaldo ----------


async def test_stream_cut_after_first_chunk_is_raised_without_secondary() -> None:
    primary, secondary = _FailsMidStream(), _fake()
    resilient = _resilient([primary, secondary])
    received: list[str] = []

    with pytest.raises(LlmUnavailableError):
        async for chunk in resilient.stream(
            model=_MODEL, messages=[ChatMessage(role="user", content="x")]
        ):
            received.append(chunk.delta_text)

    assert received == ["Hola"]
    assert secondary.stream_calls == []
    assert _state(resilient, primary) is CircuitState.OPEN


# ---------- breakers de los candidatos que no se usan ----------


async def test_stream_does_not_take_the_probe_of_an_unused_secondary() -> None:
    primary, secondary = _fake(), _fake()
    breaker_clock = _Clock()
    resilient = _resilient([primary, secondary], breaker_clock=breaker_clock)
    resilient._breakers[id(secondary)].record_failure()  # umbral 1: abre
    breaker_clock.now = 30.0  # ventana de recovery: el respaldo admitiría una prueba
    primary.queue_chunks([CompletionChunk(delta_text="principal")])

    assert await _stream_text(resilient) == "principal"
    assert _state(resilient, secondary) is CircuitState.OPEN  # la prueba sigue libre
    assert resilient._breakers[id(secondary)].would_allow()


async def test_stream_does_not_retry_open_primary_after_secondary_fails() -> None:
    """El best-effort es solo para cuando ningún candidato está disponible."""
    primary, secondary = _fake(), _fake()
    resilient = _resilient([primary, secondary])
    resilient._breakers[id(primary)].record_failure()  # umbral 1: abre
    secondary.queue_stream_error(LlmUnavailableError("HTTP 503"))

    with pytest.raises(LlmUnavailableError):
        await _stream_text(resilient)

    assert primary.stream_calls == []
