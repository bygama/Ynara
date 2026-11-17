"""``ResilientClient`` sin degradar (``degrade=False``): que levanta al agotar la cadena.

Lo usa la consolidacion de memoria, que corre en un worker y puede reintentar mas
tarde: necesita distinguir una falla transitoria (reintentar) de un rechazo permanente
(no reintentar), y una respuesta degradada no los distingue. El chat sigue con el
default (``degrade=True``): esos casos estan en ``test_resilient.py`` y aca se fija que
el default no cambio.

Mismos dobles que ``test_resilient.py``: ``FakeLlmClient`` programable, ``sleep`` y
``clock`` fakes.
"""

from __future__ import annotations

import asyncio

import pytest

from app.llm.clients.circuit import CircuitBreaker
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import ClientPool, FirstHealthy
from app.llm.clients.resilient import ResilientClient
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmTransientError,
    LlmUnavailableError,
    ModelNotServedError,
)
from app.llm.schemas import ChatMessage, CompletionResult, ModelHealth

_MODEL = "agente"


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _FakeSleep:
    """``sleep`` async fake: registra cada espera, no duerme."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


class _SlowClient:
    """Cliente que tarda mas que el presupuesto: lo corta el ``asyncio.timeout``."""

    def __init__(self, *, delay_s: float) -> None:
        self._delay_s = delay_s

    def serves_model(self, model: str) -> bool:
        return model == _MODEL

    async def complete(self, **_kwargs: object) -> CompletionResult:
        await asyncio.sleep(self._delay_s)
        return _result("tarde")

    async def health(self) -> ModelHealth:
        return ModelHealth(model_name=_MODEL, healthy=True)


def _result(text: str = "ok") -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason="stop",
        prompt_tokens=1,
        completion_tokens=1,
        model_name=_MODEL,
        latency_ms=0.0,
    )


def _fake(model: str = _MODEL) -> FakeLlmClient:
    return FakeLlmClient(models=frozenset({model}))


def _resilient(
    clients: list[object],
    *,
    degrade: bool = False,
    max_attempts: int = 3,
    breaker_factory: object | None = None,
    total_budget_s: float | None = None,
) -> ResilientClient:
    kwargs: dict[str, object] = {
        "max_attempts": max_attempts,
        "sleep": _FakeSleep(),
        "clock": _Clock(),
        "degrade": degrade,
    }
    if breaker_factory is not None:
        kwargs["breaker_factory"] = breaker_factory
    if total_budget_s is not None:
        kwargs["total_budget_s"] = total_budget_s
    return ResilientClient(ClientPool(list(clients), FirstHealthy()), **kwargs)  # type: ignore[arg-type]


async def _complete(resilient: ResilientClient) -> CompletionResult:
    return await resilient.complete(
        model=_MODEL, messages=[ChatMessage(role="user", content="dato privado")]
    )


# ---------- el default no cambia: el chat sigue degradando ----------


async def test_default_still_degrades_when_the_chain_is_exhausted() -> None:
    """Sin ``degrade``, agotar la cadena devuelve la respuesta degradada, sin levantar."""
    client = _fake()
    for _ in range(3):
        client.queue_error(LlmUnavailableError("HTTP 503"))
    resilient = ResilientClient(
        ClientPool([client], FirstHealthy()), sleep=_FakeSleep(), clock=_Clock()
    )

    result = await _complete(resilient)

    assert result.finish_reason == "degraded"


# ---------- exito: igual que con el default ----------


async def test_success_returns_the_result() -> None:
    client = _fake()
    client.queue_result(_result("hola"))

    result = await _complete(_resilient([client]))

    assert result.text == "hola"
    assert result.finish_reason == "stop"


# ---------- transitorios: LlmTransientError ----------


async def test_outages_on_every_attempt_raise_transient() -> None:
    """Caidas en todos los intentos: mas tarde puede andar, se levanta el transitorio."""
    client = _fake()
    client.queue_error(LlmTimeoutError("timeout HTTP"))
    client.queue_error(LlmUnavailableError("HTTP 503"))
    client.queue_error(LlmUnavailableError("HTTP 502"))

    with pytest.raises(LlmTransientError) as exc_info:
        await _complete(_resilient([client]))

    # La raiz con etiqueta fija, no la caida del ultimo intento.
    assert type(exc_info.value) is LlmTransientError
    assert str(exc_info.value) == "falla transitoria del proveedor LLM: candidatos agotados"
    assert len(client.complete_calls) == 3


async def test_rate_limit_on_every_attempt_raises_transient() -> None:
    """Un 429 sostenido (sin respaldo) agota los intentos: transitorio, no permanente."""
    client = _fake()
    for _ in range(3):
        client.queue_error(LlmRateLimitError("HTTP 429"))

    with pytest.raises(LlmTransientError):
        await _complete(_resilient([client]))

    assert len(client.complete_calls) == 3


async def test_a_rejection_and_an_outage_is_transient() -> None:
    """Principal 401 y respaldo caido: el respaldo puede volver, se reintenta mas tarde."""
    primary, backup = _fake(), _fake()
    primary.queue_error(LlmAuthError("HTTP 401"))
    backup.queue_error(LlmUnavailableError("HTTP 503"))

    with pytest.raises(LlmTransientError):
        await _complete(_resilient([primary, backup], max_attempts=1))


async def test_open_breaker_is_transient() -> None:
    """Un candidato salteado por su breaker puede volver: transitorio, sin llamarlo."""
    client = _fake()
    client.queue_error(LlmTimeoutError("timeout HTTP"))
    resilient = _resilient(
        [client],
        max_attempts=1,
        breaker_factory=lambda: CircuitBreaker(failure_threshold=1, recovery_timeout_s=999.0),
    )
    with pytest.raises(LlmTransientError):
        await _complete(resilient)  # abre el breaker
    calls = len(client.complete_calls)

    with pytest.raises(LlmTransientError):
        await _complete(resilient)

    assert len(client.complete_calls) == calls


async def test_open_breaker_and_a_rejection_is_transient() -> None:
    """Si un candidato se salteo, que el otro rechace no alcanza para darlo por permanente."""
    primary, backup = _fake(), _fake()
    primary.queue_error(LlmTimeoutError("timeout HTTP"))
    backup.queue_error(LlmAuthError("HTTP 401"))
    backup.queue_error(LlmAuthError("HTTP 401"))
    resilient = _resilient(
        [primary, backup],
        max_attempts=1,
        breaker_factory=lambda: CircuitBreaker(failure_threshold=1, recovery_timeout_s=999.0),
    )
    with pytest.raises(LlmTransientError):
        await _complete(resilient)  # abre el breaker del principal

    with pytest.raises(LlmTransientError):
        await _complete(resilient)


async def test_exhausted_budget_raises_transient() -> None:
    """El presupuesto vencido corta y levanta el transitorio, sin cadena de la excepcion."""
    resilient = _resilient([_SlowClient(delay_s=5.0)], total_budget_s=0.05)

    with pytest.raises(LlmTransientError) as exc_info:
        await _complete(resilient)

    assert str(exc_info.value) == "falla transitoria del proveedor LLM: presupuesto agotado"
    assert exc_info.value.__suppress_context__ is True


# ---------- permanentes: el rechazo del proveedor ----------


async def test_rejected_credential_raises_auth_error_without_retrying() -> None:
    """Un 401 del unico proveedor: reintentar da lo mismo, se levanta el rechazo."""
    client = _fake()
    client.queue_error(LlmAuthError("HTTP 401"))

    with pytest.raises(LlmAuthError):
        await _complete(_resilient([client]))

    assert len(client.complete_calls) == 1


async def test_every_provider_rejecting_raises_the_last_rejection() -> None:
    """Todos los proveedores rechazan (credencial, 4xx propio): permanente."""
    primary, backup = _fake(), _fake()
    primary.queue_error(LlmAuthError("HTTP 401"))
    backup.queue_error(LlmProviderRejectedError("HTTP 404"))

    with pytest.raises(LlmProviderRejectedError):
        await _complete(_resilient([primary, backup]))

    assert len(primary.complete_calls) == 1
    assert len(backup.complete_calls) == 1


async def test_no_candidate_for_the_model_raises_model_not_served() -> None:
    """Ningun cliente sirve el modelo: es de configuracion, no se reintenta."""
    with pytest.raises(ModelNotServedError):
        await _complete(_resilient([_fake(model="otro")]))


async def test_request_errors_still_propagate() -> None:
    """Un error del request se re-lanza igual que con el default."""
    client = _fake()
    client.queue_error(LlmBadRequestError("HTTP 400"))

    with pytest.raises(LlmBadRequestError):
        await _complete(_resilient([client]))
