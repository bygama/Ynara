"""Política del ``ResilientClient`` ante proveedores por API (ADR-028).

Complementa ``test_resilient.py`` con lo que agrega un proveedor por API: la
credencial rechazada (sin reintento, pasa al respaldo), el 429 con ``Retry-After``
medido contra el presupuesto global, qué errores abren el breaker y cuáles no, la
prueba HALF_OPEN que se cierra con cualquier respuesta del proveedor, el corte del
presupuesto que cuenta como caída y el log del candidato descartado.

Mismos dobles que ``test_resilient.py``: ``FakeLlmClient`` programable, ``sleep`` y
``clock`` fakes. ``_AdvancingSleep`` además avanza el reloj, así el presupuesto que
queda baja con cada espera como en la realidad.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.llm.clients.circuit import CircuitBreaker, CircuitState
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import ClientPool, FirstHealthy
from app.llm.clients.resilient import ResilientClient
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmContextOverflowError,
    LlmError,
    LlmOverloadedError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
    ToolParsingError,
)
from app.llm.schemas import ChatMessage, CompletionChunk, CompletionResult, ModelHealth

_MODEL = "qwen-3.5-9b"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _AdvancingSleep:
    """``sleep`` fake que registra cada espera y avanza el reloj fake."""

    def __init__(self, clock: _Clock) -> None:
        self._clock = clock
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self._clock.now += delay


class _NamedFake(FakeLlmClient):
    """``FakeLlmClient`` con ``name``, como ``OpenAICompatClient``."""

    def __init__(self, name: str) -> None:
        super().__init__(served_models=frozenset({_MODEL}))
        self.name = name


def _result(text: str = "ok") -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason="stop",
        prompt_tokens=1,
        completion_tokens=1,
        model_name=_MODEL,
        latency_ms=0.0,
    )


def _messages() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="hola, dato privado")]


def _fake() -> FakeLlmClient:
    return FakeLlmClient(served_models=frozenset({_MODEL}))


def _one_failure_breaker(clock: _Clock | None = None) -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=1, recovery_timeout_s=30.0, clock=clock or _Clock())


def _resilient(
    clients: list[FakeLlmClient],
    *,
    clock: _Clock | None = None,
    sleep: _AdvancingSleep | None = None,
    max_attempts: int = 3,
    total_budget_s: float = 90.0,
    breaker_clock: _Clock | None = None,
) -> ResilientClient:
    clock = clock or _Clock()
    return ResilientClient(
        ClientPool(list(clients), FirstHealthy()),
        max_attempts=max_attempts,
        total_budget_s=total_budget_s,
        clock=clock,
        sleep=sleep or _AdvancingSleep(clock),
        breaker_factory=lambda: _one_failure_breaker(breaker_clock),
    )


def _state(resilient: ResilientClient, client: object) -> CircuitState:
    return resilient._breakers[id(client)].state


async def _complete(resilient: ResilientClient) -> CompletionResult:
    return await resilient.complete(model=_MODEL, messages=_messages())


# ---------- credencial rechazada ----------


async def test_auth_error_is_not_retried_and_uses_secondary() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(LlmAuthError("HTTP 401"))
    secondary.queue_result(_result("desde el respaldo"))
    clock = _Clock()
    sleep = _AdvancingSleep(clock)
    resilient = _resilient([primary, secondary], clock=clock, sleep=sleep)

    result = await _complete(resilient)

    assert result.text == "desde el respaldo"
    assert len(primary.complete_calls) == 1  # sin reintentos con la misma credencial
    assert sleep.delays == []
    assert _state(resilient, primary) is CircuitState.CLOSED  # contestó: no está caído


async def test_auth_error_everywhere_degrades() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(LlmAuthError("HTTP 401"))
    secondary.queue_error(LlmAuthError("HTTP 403"))
    resilient = _resilient([primary, secondary])

    result = await _complete(resilient)

    assert result.finish_reason == "degraded"
    assert (len(primary.complete_calls), len(secondary.complete_calls)) == (1, 1)


# ---------- 429 con Retry-After contra el presupuesto ----------


async def test_rate_limit_waits_retry_after_within_budget() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(LlmRateLimitError("HTTP 429", retry_after_s=7.0))
    primary.queue_result(_result("después de esperar"))
    clock = _Clock()
    sleep = _AdvancingSleep(clock)
    resilient = _resilient([primary, secondary], clock=clock, sleep=sleep)

    result = await _complete(resilient)

    assert result.text == "después de esperar"
    assert sleep.delays == [7.0]
    assert secondary.complete_calls == []


async def test_rate_limit_waits_while_budget_lasts_then_uses_secondary() -> None:
    """40 s entran dos veces en 90 s; la tercera vez quedan 10 y se pasa al respaldo."""
    primary, secondary = _fake(), _fake()
    for _ in range(3):
        primary.queue_error(LlmRateLimitError("HTTP 429", retry_after_s=40.0))
    secondary.queue_result(_result("desde el respaldo"))
    clock = _Clock()
    sleep = _AdvancingSleep(clock)
    resilient = _resilient([primary, secondary], clock=clock, sleep=sleep, max_attempts=4)

    result = await _complete(resilient)

    assert result.text == "desde el respaldo"
    assert sleep.delays == [40.0, 40.0]
    assert len(primary.complete_calls) == 3


async def test_rate_limit_beyond_budget_goes_straight_to_secondary() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(LlmRateLimitError("HTTP 429", retry_after_s=120.0))
    secondary.queue_result(_result("desde el respaldo"))
    clock = _Clock()
    sleep = _AdvancingSleep(clock)
    resilient = _resilient([primary, secondary], clock=clock, sleep=sleep)

    result = await _complete(resilient)

    assert result.text == "desde el respaldo"
    assert sleep.delays == []
    assert len(primary.complete_calls) == 1


async def test_rate_limit_on_last_attempt_does_not_wait() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(LlmRateLimitError("HTTP 429", retry_after_s=5.0))
    primary.queue_error(LlmRateLimitError("HTTP 429", retry_after_s=5.0))
    secondary.queue_result(_result("desde el respaldo"))
    clock = _Clock()
    sleep = _AdvancingSleep(clock)
    resilient = _resilient([primary, secondary], clock=clock, sleep=sleep, max_attempts=2)

    result = await _complete(resilient)

    assert result.text == "desde el respaldo"
    assert sleep.delays == [5.0]


@pytest.mark.parametrize(
    "error",
    [LlmRateLimitError("HTTP 429", retry_after_s=None), LlmOverloadedError("HTTP 429")],
)
async def test_overload_without_retry_after_uses_backoff(error: LlmError) -> None:
    primary = _fake()
    primary.queue_error(error)
    primary.queue_result(_result("recuperado"))
    clock = _Clock()
    sleep = _AdvancingSleep(clock)
    resilient = _resilient([primary], clock=clock, sleep=sleep)

    result = await _complete(resilient)

    assert result.text == "recuperado"
    assert len(sleep.delays) == 1
    assert 0.5 <= sleep.delays[0] <= 1.0  # backoff del primer intento + jitter


# ---------- qué abre el breaker ----------


@pytest.mark.parametrize(
    "error", [LlmTimeoutError("timeout HTTP"), LlmUnavailableError("HTTP 503")]
)
async def test_outage_opens_breaker(error: LlmError) -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(error)
    secondary.queue_result(_result())
    resilient = _resilient([primary, secondary], max_attempts=1)

    await _complete(resilient)

    assert _state(resilient, primary) is CircuitState.OPEN


@pytest.mark.parametrize(
    "error",
    [
        LlmAuthError("HTTP 401"),
        LlmRateLimitError("HTTP 429", retry_after_s=500.0),
        LlmOverloadedError("HTTP 429"),
        LlmError("respuesta sin choices"),
        ToolParsingError(),
    ],
)
async def test_provider_answer_does_not_open_breaker(error: LlmError) -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(error)
    secondary.queue_result(_result("desde el respaldo"))
    resilient = _resilient([primary, secondary], max_attempts=1)

    result = await _complete(resilient)

    assert result.text == "desde el respaldo"
    assert _state(resilient, primary) is CircuitState.CLOSED


@pytest.mark.parametrize("error", [LlmBadRequestError("HTTP 400"), LlmContextOverflowError()])
async def test_request_error_is_reraised_without_opening_breaker(error: LlmError) -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(error)
    resilient = _resilient([primary, secondary])

    with pytest.raises(type(error)):
        await _complete(resilient)

    assert secondary.complete_calls == []
    assert _state(resilient, primary) is CircuitState.CLOSED


async def test_any_outage_among_attempts_opens_breaker() -> None:
    """Un 429 al final no tapa los timeouts de los intentos anteriores."""
    primary, secondary = _fake(), _fake()
    primary.queue_error(LlmTimeoutError("timeout HTTP"))
    primary.queue_error(LlmTimeoutError("timeout HTTP"))
    primary.queue_error(LlmRateLimitError("HTTP 429", retry_after_s=None))
    secondary.queue_result(_result())
    resilient = _resilient([primary, secondary], max_attempts=3)

    await _complete(resilient)

    assert _state(resilient, primary) is CircuitState.OPEN


# ---------- la prueba HALF_OPEN siempre se cierra ----------


@pytest.mark.parametrize(
    "error",
    [
        LlmAuthError("HTTP 401"),
        LlmRateLimitError("HTTP 429", retry_after_s=500.0),
        LlmBadRequestError("HTTP 400"),
    ],
)
async def test_half_open_probe_is_closed_by_any_provider_answer(error: LlmError) -> None:
    primary = _fake()
    breaker_clock = _Clock()
    resilient = _resilient([primary], max_attempts=1, breaker_clock=breaker_clock)
    primary.queue_error(LlmTimeoutError("timeout HTTP"))
    await _complete(resilient)  # abre el breaker
    assert _state(resilient, primary) is CircuitState.OPEN

    breaker_clock.now = 30.0  # ventana de recovery: entra una prueba
    primary.queue_error(error)
    if isinstance(error, LlmBadRequestError):
        with pytest.raises(LlmBadRequestError):
            await _complete(resilient)
    else:
        assert (await _complete(resilient)).finish_reason == "degraded"

    assert _state(resilient, primary) is CircuitState.CLOSED  # la prueba no quedó colgada
    primary.queue_result(_result("de vuelta"))
    assert (await _complete(resilient)).text == "de vuelta"


async def test_stream_auth_error_closes_half_open_probe() -> None:
    primary = _fake()
    breaker_clock = _Clock()
    resilient = _resilient([primary], max_attempts=1, breaker_clock=breaker_clock)
    primary.queue_error(LlmTimeoutError("timeout HTTP"))
    await _complete(resilient)  # abre el breaker
    breaker_clock.now = 30.0

    primary.queue_stream_error(LlmAuthError("HTTP 401"))
    with pytest.raises(LlmAuthError):
        async for _ in resilient.stream(model=_MODEL, messages=_messages()):
            pass

    assert _state(resilient, primary) is CircuitState.CLOSED


async def test_stream_success_still_closes_breaker() -> None:
    primary = _fake()
    primary.queue_chunks([CompletionChunk(delta_text="hola")])
    resilient = _resilient([primary])
    chunks = [c async for c in resilient.stream(model=_MODEL, messages=_messages())]
    assert "".join(c.delta_text for c in chunks) == "hola"
    assert _state(resilient, primary) is CircuitState.CLOSED


# ---------- el presupuesto que corta un request cuenta como caída ----------


class _HangingClient:
    """Cliente cuyo ``complete`` no responde dentro del presupuesto."""

    def __init__(self) -> None:
        self.complete_calls = 0

    def serves_model(self, model: str) -> bool:
        return model == _MODEL

    async def complete(self, **_kwargs: object) -> CompletionResult:
        self.complete_calls += 1
        await asyncio.sleep(5.0)
        return _result("tarde")

    async def health(self) -> ModelHealth:
        return ModelHealth(model_name=_MODEL, healthy=True)


async def test_budget_cut_counts_as_outage() -> None:
    hanging, secondary = _HangingClient(), _fake()
    resilient = _resilient([hanging, secondary], total_budget_s=0.05)  # type: ignore[list-item]

    assert (await _complete(resilient)).finish_reason == "degraded"
    assert _state(resilient, hanging) is CircuitState.OPEN

    secondary.queue_result(_result("desde el respaldo"))
    assert (await _complete(resilient)).text == "desde el respaldo"
    assert hanging.complete_calls == 1  # breaker abierto: se saltea


async def test_budget_cut_reopens_half_open_probe() -> None:
    """Sin resolverla, la prueba quedaría colgada y el breaker no volvería a probar."""
    hanging = _HangingClient()
    breaker_clock = _Clock()
    resilient = _resilient(
        [hanging],  # type: ignore[list-item]
        total_budget_s=0.05,
        breaker_clock=breaker_clock,
    )
    await _complete(resilient)  # abre el breaker
    breaker_clock.now = 30.0
    await _complete(resilient)  # la prueba también se corta

    assert _state(resilient, hanging) is CircuitState.OPEN
    breaker_clock.now = 60.0
    assert resilient._breakers[id(hanging)].allow()  # vuelve a admitir una prueba


# ---------- log del candidato descartado ----------


async def test_discarded_candidate_is_logged_without_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    primary, secondary = _NamedFake("principal"), _fake()
    primary.queue_error(LlmAuthError("HTTP 401"))
    secondary.queue_error(LlmUnavailableError("HTTP 503"))
    resilient = _resilient([primary, secondary], max_attempts=1)

    with caplog.at_level(logging.WARNING, logger="app.llm.clients.resilient"):
        await _complete(resilient)

    messages = [record.getMessage() for record in caplog.records]
    assert "llm: candidato principal descartado (LlmAuthError)" in messages
    assert "llm: candidato FakeLlmClient descartado (LlmUnavailableError)" in messages
    assert "llm: sin candidatos disponibles, respuesta degradada" in messages
    assert "dato privado" not in caplog.text


def test_max_attempts_must_be_positive() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        ResilientClient(ClientPool([_fake()], FirstHealthy()), max_attempts=0)
