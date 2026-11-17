"""Qué le hace el ``ResilientClient`` al circuit breaker de cada candidato.

Complementa ``test_resilient.py`` y ``test_resilient_policy.py`` con el estado del
breaker después de cada camino de ``complete()``: un candidato que termina respondiendo
cierra el breaker aunque un intento anterior haya sido una caída, también cuando es la
prueba HALF_OPEN, y varios requests que responden en el reintento no lo abren.

Mismos dobles que el resto de los tests del cliente resiliente: ``FakeLlmClient``
programable, ``sleep`` y ``clock`` fakes.
"""

from __future__ import annotations

from app.llm.clients.circuit import CircuitBreaker, CircuitState
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import ClientPool, FirstHealthy
from app.llm.clients.resilient import ResilientClient
from app.llm.errors import LlmTimeoutError, LlmUnavailableError
from app.llm.schemas import ChatMessage, CompletionResult

_MODEL = "agente"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _no_sleep(_delay: float) -> None:
    return None


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


def _resilient(
    clients: list[FakeLlmClient],
    *,
    breaker_factory: object | None = None,
    max_attempts: int = 3,
) -> ResilientClient:
    kwargs: dict[str, object] = {"max_attempts": max_attempts, "sleep": _no_sleep}
    if breaker_factory is not None:
        kwargs["breaker_factory"] = breaker_factory
    return ResilientClient(ClientPool(list(clients), FirstHealthy()), **kwargs)  # type: ignore[arg-type]


def _state(resilient: ResilientClient, client: object) -> CircuitState:
    return resilient._breakers[id(client)].state


async def _complete(resilient: ResilientClient) -> CompletionResult:
    return await resilient.complete(model=_MODEL, messages=[ChatMessage(role="user", content="x")])


# ---------- un candidato que termina respondiendo cierra el breaker ----------


async def test_success_after_outage_closes_breaker() -> None:
    """Con umbral 1, contar la caída previa al éxito lo dejaría abierto."""
    primary = _fake()
    primary.queue_error(LlmTimeoutError("timeout HTTP"))
    primary.queue_result(_result("recuperado"))
    resilient = _resilient(
        [primary],
        breaker_factory=lambda: CircuitBreaker(failure_threshold=1, recovery_timeout_s=30.0),
    )

    result = await _complete(resilient)

    assert result.text == "recuperado"
    assert _state(resilient, primary) is CircuitState.CLOSED


async def test_half_open_probe_answering_on_retry_closes_breaker() -> None:
    primary = _fake()
    clock = _Clock()
    resilient = _resilient(
        [primary],
        breaker_factory=lambda: CircuitBreaker(
            failure_threshold=1, recovery_timeout_s=30.0, clock=clock
        ),
    )
    for _ in range(3):
        primary.queue_error(LlmTimeoutError("timeout HTTP"))
    assert (await _complete(resilient)).finish_reason == "degraded"
    assert _state(resilient, primary) is CircuitState.OPEN

    clock.now = 30.0  # ventana de recovery: entra una prueba
    primary.queue_error(LlmUnavailableError("HTTP 503"))
    primary.queue_result(_result("de vuelta"))
    assert (await _complete(resilient)).text == "de vuelta"
    assert _state(resilient, primary) is CircuitState.CLOSED

    primary.queue_result(_result("sigue"))
    assert (await _complete(resilient)).text == "sigue"  # no quedó salteado


async def test_requests_answered_on_retry_never_open_breaker() -> None:
    """Con el umbral por default (5), seis requests que responden en el reintento."""
    primary, secondary = _fake(), _fake()
    resilient = _resilient([primary, secondary])

    for _ in range(6):
        primary.queue_error(LlmUnavailableError("HTTP 503"))
        primary.queue_result(_result("principal"))
        assert (await _complete(resilient)).text == "principal"

    assert _state(resilient, primary) is CircuitState.CLOSED
    assert secondary.complete_calls == []
