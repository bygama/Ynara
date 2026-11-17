"""Politica del ``ResilientClient``: reintentos, esperas y breaker (M3, ADR-028).

Modulo privado de ``app/llm/clients/``: lo usa solo ``resilient.py``, que se queda con
el recorrido de los candidatos y el presupuesto global. Aca vive lo que decide que hacer
con cada error de un candidato:

- ``REQUEST_ERRORS``, ``PROVIDER_ERRORS``, ``PERMANENT_ERRORS`` y ``BREAKER_ERRORS``: que
  se re-lanza, que pasa al siguiente candidato sin reintentar, que no se reintenta contra
  el mismo candidato y que cuenta como caida del proveedor.
- ``RetryPolicy``: cuanto esperar antes de reintentar el mismo candidato, o si hay que
  pasar al siguiente (con los defaults de la espera de un 429).
- ``BreakerTally``: que le hace el candidato en curso a su circuit breaker.
"""

from __future__ import annotations

import random

from app.llm.clients.circuit import CircuitBreaker
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmError,
    LlmOverloadedError,
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


# Con un respaldo disponible, el ``Retry-After`` mas largo que se espera antes de
# reintentar el mismo candidato: si el proveedor pide mas, el respaldo contesta antes.
DEFAULT_MAX_WAIT_WITH_FALLBACK_S: float = 5.0

# Lo que tiene que quedar del presupuesto despues de esperar un ``Retry-After``, para
# el request que sigue: sin ese margen el reintento se cortaria por el presupuesto,
# asi que se pasa al siguiente candidato (o se degrada) sin esperar.
DEFAULT_REQUEST_MARGIN_S: float = 15.0


class RetryPolicy:
    """Cuanto esperar antes de reintentar el mismo candidato, o si hay que pasar al siguiente."""

    def __init__(
        self,
        *,
        max_attempts: int,
        base_backoff_s: float,
        max_backoff_s: float,
        max_wait_with_fallback_s: float,
        request_margin_s: float,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts tiene que ser al menos 1")
        if max_wait_with_fallback_s < 0:
            raise ValueError("rate_limit_max_wait_with_fallback_s no puede ser negativo")
        if request_margin_s < 0:
            raise ValueError("rate_limit_request_margin_s no puede ser negativo")
        self.max_attempts = max_attempts
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s
        self._max_wait_with_fallback_s = max_wait_with_fallback_s
        self._request_margin_s = request_margin_s

    def delay(
        self, exc: LlmError, attempt: int, remaining_s: float, *, has_fallback: bool
    ) -> float | None:
        """Espera antes de reintentar el mismo candidato, o ``None`` para pasar al siguiente.

        Sin reintento tras el ultimo intento ni ante un error permanente (la
        credencial y los modelos del proveedor no cambian entre intentos). Un 429 o
        una sobrecarga siguen ``_overload_delay``; el resto, backoff exponencial.
        ``remaining_s`` es lo que queda del presupuesto global y ``has_fallback``
        dice si algun candidato posterior puede atender.
        """
        if isinstance(exc, PERMANENT_ERRORS) or attempt + 1 >= self.max_attempts:
            return None
        if isinstance(exc, LlmOverloadedError):
            return self._overload_delay(exc, attempt, remaining_s, has_fallback=has_fallback)
        return self.backoff(attempt)

    def _overload_delay(
        self, exc: LlmOverloadedError, attempt: int, remaining_s: float, *, has_fallback: bool
    ) -> float | None:
        """Espera ante un 429 o una sobrecarga, o ``None`` para pasar al siguiente.

        Sin ``Retry-After`` el proveedor no dijo cuando vuelve: con respaldo se pasa
        a el; sin respaldo, backoff como cualquier transitorio. Con ``Retry-After``,
        si hay respaldo solo se espera si es corto (``max_wait_with_fallback_s``), y
        en cualquier caso solo si despues queda ``request_margin_s`` del presupuesto
        para el request; si no, se pasa al siguiente en vez de gastar el
        presupuesto esperando un reintento que despues se corta.
        """
        retry_after_s = exc.retry_after_s if isinstance(exc, LlmRateLimitError) else None
        if retry_after_s is None:
            return None if has_fallback else self.backoff(attempt)
        if has_fallback and retry_after_s > self._max_wait_with_fallback_s:
            return None
        if retry_after_s + self._request_margin_s > remaining_s:
            return None
        return retry_after_s

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
    (``BREAKER_ERRORS``) o si el presupuesto global corto un request en curso
    (``sending`` sin respuesta); si no, exito, porque el proveedor respondio
    (aunque haya sido con un error del request, un rechazo propio del proveedor
    o un 429). Un corte durante una espera (el ``Retry-After`` de un 429, un
    backoff) no es una caida: el proveedor ya habia contestado. Resolver siempre
    cierra tambien la prueba HALF_OPEN que ``allow()`` pudo haber abierto: sin
    eso el candidato quedaria salteado hasta reiniciar el proceso. Vive por
    llamada (no en el ``ResilientClient``) para que dos ``complete()``
    concurrentes no se pisen.
    """

    def __init__(self) -> None:
        self._breaker: CircuitBreaker | None = None
        self._failed = False
        self._in_flight = False

    def start(self, breaker: CircuitBreaker) -> None:
        self._breaker = breaker
        self._failed = False
        self._in_flight = False

    def sending(self) -> None:
        """Hay un request en curso: si el presupuesto lo corta, cuenta como caida."""
        self._in_flight = True

    def note(self, exc: LlmError) -> None:
        """El request en curso termino con ``exc``: el proveedor contesto o se cayo."""
        self._in_flight = False
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
        if self._failed or (cut and self._in_flight):
            breaker.record_failure()
        else:
            breaker.record_success()
