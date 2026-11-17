"""Contract tests de ``OpenAICompatClient`` con ``httpx.MockTransport`` (sin red).

Portan los casos del camino OpenAI de ``test_vllm_client.py`` (texto, timeouts, mapeo
de errores, que nada filtre el host, health) y agregan lo propio de un proveedor por
API: la credencial como ``Bearer`` (y que no aparezca en logs, ``repr`` ni
excepciones), el alias del router -> id del modelo, 401/403/429 con ``Retry-After``, el
contexto excedido y, con el ``ResilientClient``, el respaldo: sin reintento ante la
credencial rechazada y el paso al segundo proveedor. Tools, razonamiento y respuestas
raras están en ``test_openai_compat_wire.py``; el streaming, en
``test_openai_compat_stream.py``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.llm_protocol import LLMClientProtocol
from app.llm.clients.base import LLMClient
from app.llm.clients.circuit import CircuitBreaker
from app.llm.clients.pool import build_provider_pool
from app.llm.clients.providers.openai_compat import OpenAICompatClient
from app.llm.clients.resilient import ResilientClient
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmContextOverflowError,
    LlmError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
    ModelNotServedError,
)
from app.llm.schemas import ChatMessage

_FIXTURES = Path(__file__).parent / "fixtures"
_BASE_URL = "https://proveedor.test/v1"
_HOST = "proveedor.test"
_API_KEY = "sk-test-0123456789abcdef"
_BACKUP_KEY = "sk-test-respaldo-fedcba9876543210"
_ALIAS = "qwen-3.5-9b"
_MODEL_ID = "org/qwen3.5-9b-instruct"

Handler = Any


def _load(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _client(handler: Handler, **overrides: Any) -> OpenAICompatClient:
    kwargs: dict[str, Any] = {
        "name": "principal",
        "base_url": _BASE_URL,
        "api_key": SecretStr(_API_KEY),
        "model_map": {_ALIAS: _MODEL_ID},
        "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    }
    kwargs.update(overrides)
    return OpenAICompatClient(**kwargs)


def _capturing(body: Any, captured: dict[str, Any], status: int = 200) -> Handler:
    """Handler que guarda el request y el payload y responde ``body``."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["payload"] = json.loads(request.content) if request.content else None
        return httpx.Response(status, json=body)

    return handler


def _completion(message: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": _MODEL_ID,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    body.update(extra)
    return body


def _messages() -> list[ChatMessage]:
    return [ChatMessage(role="user", content="hola")]


async def _complete(client: OpenAICompatClient, **kwargs: Any) -> Any:
    return await client.complete(model=_ALIAS, messages=_messages(), **kwargs)


# ---------- contrato y modelos ----------


def test_satisfies_llm_client_protocols() -> None:
    client = _client(lambda request: httpx.Response(200))
    assert isinstance(client, LLMClient)
    assert isinstance(client, LLMClientProtocol)


def test_serves_only_mapped_aliases() -> None:
    client = _client(lambda request: httpx.Response(200))
    assert client.serves_model(_ALIAS)
    assert not client.serves_model(_MODEL_ID)
    assert not client.serves_model("otro")


async def test_alias_is_sent_as_provider_model_id() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    await _complete(client)
    assert captured["payload"]["model"] == _MODEL_ID


async def test_unknown_alias_raises_without_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_load("completion_text.json"))

    client = _client(handler)
    with pytest.raises(ModelNotServedError):
        await client.complete(model="desconocido", messages=_messages())
    assert calls == []


async def test_sends_bearer_credential() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    await _complete(client)
    assert captured["request"].headers["Authorization"] == f"Bearer {_API_KEY}"


# ---------- complete: texto y payload ----------


async def test_complete_text() -> None:
    client = _client(lambda request: httpx.Response(200, json=_load("completion_text.json")))
    result = await _complete(client)
    assert result.text == "Hola, en que te puedo ayudar?"
    assert result.tool_calls == []
    assert result.finish_reason == "stop"
    assert (result.prompt_tokens, result.completion_tokens) == (18, 9)
    assert result.model_name == "qwen-3.5-9b"
    assert result.reasoning is None
    assert result.latency_ms >= 0.0


