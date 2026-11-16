"""Helpers HTTP compartidos por los clientes de proveedores de IA (regla #4b).

- ``BearerAuth`` manda la credencial del proveedor como ``Authorization: Bearer``.
- ``raise_for_provider_status`` mapea un status >= 400 a la taxonomía ``LlmError``.
- ``parse_retry_after`` lee el header ``Retry-After`` (segundos o fecha HTTP).
- ``transport_errors`` convierte los errores de red de ``httpx`` en ``LlmError``.

Sirven para cualquier cliente de este paquete (chat o embeddings): un proveedor
responde igual a los dos ante una credencial inválida, un límite de tasa o una caída.

Regla #4a: el detail de cada excepción es una etiqueta fija (``HTTP <status>``,
``timeout HTTP``), nunca el body del proveedor ni el mensaje de ``httpx``. Un 400 puede
repetir el prompt del usuario, un 401 un pedazo de la clave y el mensaje de ``httpx``
trae la URL del proveedor. El body solo se mira para elegir el TIPO de excepción
(contexto excedido), y la excepción original de ``httpx`` queda en ``__cause__``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx
from pydantic import SecretStr

from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmContextOverflowError,
    LlmError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
)

# ``error.code`` que devuelven las APIs compatibles con OpenAI cuando el prompt más la
# generación no entran en la ventana del modelo.
_CONTEXT_OVERFLOW_CODES = frozenset({"context_length_exceeded", "context_window_exceeded"})

# Frases del mensaje de error para el mismo caso, de los proveedores que no mandan ese
# ``code``. Se buscan en minúscula en el body entero.
_CONTEXT_OVERFLOW_SIGNATURES: tuple[str, ...] = (
    "maximum context length",
    "context length",
    "context window",
    "reduce the length of the messages",
)


class BearerAuth(httpx.Auth):
    """Credencial del proveedor como ``Authorization: Bearer <clave>``.

    La clave se lee del ``SecretStr`` recién al armar el header de cada request, así no
    queda en una variable local del cliente (que podría terminar en un traceback) ni en
    ningún ``repr``.
    """

    def __init__(self, api_key: SecretStr) -> None:
        self._api_key = api_key

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {self._api_key.get_secret_value()}"
        yield request

    def __repr__(self) -> str:
        return "BearerAuth(***)"


def raise_for_provider_status(response: httpx.Response) -> None:
    """Mapea un status >= 400 a la taxonomía ``LlmError`` (no-op si es < 400).

    - 401 / 403                 -> ``LlmAuthError`` (credencial del proveedor)
    - 429                       -> ``LlmRateLimitError``, con la espera de ``Retry-After``
    - 400 con contexto excedido -> ``LlmContextOverflowError``
    - otro 400, o 422           -> ``LlmBadRequestError``
    - >= 500                    -> ``LlmUnavailableError``
    - otro >= 400               -> ``LlmError`` genérico

    Para detectar el contexto excedido mira el body: en streaming hay que leerlo antes
    (``await response.aread()``); si no está leído, el 400 queda como request inválido.
    """
    status = response.status_code
    if status < httpx.codes.BAD_REQUEST:
        return
    label = f"HTTP {status}"
    if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
        raise LlmAuthError(label)
    if status == httpx.codes.TOO_MANY_REQUESTS:
        retry_after_s = parse_retry_after(response.headers.get("retry-after"))
        raise LlmRateLimitError(label, retry_after_s=retry_after_s)
    if status == httpx.codes.BAD_REQUEST:
        if _is_context_overflow(_body_text(response)):
            raise LlmContextOverflowError(label)
        raise LlmBadRequestError(label)
    if status == httpx.codes.UNPROCESSABLE_ENTITY:
        raise LlmBadRequestError(label)
    if status >= httpx.codes.INTERNAL_SERVER_ERROR:
        raise LlmUnavailableError(label)
    raise LlmError(label)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Segundos de espera de un header ``Retry-After``, o ``None`` si no se puede leer.

    Acepta las dos formas de RFC 9110: segundos (``"30"``; se tolera un decimal) o una
    fecha HTTP (``"Mon, 16 Nov 2026 21:30:00 GMT"``), que se convierte en la distancia
    desde ``now`` (default: ahora, en UTC). Una fecha pasada o un número negativo dan
    ``0.0``; un valor ilegible da ``None`` y el caller usa su backoff normal.
    """
    if value is None:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        seconds = float(raw)
    except ValueError:
        return _seconds_until(raw, now or datetime.now(UTC))
    if not math.isfinite(seconds):
        return None
    return max(seconds, 0.0)


@contextmanager
def transport_errors() -> Iterator[None]:
    """Convierte los errores de red de ``httpx`` en la taxonomía ``LlmError``.

    - timeout (de conexión, lectura, escritura o del pool) -> ``LlmTimeoutError``
    - no se pudo conectar                                -> ``LlmUnavailableError``
    - cualquier otro error del request (lectura cortada a mitad de un stream,
      protocolo, body que no se puede decodificar)       -> ``LlmUnavailableError``

    Así de un cliente nunca sale una excepción de ``httpx``: el ``ResilientClient``
    reintenta, cambia de proveedor o degrada solo ante ``LlmError``.
    """
    try:
        yield
    except httpx.TimeoutException as exc:
        raise LlmTimeoutError("timeout HTTP") from exc
    except httpx.ConnectError as exc:
        raise LlmUnavailableError("connect error") from exc
    except httpx.RequestError as exc:
        raise LlmUnavailableError("transport error") from exc


# ---------- helpers internos ----------


def _body_text(response: httpx.Response) -> str:
    """Body de la respuesta como texto, o ``""`` si es un stream que no se leyó."""
    try:
        return response.text
    except httpx.ResponseNotRead:
        return ""


def _is_context_overflow(body_text: str) -> bool:
    """``True`` si el body de un 400 dice que se excedió la ventana de contexto."""
    if not body_text:
        return False
    if _error_code(body_text) in _CONTEXT_OVERFLOW_CODES:
        return True
    lowered = body_text.lower()
    return any(signature in lowered for signature in _CONTEXT_OVERFLOW_SIGNATURES)


def _error_code(body_text: str) -> str | None:
    """``error.code`` del body JSON con la forma de OpenAI, o ``None`` si no hay."""
    try:
        body = json.loads(body_text)
    except ValueError:
        return None
    error = body.get("error") if isinstance(body, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) else None


def _seconds_until(http_date: str, now: datetime) -> float | None:
    """Segundos desde ``now`` hasta una fecha HTTP, o ``None`` si no es una fecha."""
    try:
        when = parsedate_to_datetime(http_date)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return max((when - now).total_seconds(), 0.0)
