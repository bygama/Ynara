"""Qué hace el ``ResilientClient`` ante un 429 o una sobrecarga del proveedor (ADR-028).

Complementa ``test_resilient_policy.py``. Con un respaldo disponible, el principal solo
se espera si el ``Retry-After`` es corto; un 429 sin ``Retry-After`` o con una espera
larga pasa al respaldo sin esperar, y un respaldo con el breaker abierto no cuenta.
Sin respaldo se espera el ``Retry-After`` (o el backoff, si no vino), pero solo si
después queda margen en el presupuesto global para el request.

Mismos dobles que ``test_resilient_policy.py``: ``FakeLlmClient`` programable y
``_AdvancingSleep``, que avanza con cada espera el reloj fake con el que el cliente mide
el presupuesto.
"""

from __future__ import annotations

import pytest

from app.llm.clients.circuit import CircuitBreaker, CircuitState
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import ClientPool, FirstHealthy
from app.llm.clients.resilient import ResilientClient
from app.llm.errors import LlmError, LlmOverloadedError, LlmRateLimitError
from app.llm.schemas import ChatMessage, CompletionResult

_MODEL = "agente"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _AdvancingSleep:
    """``sleep`` fake que registra cada espera y avanza su reloj fake (``clock``)."""

    def __init__(self) -> None:
        self.clock = _Clock()
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        self.clock.now += delay


def _result(text: str = "ok") -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason="stop",
        prompt_tokens=1,
        completion_tokens=1,
        model_name=_MODEL,
        latency_ms=0.0,
    )


def _fake() -> FakeLlmClient:
    return FakeLlmClient(served_models=frozenset({_MODEL}))


def _rate_limit(retry_after_s: float | None) -> LlmRateLimitError:
    return LlmRateLimitError("HTTP 429", retry_after_s=retry_after_s)


def _resilient(
    clients: list[FakeLlmClient],
    sleep: _AdvancingSleep,
    *,
    breaker_clock: _Clock | None = None,
    **options: float,
) -> ResilientClient:
    """Cliente resiliente con el presupuesto por default (90 s) medido por el reloj fake."""
    return ResilientClient(
        ClientPool(list(clients), FirstHealthy()),
        clock=sleep.clock,
        sleep=sleep,
        breaker_factory=lambda: CircuitBreaker(
            failure_threshold=1, recovery_timeout_s=30.0, clock=breaker_clock or _Clock()
        ),
        **options,  # type: ignore[arg-type]
    )


def _state(resilient: ResilientClient, client: object) -> CircuitState:
    return resilient._breakers[id(client)].state


async def _complete(resilient: ResilientClient) -> CompletionResult:
    return await resilient.complete(model=_MODEL, messages=[ChatMessage(role="user", content="x")])


# ---------- con un respaldo disponible ----------


async def test_short_retry_after_waits_for_primary() -> None:
    """Hasta 5 s (el default) se espera al principal aunque haya respaldo."""
    primary, secondary = _fake(), _fake()
    primary.queue_error(_rate_limit(5.0))
    primary.queue_result(_result("después de esperar"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary, secondary], sleep)

    assert (await _complete(resilient)).text == "después de esperar"
    assert sleep.delays == [5.0]
    assert secondary.complete_calls == []


@pytest.mark.parametrize("retry_after_s", [5.5, 40.0, 120.0])
async def test_long_retry_after_goes_to_secondary_without_waiting(retry_after_s: float) -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(_rate_limit(retry_after_s))
    secondary.queue_result(_result("desde el respaldo"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary, secondary], sleep)

    assert (await _complete(resilient)).text == "desde el respaldo"
    assert sleep.delays == []
    assert len(primary.complete_calls) == 1


@pytest.mark.parametrize("error", [_rate_limit(None), LlmOverloadedError("HTTP 429")])
async def test_overload_without_retry_after_goes_to_secondary(error: LlmError) -> None:
    """Sin ``Retry-After`` el proveedor no dijo cuándo vuelve: el respaldo contesta ya."""
    primary, secondary = _fake(), _fake()
    primary.queue_error(error)
    secondary.queue_result(_result("desde el respaldo"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary, secondary], sleep)

    assert (await _complete(resilient)).text == "desde el respaldo"
    assert sleep.delays == []
    assert len(primary.complete_calls) == 1
    assert _state(resilient, primary) is CircuitState.CLOSED  # respondió: no está caído