async def test_complete_payload_is_plain_chat_completions() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    await _complete(client, max_tokens=256, temperature=0.2)
    request = captured["request"]
    assert request.method == "POST"
    assert str(request.url) == f"{_BASE_URL}/chat/completions"
    assert captured["payload"] == {
        "model": _MODEL_ID,
        "messages": [{"role": "user", "content": "hola"}],
        "max_tokens": 256,
        "temperature": 0.2,
        "stream": False,
    }


async def test_base_url_trailing_slash_is_ignored() -> None:
    captured: dict[str, Any] = {}
    handler = _capturing(_load("completion_text.json"), captured)
    client = _client(handler, base_url=f"{_BASE_URL}/")
    await _complete(client)
    assert captured["request"].url.path == "/v1/chat/completions"


async def test_model_name_falls_back_to_provider_model_id() -> None:
    body = _completion({"role": "assistant", "content": "ok"})
    del body["model"]
    client = _client(lambda request: httpx.Response(200, json=body))
    assert (await _complete(client)).model_name == _MODEL_ID


async def test_complete_uses_configured_default_timeout() -> None:
    captured: dict[str, Any] = {}
    handler = _capturing(_load("completion_text.json"), captured)
    client = _client(handler, default_timeout_s=120.0)
    await _complete(client)
    assert captured["request"].extensions["timeout"]["read"] == 120.0


async def test_complete_explicit_timeout_overrides_default() -> None:
    captured: dict[str, Any] = {}
    handler = _capturing(_load("completion_text.json"), captured)
    client = _client(handler, default_timeout_s=120.0)
    await _complete(client, timeout_s=5.0)
    assert captured["request"].extensions["timeout"]["read"] == 5.0


# ---------- errores HTTP y de red ----------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, LlmAuthError),
        (403, LlmAuthError),
        (429, LlmRateLimitError),
        (400, LlmBadRequestError),
        (422, LlmBadRequestError),
        (404, LlmProviderRejectedError),
        (413, LlmProviderRejectedError),
        (500, LlmUnavailableError),
        (502, LlmUnavailableError),
        (503, LlmUnavailableError),
    ],
)
async def test_complete_http_error_mapping(status: int, expected: type[LlmError]) -> None:
    client = _client(lambda request: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(expected):
        await _complete(client)


async def test_complete_429_retry_after_seconds() -> None:
    response = httpx.Response(429, headers={"Retry-After": "7"})
    client = _client(lambda request: response)
    with pytest.raises(LlmRateLimitError) as excinfo:
        await _complete(client)
    assert excinfo.value.retry_after_s == 7.0


async def test_complete_429_retry_after_http_date() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    client = _client(lambda request: httpx.Response(429, headers={"Retry-After": when}))
    with pytest.raises(LlmRateLimitError) as excinfo:
        await _complete(client)
    assert excinfo.value.retry_after_s is not None
    assert 18.0 <= excinfo.value.retry_after_s <= 20.0


@pytest.mark.parametrize(
    "error",
    [
        {"message": "too long", "code": "context_length_exceeded"},
        {"message": "This model's maximum context length is 128000 tokens."},
    ],
)
async def test_complete_400_context_exceeded(error: dict[str, str]) -> None:
    client = _client(lambda request: httpx.Response(400, json={"error": error}))
    with pytest.raises(LlmContextOverflowError) as excinfo:
        await _complete(client)
    assert str(excinfo.value) == "contexto excedido: HTTP 400"


@pytest.mark.parametrize(
    ("raised", "expected", "rendered"),
    [
        (httpx.ReadTimeout, LlmTimeoutError, "timeout de inferencia LLM: timeout HTTP"),
        (httpx.ConnectError, LlmUnavailableError, "instancia LLM no disponible: connect error"),
        (httpx.ReadError, LlmUnavailableError, "instancia LLM no disponible: transport error"),
    ],
)
async def test_complete_network_errors_do_not_leak_host_or_key(
    raised: type[httpx.RequestError], expected: type[LlmError], rendered: str
) -> None:
    leaky = f"failed for {_BASE_URL} with Bearer {_API_KEY}"

    def handler(request: httpx.Request) -> httpx.Response:
        raise raised(leaky, request=request)

    client = _client(handler)
    with pytest.raises(expected) as excinfo:
        await _complete(client)
    assert str(excinfo.value) == rendered
    assert isinstance(excinfo.value.__cause__, raised)


async def test_auth_error_does_not_leak_provider_body() -> None:
    body = {"error": {"message": f"Incorrect API key provided: {_API_KEY[:8]}...cdef"}}
    client = _client(lambda request: httpx.Response(401, json=body))
    with pytest.raises(LlmAuthError) as excinfo:
        await _complete(client)
    assert str(excinfo.value) == "credencial rechazada por el proveedor LLM: HTTP 401"


# ---------- health ----------


async def test_health_ok_with_credential_and_short_timeout() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing({"data": [{"id": _MODEL_ID}]}, captured))
    health = await client.health()
    request = captured["request"]
    assert (health.healthy, health.model_name) == (True, "principal")
    assert request.method == "GET"
    assert str(request.url) == f"{_BASE_URL}/models"
    assert request.headers["Authorization"] == f"Bearer {_API_KEY}"
    assert request.extensions["timeout"]["read"] == 5.0


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_health_down_on_status(status: int) -> None:
    client = _client(lambda request: httpx.Response(status))
    assert (await client.health()).healthy is False


