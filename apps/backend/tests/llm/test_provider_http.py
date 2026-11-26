"""Tests de los helpers HTTP de los proveedores de IA (``providers/_http.py``).

Sin red: las respuestas se arman con ``httpx.Response`` y las excepciones de red se
levantan a mano. Cubren el mapeo de status a la taxonomía ``LlmError`` (credencial,
límite de tasa, contexto excedido, caídas), la lectura de ``Retry-After`` en segundos y
en fecha HTTP, la credencial como ``Bearer`` y que ningún detail filtre el body del
proveedor (regla #4a).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from pydantic import SecretStr

from app.llm.clients.providers._http import (
    BearerAuth,
    parse_retry_after,
    raise_for_provider_status,
    transport_errors,
)
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmContextOverflowError,
    LlmError,
    LlmOverloadedError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmSpendLimitError,
    LlmTimeoutError,
    LlmTransientError,
    LlmUnavailableError,
)

_NOW = datetime(2026, 11, 16, 21, 0, 0, tzinfo=UTC)


def _error_body(message: str, *, code: str | None = None) -> dict[str, object]:
    """Body de error con la forma de OpenAI (``{"error": {...}}``)."""
    error: dict[str, object] = {"message": message, "type": "invalid_request_error"}
    if code is not None:
        error["code"] = code
    return {"error": error}


# ---------- Retry-After ----------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("30", 30.0),
        (" 7 ", 7.0),
        ("1.5", 1.5),
        ("0", 0.0),
        ("-3", 0.0),
    ],
)
def test_parse_retry_after_seconds(value: str, expected: float) -> None:
    assert parse_retry_after(value, now=_NOW) == expected


def test_parse_retry_after_http_date() -> None:
    """Una fecha HTTP se convierte en los segundos que faltan desde ``now``."""
    when = format_datetime(_NOW + timedelta(seconds=45), usegmt=True)
    assert parse_retry_after(when, now=_NOW) == 45.0


def test_parse_retry_after_past_date_is_zero() -> None:
    when = format_datetime(_NOW - timedelta(minutes=5), usegmt=True)
    assert parse_retry_after(when, now=_NOW) == 0.0


def test_parse_retry_after_http_date_defaults_to_now() -> None:
    """Sin ``now`` explícito, mide contra el reloj real (resolución de un segundo)."""
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=30), usegmt=True)
    seconds = parse_retry_after(when)
    assert seconds is not None
    assert 28.0 <= seconds <= 30.0


@pytest.mark.parametrize("value", [None, "", "   ", "pronto", "nan", "inf", "Lun, 99 Foo"])
def test_parse_retry_after_unreadable_is_none(value: str | None) -> None:
    assert parse_retry_after(value, now=_NOW) is None


# ---------- mapeo de status ----------


def test_success_status_is_noop() -> None:
    raise_for_provider_status(httpx.Response(200, json={}))
    raise_for_provider_status(httpx.Response(204))


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, LlmAuthError),
        (403, LlmAuthError),
        (429, LlmRateLimitError),
        (400, LlmBadRequestError),
        (422, LlmBadRequestError),
        (500, LlmUnavailableError),
        (502, LlmUnavailableError),
        (503, LlmUnavailableError),
        (504, LlmUnavailableError),
    ],
)
def test_status_mapping(status: int, expected: type[LlmError]) -> None:
    response = httpx.Response(status, json=_error_body("x"))
    with pytest.raises(expected) as excinfo:
        raise_for_provider_status(response)
    assert str(excinfo.value).endswith(f"HTTP {status}")


def test_rate_limit_is_overloaded() -> None:
    """El 429 sigue siendo sobrecarga para quien ya la trataba (p.ej. el playground)."""
    with pytest.raises(LlmOverloadedError):
        raise_for_provider_status(httpx.Response(429))


@pytest.mark.parametrize("status", [402, 404, 405, 409, 410, 413, 415])
def test_provider_specific_4xx_is_rejected_by_that_provider(status: int) -> None:
    """Un 4xx propio del proveedor (modelo inexistente, límite, crédito): otro puede aceptarlo."""
    with pytest.raises(LlmProviderRejectedError) as excinfo:
        raise_for_provider_status(httpx.Response(status, json=_error_body("x")))
    assert not isinstance(excinfo.value, LlmBadRequestError)
    assert str(excinfo.value) == f"request rechazado por el proveedor LLM: HTTP {status}"


@pytest.mark.parametrize("status", [408, 425])
def test_4xx_that_asks_to_repeat_is_transient(status: int) -> None:
    """408 y 425 piden repetir el request: ``LlmTransientError`` pelado.

    Es la clase que la consolidación de memoria reintenta (``autoretry_for``); con un
    ``LlmError`` base el worker descartaba la op o la extracción. Pelado y no una
    subclase: no es una caída (no abre el breaker) ni una sobrecarga (sin la espera del
    429), así que el ``ResilientClient`` lo sigue reintentando con backoff.
    """
    with pytest.raises(LlmTransientError) as excinfo:
        raise_for_provider_status(httpx.Response(status, json=_error_body("x")))
    assert type(excinfo.value) is LlmTransientError
    assert str(excinfo.value) == f"falla transitoria del proveedor LLM: HTTP {status}"


def test_auth_is_not_a_request_error() -> None:
    with pytest.raises(LlmAuthError) as excinfo:
        raise_for_provider_status(httpx.Response(401, json=_error_body("bad key")))
    assert not isinstance(excinfo.value, LlmBadRequestError)


def test_rate_limit_reads_retry_after_seconds() -> None:
    response = httpx.Response(429, headers={"Retry-After": "12"})
    with pytest.raises(LlmRateLimitError) as excinfo:
        raise_for_provider_status(response)
    assert excinfo.value.retry_after_s == 12.0


def test_rate_limit_reads_retry_after_http_date() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=40), usegmt=True)
    response = httpx.Response(429, headers={"Retry-After": when})
    with pytest.raises(LlmRateLimitError) as excinfo:
        raise_for_provider_status(response)
    assert excinfo.value.retry_after_s is not None
    assert 38.0 <= excinfo.value.retry_after_s <= 40.0


def test_rate_limit_without_retry_after() -> None:
    with pytest.raises(LlmRateLimitError) as excinfo:
        raise_for_provider_status(httpx.Response(429, json=_error_body("slow down")))
    assert excinfo.value.retry_after_s is None
    assert not isinstance(excinfo.value, LlmSpendLimitError)


def _anthropic_error_body(
    error_type: str, message: str, *, error_code: str | None = None
) -> dict[str, object]:
    """Body de error con la forma de Anthropic (el código va en ``error.details``)."""
    error: dict[str, object] = {"type": error_type, "message": message}
    if error_code is not None:
        error["details"] = {"error_code": error_code}
    return {"type": "error", "error": error}


def test_429_spend_limit_is_a_rate_limit_with_its_own_name() -> None:
    """El tope de gasto sigue siendo un 429 (mismo manejo) con un nombre que lo dice."""
    body = _anthropic_error_body(
        "rate_limit_error", "spend limit", error_code="enforced_spend_limit_reached"
    )
    with pytest.raises(LlmSpendLimitError) as excinfo:
        raise_for_provider_status(httpx.Response(429, json=body))
    assert isinstance(excinfo.value, LlmRateLimitError)
    assert isinstance(excinfo.value, LlmTransientError)
    assert excinfo.value.retry_after_s is None
    assert "spend limit" not in str(excinfo.value)


def test_429_with_other_anthropic_code_is_plain_rate_limit() -> None:
    body = _anthropic_error_body("rate_limit_error", "slow down", error_code="other_limit")
    with pytest.raises(LlmRateLimitError) as excinfo:
        raise_for_provider_status(httpx.Response(429, json=body))
    assert not isinstance(excinfo.value, LlmSpendLimitError)


async def test_429_spend_limit_in_stream_needs_the_body_read() -> None:
    """Como el overflow: sin leer el body de un stream queda como límite de tasa común."""
    raw = b'{"error": {"details": {"error_code": "enforced_spend_limit_reached"}}}'

    async def _body() -> AsyncIterator[bytes]:
        yield raw

    response = httpx.Response(429, content=_body())
    with pytest.raises(LlmRateLimitError) as excinfo:
        raise_for_provider_status(response)
    assert not isinstance(excinfo.value, LlmSpendLimitError)
    await response.aread()
    with pytest.raises(LlmSpendLimitError):
        raise_for_provider_status(response)
    await response.aclose()


# ---------- contexto excedido ----------


def test_400_with_context_length_code_is_overflow() -> None:
    body = _error_body("request too long", code="context_length_exceeded")
    with pytest.raises(LlmContextOverflowError):
        raise_for_provider_status(httpx.Response(400, json=body))


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 128000 tokens.",
        "The input exceeds the context length of the model.",
        "Prompt is larger than the context window",
        "Please reduce the length of the messages.",
        "MAXIMUM CONTEXT LENGTH exceeded",
    ],
)
def test_400_with_overflow_message_is_overflow(message: str) -> None:
    with pytest.raises(LlmContextOverflowError):
        raise_for_provider_status(httpx.Response(400, json=_error_body(message)))


def test_400_anthropic_prompt_too_long_is_overflow() -> None:
    body = _anthropic_error_body(
        "invalid_request_error", "prompt is too long: 210000 tokens > 200000 maximum"
    )
    with pytest.raises(LlmContextOverflowError):
        raise_for_provider_status(httpx.Response(400, json=body))


def test_400_anthropic_error_code_in_details_is_read() -> None:
    """El código de Anthropic va en ``error.details.error_code`` y se lee igual."""
    body = _anthropic_error_body(
        "invalid_request_error", "too much", error_code="context_length_exceeded"
    )
    with pytest.raises(LlmContextOverflowError):
        raise_for_provider_status(httpx.Response(400, json=body))


def test_400_generic_stays_bad_request() -> None:
    body = _error_body("invalid value for 'temperature'", code="invalid_value")
    with pytest.raises(LlmBadRequestError) as excinfo:
        raise_for_provider_status(httpx.Response(400, json=body))
    assert not isinstance(excinfo.value, LlmContextOverflowError)


def test_422_with_overflow_message_stays_bad_request() -> None:
    """La detección de contexto excedido es solo para 400 (el caso de la API de OpenAI)."""
    body = _error_body("maximum context length", code="context_length_exceeded")
    with pytest.raises(LlmBadRequestError) as excinfo:
        raise_for_provider_status(httpx.Response(422, json=body))
    assert not isinstance(excinfo.value, LlmContextOverflowError)


def test_400_non_json_body_is_bad_request() -> None:
    with pytest.raises(LlmBadRequestError):
        raise_for_provider_status(httpx.Response(400, text="<html>bad request</html>"))


async def test_400_stream_not_read_is_bad_request() -> None:
    """Sin leer el body de un stream no hay forma de ver el overflow: queda como 400."""

    async def _body() -> AsyncIterator[bytes]:
        yield b'{"error": {"code": "context_length_exceeded"}}'

    response = httpx.Response(400, content=_body())
    with pytest.raises(LlmBadRequestError) as excinfo:
        raise_for_provider_status(response)
    assert not isinstance(excinfo.value, LlmContextOverflowError)
    await response.aclose()


# ---------- regla #4a: el detail nunca trae el body ----------


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 429, 500])
def test_detail_never_leaks_body(status: int) -> None:
    leaky = "Incorrect API key provided: sk-abc...wxyz. Tu mensaje era 'mi secreto 4111-1111'"
    response = httpx.Response(status, json=_error_body(leaky, code="context_length_exceeded"))
    with pytest.raises(LlmError) as excinfo:
        raise_for_provider_status(response)
    rendered = str(excinfo.value)
    assert "sk-abc" not in rendered
    assert "mi secreto" not in rendered
    assert rendered.endswith(f": HTTP {status}")


# ---------- credencial ----------


def test_bearer_auth_sets_header() -> None:
    auth = BearerAuth(SecretStr("sk-test-123"))
    request = httpx.Request("POST", "https://proveedor.test/v1/chat/completions")
    flow = auth.auth_flow(request)
    sent = next(flow)
    assert sent.headers["Authorization"] == "Bearer sk-test-123"


def test_bearer_auth_repr_hides_key() -> None:
    auth = BearerAuth(SecretStr("sk-test-123"))
    assert "sk-test-123" not in repr(auth)


# ---------- errores de red ----------


@pytest.mark.parametrize(
    ("raised", "expected", "rendered"),
    [
        (httpx.ConnectTimeout("t"), LlmTimeoutError, "timeout de inferencia LLM: timeout HTTP"),
        (httpx.ReadTimeout("t"), LlmTimeoutError, "timeout de inferencia LLM: timeout HTTP"),
        (httpx.PoolTimeout("t"), LlmTimeoutError, "timeout de inferencia LLM: timeout HTTP"),
        (
            httpx.ConnectError("c"),
            LlmUnavailableError,
            "instancia LLM no disponible: connect error",
        ),
        (
            httpx.ReadError("r"),
            LlmUnavailableError,
            "instancia LLM no disponible: transport error",
        ),
        (
            httpx.RemoteProtocolError("p"),
            LlmUnavailableError,
            "instancia LLM no disponible: transport error",
        ),
        (
            httpx.DecodingError("d"),
            LlmUnavailableError,
            "instancia LLM no disponible: transport error",
        ),
    ],
)
def test_transport_errors_mapping(
    raised: httpx.RequestError, expected: type[LlmError], rendered: str
) -> None:
    with pytest.raises(expected) as excinfo, transport_errors():
        raise raised
    assert str(excinfo.value) == rendered
    assert excinfo.value.__cause__ is raised


def test_transport_errors_does_not_leak_url() -> None:
    leaky = "All connection attempts failed for https://proveedor.test/v1"
    with pytest.raises(LlmUnavailableError) as excinfo, transport_errors():
        raise httpx.ConnectError(leaky)
    assert "proveedor.test" not in str(excinfo.value)


def test_transport_errors_lets_other_exceptions_through() -> None:
    with pytest.raises(KeyError), transport_errors():
        raise KeyError("x")