async def test_max_wait_with_fallback_is_configurable() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(_rate_limit(20.0))
    primary.queue_result(_result("después de esperar"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary, secondary], sleep, rate_limit_max_wait_with_fallback_s=30.0)

    assert (await _complete(resilient)).text == "después de esperar"
    assert sleep.delays == [20.0]


async def test_rate_limit_on_last_attempt_does_not_wait() -> None:
    primary, secondary = _fake(), _fake()
    primary.queue_error(_rate_limit(5.0))
    primary.queue_error(_rate_limit(5.0))
    secondary.queue_result(_result("desde el respaldo"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary, secondary], sleep, max_attempts=2)

    assert (await _complete(resilient)).text == "desde el respaldo"
    assert sleep.delays == [5.0]


async def test_fallback_with_open_breaker_does_not_count() -> None:
    """Un respaldo que no puede atender no es respaldo: se espera al principal."""
    primary, secondary = _fake(), _fake()
    primary.queue_error(_rate_limit(30.0))
    primary.queue_result(_result("después de esperar"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary, secondary], sleep)
    resilient._breakers[id(secondary)].record_failure()  # umbral 1: abre
    assert _state(resilient, secondary) is CircuitState.OPEN

    assert (await _complete(resilient)).text == "después de esperar"
    assert sleep.delays == [30.0]
    assert secondary.complete_calls == []


async def test_checking_the_fallback_keeps_its_half_open_probe() -> None:
    """Preguntar si el respaldo puede atender no le consume la prueba HALF_OPEN."""
    primary, secondary = _fake(), _fake()
    breaker_clock = _Clock()
    sleep = _AdvancingSleep()
    resilient = _resilient([primary, secondary], sleep, breaker_clock=breaker_clock)
    resilient._breakers[id(secondary)].record_failure()  # umbral 1: abre
    breaker_clock.now = 30.0  # ventana de recovery: entra una prueba
    primary.queue_error(_rate_limit(None))
    secondary.queue_result(_result("desde el respaldo"))

    assert (await _complete(resilient)).text == "desde el respaldo"
    assert _state(resilient, secondary) is CircuitState.CLOSED


# ---------- sin respaldo ----------


async def test_single_provider_waits_retry_after() -> None:
    primary = _fake()
    primary.queue_error(_rate_limit(30.0))
    primary.queue_result(_result("después de esperar"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary], sleep)

    assert (await _complete(resilient)).text == "después de esperar"
    assert sleep.delays == [30.0]


async def test_retry_after_without_margin_for_the_request_degrades() -> None:
    """80 s entran en 90, pero el reintento se cortaría por el presupuesto: degrada ya."""
    primary = _fake()
    primary.queue_error(_rate_limit(80.0))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary], sleep)

    assert (await _complete(resilient)).finish_reason == "degraded"
    assert sleep.delays == []
    assert len(primary.complete_calls) == 1


async def test_retry_after_waits_while_budget_and_margin_last() -> None:
    """30 s más 15 de margen entran dos veces en 90; a la tercera quedan 30 y degrada."""
    primary = _fake()
    for _ in range(3):
        primary.queue_error(_rate_limit(30.0))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary], sleep, max_attempts=4)

    assert (await _complete(resilient)).finish_reason == "degraded"
    assert sleep.delays == [30.0, 30.0]
    assert len(primary.complete_calls) == 3


async def test_request_margin_is_configurable() -> None:
    primary = _fake()
    primary.queue_error(_rate_limit(80.0))
    primary.queue_result(_result("después de esperar"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary], sleep, rate_limit_request_margin_s=5.0)

    assert (await _complete(resilient)).text == "después de esperar"
    assert sleep.delays == [80.0]


@pytest.mark.parametrize("error", [_rate_limit(None), LlmOverloadedError("HTTP 429")])
async def test_single_provider_overload_without_retry_after_uses_backoff(
    error: LlmError,
) -> None:
    primary = _fake()
    primary.queue_error(error)
    primary.queue_result(_result("recuperado"))
    sleep = _AdvancingSleep()
    resilient = _resilient([primary], sleep)

    assert (await _complete(resilient)).text == "recuperado"
    assert len(sleep.delays) == 1
    assert 0.5 <= sleep.delays[0] <= 1.0  # backoff del primer intento + jitter


@pytest.mark.parametrize(
    "option", ["rate_limit_max_wait_with_fallback_s", "rate_limit_request_margin_s"]
)
def test_rate_limit_waits_cannot_be_negative(option: str) -> None:
    with pytest.raises(ValueError, match=option):
        ResilientClient(ClientPool([_fake()], FirstHealthy()), **{option: -1.0})  # type: ignore[arg-type]