@pytest.mark.parametrize("raised", [httpx.ConnectError, httpx.ReadTimeout])
async def test_health_down_on_network_error(raised: type[httpx.RequestError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised("x", request=request)

    assert (await _client(handler).health()).healthy is False


# ---------- credencial: nunca en logs, repr ni excepciones ----------


async def test_key_never_reaches_logs_repr_or_errors(caplog: pytest.LogCaptureFixture) -> None:
    responses = iter(
        [
            httpx.Response(200, json=_load("completion_text.json")),
            httpx.Response(401, json={"error": {"message": f"bad key {_API_KEY}"}}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    client = _client(lambda request: next(responses))
    # El logger de httpx puede venir en WARNING (``build_llm_client`` lo sube con
    # ``LLM_BACKEND=api``): se baja a DEBUG junto con el root para capturar cada request.
    with caplog.at_level(logging.DEBUG), caplog.at_level(logging.DEBUG, logger="httpx"):
        await _complete(client)
        with pytest.raises(LlmAuthError) as excinfo:
            await _complete(client)
        await client.health()
    assert caplog.records  # la captura anda: httpx loguea cada request
    assert _API_KEY not in caplog.text
    assert _API_KEY not in str(excinfo.value)
    assert _API_KEY not in repr(client)
    assert repr(client) == "OpenAICompatClient(name='principal')"
    assert client.name == "principal"


# ---------- construcción ----------


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": " "},
        {"base_url": ""},
        {"api_key": SecretStr("  ")},
        {"model_map": {}},
        {"model_map": {_ALIAS: ""}},
        {"reasoning_effort_on": ""},
        {"reasoning_effort_off": " "},
    ],
)
def test_constructor_rejects_invalid_config(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"proveedor|reasoning_effort") as excinfo:
        _client(lambda request: httpx.Response(200), **overrides)
    assert _API_KEY not in str(excinfo.value)


async def test_aclose_closes_http_client() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    client = _client(lambda request: httpx.Response(200), http_client=http)
    await client.aclose()
    assert http.is_closed
    assert _HOST not in repr(client)


# ---------- con el ResilientClient: credencial sin reintento y respaldo ----------

Step = Callable[[httpx.Request], httpx.Response]


def _status(code: int, **headers: str) -> Step:
    return lambda request: httpx.Response(code, headers=headers, json={"error": {"message": "x"}})


def _raises(error: type[httpx.RequestError]) -> Step:
    def step(request: httpx.Request) -> httpx.Response:
        raise error("x", request=request)

    return step


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_load("completion_text.json"))


class _RecordingSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _provider(name: str, key: str, steps: list[Step], calls: list[str]) -> OpenAICompatClient:
    """Proveedor que responde ``steps`` en orden y anota su nombre en cada request."""
    pending = list(steps)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(name)
        assert request.headers["Authorization"] == f"Bearer {key}"
        return pending.pop(0)(request)

    return _client(handler, name=name, api_key=SecretStr(key))


def _resilient_pair(
    principal_steps: list[Step], backup_steps: list[Step], calls: list[str], **options: Any
) -> ResilientClient:
    principal = _provider("principal", _API_KEY, principal_steps, calls)
    backup = _provider("respaldo", _BACKUP_KEY, backup_steps, calls)
    return ResilientClient(build_provider_pool([principal, backup]), **options)


@pytest.mark.parametrize(
    ("principal_steps", "principal_calls", "error_name"),
    [
        ([_status(401)], 1, "LlmAuthError"),
        ([_status(403)], 1, "LlmAuthError"),
        ([_status(404)], 1, "LlmProviderRejectedError"),
        ([_status(429, **{"Retry-After": "120"})], 1, "LlmRateLimitError"),
        ([_status(503), _status(502), _status(500)], 3, "LlmUnavailableError"),
        ([_raises(httpx.ReadTimeout)] * 3, 3, "LlmTimeoutError"),
        ([_raises(httpx.ConnectError)] * 3, 3, "LlmUnavailableError"),
    ],
    ids=["401", "403", "404", "429-espera-larga", "5xx", "timeout", "red"],
)
async def test_failover_to_backup_provider(
    principal_steps: list[Step],
    principal_calls: int,
    error_name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[str] = []
    sleep = _RecordingSleep()
    resilient = _resilient_pair(principal_steps, [_ok], calls, sleep=sleep)

    with caplog.at_level(logging.DEBUG):
        result = await resilient.complete(model=_ALIAS, messages=_messages())

    assert result.text == "Hola, en que te puedo ayudar?"
    assert calls == ["principal"] * principal_calls + ["respaldo"]
    assert len(sleep.delays) == principal_calls - 1  # 401/403/404 y 429 con espera larga: 0
    assert f"llm: candidato principal descartado ({error_name})" in caplog.text
    assert _API_KEY not in caplog.text
    assert _BACKUP_KEY not in caplog.text


async def test_rate_limit_within_budget_waits_and_retries_principal() -> None:
    calls: list[str] = []
    sleep = _RecordingSleep()
    resilient = _resilient_pair([_status(429, **{"Retry-After": "2"}), _ok], [], calls, sleep=sleep)
    result = await resilient.complete(model=_ALIAS, messages=_messages())
    assert result.finish_reason == "stop"
    assert calls == ["principal", "principal"]
    assert sleep.delays == [2.0]


async def test_open_breaker_skips_principal() -> None:
    calls: list[str] = []
    resilient = _resilient_pair(
        [_status(503)],
        [_ok, _ok],
        calls,
        max_attempts=1,
        sleep=_RecordingSleep(),
        breaker_factory=lambda: CircuitBreaker(failure_threshold=1, recovery_timeout_s=999.0),
    )
    await resilient.complete(model=_ALIAS, messages=_messages())
    await resilient.complete(model=_ALIAS, messages=_messages())
    assert calls == ["principal", "respaldo", "respaldo"]


async def test_both_providers_rejected_degrades() -> None:
    calls: list[str] = []
    resilient = _resilient_pair([_status(401)], [_status(403)], calls, sleep=_RecordingSleep())
    result = await resilient.complete(model=_ALIAS, messages=_messages())
    assert result.finish_reason == "degraded"
    assert calls == ["principal", "respaldo"]


async def test_context_exceeded_is_not_sent_to_backup() -> None:
    body = {"error": {"message": "too long", "code": "context_length_exceeded"}}
    calls: list[str] = []
    resilient = _resilient_pair(
        [lambda request: httpx.Response(400, json=body)], [_ok], calls, sleep=_RecordingSleep()
    )
    with pytest.raises(LlmContextOverflowError):
        await resilient.complete(model=_ALIAS, messages=_messages())
    assert calls == ["principal"]
