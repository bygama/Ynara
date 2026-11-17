"""Politica del ``ResilientClient``: reintentos, esperas y breaker (M3, ADR-028).

Modulo privado de ``app/llm/clients/``: lo usa solo ``resilient.py``, que se queda con
el recorrido de los candidatos y el presupuesto global. Aca vive lo que decide que hacer
con cada error de un candidato:

- ``REQUEST_ERRORS``, ``PROVIDER_ERRORS``, ``PERMANENT_ERRORS`` y ``BREAKER_ERRORS``: que
  se re-lanza, que pasa al siguiente candidato sin reintentar, que no se reintenta contra
  el mismo candidato y que cuenta como caida del proveedor.
- ``RetryPolicy``: cuanto esperar antes de reintentar el mismo candidato, o si hay que
  pasar al siguiente.
- ``BreakerTally``: que le hace el candidato en curso a su circuit breaker.
"""

from __future__ import annotations

import random

from app.llm.clients.circuit import CircuitBreaker
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
    ModelNotServedError,
)

# Errores del REQUEST: se re-lanzan sin reintentar ni pasar al respaldo (bug del
# request, no del proveedor: el respaldo fallaria igual). Incluye el contexto
# excedido, subclase de ``LlmBadRequestError``.
REQUEST_ERRORS: tuple[type[LlmError], ...] = (
    LlmBadRequestError,
    ModelNotServedError,
)

# Errores DEL PROVEEDOR: la credencial rechazada (401/403) y el request que ese
# proveedor no acepta (``LlmProviderRejectedError``: un 404 por un id de modelo que
# no tiene, un 413 por su limite, ...). No son del request: reintentar contra el
# mismo da lo mismo, pero el siguiente candidato tiene su credencial y sus modelos.
PROVIDER_ERRORS: tuple[type[LlmError], ...] = (LlmAuthError, LlmProviderRejectedError)

# Errores PERMANENTES: no se reintentan contra el mismo candidato. Los del request
# se re-lanzan; los del proveedor pasan al siguiente candidato. Cualquier OTRO
# ``LlmError`` (timeout, caida, sobrecarga, 408/425, respuesta malformada, tool
# parse error) se reintenta y, al agotarse, pasa al respaldo o degrada.
PERMANENT_ERRORS: tuple[type[LlmError], ...] = (*REQUEST_ERRORS, *PROVIDER_ERRORS)

# Errores que abren el breaker: el proveedor no respondio (timeout, 5xx, red). El
# resto de los ``LlmError`` son respuestas del proveedor: no lo marcan como caido.
BREAKER_ERRORS: tuple[type[LlmError], ...] = (LlmTimeoutError, LlmUnavailableError)


class RetryPolicy:
    """Cuanto esperar antes de reintentar el mismo candidato, o si hay que pasar al siguiente."""

    def __init__(self, *, max_attempts: int, base_backoff_s: float, max_backoff_s: float) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts tiene que ser al menos 1")
        self.max_attempts = max_attempts
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s

    def delay(self, exc: LlmError, attempt: int, remaining_s: float) -> float | None:
        """Espera antes de reintentar el mismo candidato, o ``None`` para pasar al siguiente.

        Sin reintento tras el ultimo intento ni ante un error permanente (la
        credencial y los modelos del proveedor no cambian entre intentos). Un 429
        con ``Retry-After`` espera lo que pidio el proveedor si entra en lo que queda
        del presupuesto (``remaining_s``); si no entra, pasa al siguiente candidato
        en vez de gastar el presupuesto esperando. El resto, backoff exponencial.
        """
        if isinstance(exc, PERMANENT_ERRORS) or attempt + 1 >= self.max_attempts:
            return None
        if isinstance(exc, LlmRateLimitError) and exc.retry_after_s is not None:
            return exc.retry_after_s if exc.retry_after_s <= remaining_s else None
        return self.backoff(attempt)

    def backoff(self, attempt: int) -> float:
        """Backoff exponencial cap a ``max_backoff_s`` mas jitter."""
        exponential = self._base_backoff_s * (2**attempt)
        capped = min(exponential, self._max_backoff_s)
        # Jitter para descorrelacionar reintentos de instancias paralelas;
        # no es uso criptografico, asi que ``random`` esta bien (noqa S311).
        jitter = random.uniform(0.0, self._base_backoff_s)  # noqa: S311
        # Cap duro: el jitter no debe empujar el delay por encima del techo.
        return min(capped + jitter, self._max_backoff_s)


class BreakerTally:
    """Lo que el candidato en curso de un ``complete()`` le hace a su breaker.

    Se resuelve UNA vez por candidato. Si devuelve un resultado, exito
    (``succeed``), aunque un intento anterior haya sido una caida: el proveedor
    respondio. Si se agota sin resultado o re-lanza un error del request
    (``resolve``), falla si alguno de sus intentos fue una caida
    (``BREAKER_ERRORS``) o si el presupuesto global lo corto; si no, exito,
    porque el proveedor respondio (aunque haya sido con un error del request, un
    rechazo propio del proveedor o un 429). Resolver siempre cierra tambien la
    prueba HALF_OPEN que ``allow()`` pudo haber abierto: sin eso el candidato
    quedaria salteado hasta reiniciar el proceso. Vive por llamada (no en el
    ``ResilientClient``) para que dos ``complete()`` concurrentes no se pisen.
    """

    def __init__(self) -> None:
        self._breaker: CircuitBreaker | None = None
        self._failed = False

    def start(self, breaker: CircuitBreaker) -> None:
        self._breaker = breaker
        self._failed = False

    def note(self, exc: LlmError) -> None:
        if isinstance(exc, BREAKER_ERRORS):
            self._failed = True

    def succeed(self) -> None:
        breaker, self._breaker = self._breaker, None
        if breaker is not None:
            breaker.record_success()

    def resolve(self, *, cut: bool = False) -> None:
        breaker, self._breaker = self._breaker, None
        if breaker is None:
            return
        if self._failed or cut:
            breaker.record_failure()
        else:
            breaker.record_success()
