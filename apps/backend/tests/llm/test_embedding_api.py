"""Contract tests de ``ApiEmbeddingClient`` con ``httpx.MockTransport`` (sin red).

Cubren el wire de la API de Embeddings compatible con OpenAI (payload, credencial
``Bearer``, orden por ``index``, timeout), los lotes de hasta ``batch_size`` textos, la
dimensión exacta de cada vector (sin recortar ni rellenar), las respuestas malformadas,
el mapeo de errores HTTP y de red que comparte con el cliente de chat (401/403, 429 con
``Retry-After``), la salud por ``GET /models`` y que ni la credencial, ni la URL, ni los
textos lleguen a los logs, al ``repr`` o a las excepciones.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.constants import DEFAULT_EMBEDDING_BATCH_SIZE, EMBEDDING_DIM
from app.core.llm_protocol import EmbeddingClientProtocol
from app.llm.clients.embedding import EmbeddingClient
from app.llm.clients.providers.embeddings import ApiEmbeddingClient
from app.llm.errors import (
    EmbeddingDimensionError,
    LlmAuthError,
    LlmBadRequestError,
    LlmContextOverflowError,
    LlmError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmTransientError,
    LlmUnavailableError,
)

_BASE_URL = "https://embeddings.proveedor.test/v1"
_HOST = "embeddings.proveedor.test"
_API_KEY = "sk-test-embeddings-0123456789abcdef"
_MODEL = "BAAI/bge-m3"
# Texto sintético con forma de dato de usuario: no puede aparecer en logs ni errores.
_TEXT = "el martes tengo turno con la psicóloga"

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler, **overrides: Any) -> ApiEmbeddingClient:
    kwargs: dict[str, Any] = {
        "base_url": _BASE_URL,
        "api_key": SecretStr(_API_KEY),
        "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    }
    kwargs.update(overrides)
    return ApiEmbeddingClient(**kwargs)


def _vector(seed: float) -> list[float]:
    """Un vector de ``EMBEDDING_DIM`` valores que se distingue por ``seed``."""
    return [seed] * EMBEDDING_DIM


def _body(vectors: list[list[float]]) -> dict[str, Any]:
    data = [{"object": "embedding", "index": i, "embedding": v} for i, v in enumerate(vectors)]
    return {"object": "list", "data": data, "model": _MODEL, "usage": {"prompt_tokens": 4}}


def _ok(vectors: list[list[float]]) -> httpx.Response:
    return httpx.Response(200, json=_body(vectors))


def _seeded(requests: list[httpx.Request]) -> Handler:
    """Handler que guarda cada request y responde, por texto ``"t<n>"``, ``_vector(n)``."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        texts = json.loads(request.content)["input"]
        return _ok([_vector(float(text.removeprefix("t"))) for text in texts])

    return handler


def _inputs(requests: list[httpx.Request]) -> list[list[str]]:
    return [json.loads(request.content)["input"] for request in requests]


# ---------- contrato y wire ----------


def test_satisfies_embedding_protocols() -> None:
    client = _client(lambda request: _ok([]))
    assert isinstance(client, EmbeddingClient)
    assert isinstance(client, EmbeddingClientProtocol)


async def test_embed_posts_openai_payload_with_bearer_credential() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _ok([_vector(0.1), _vector(0.2)])

    result = await _client(handler, model="org/bge-m3").embed(["hola", "chau"])

    [request] = requests
    assert request.method == "POST"
    assert str(request.url) == f"{_BASE_URL}/embeddings"
    assert request.headers["Authorization"] == f"Bearer {_API_KEY}"
    assert json.loads(request.content) == {
        "model": "org/bge-m3",
        "input": ["hola", "chau"],
        "encoding_format": "float",
    }
    assert result == [_vector(0.1), _vector(0.2)]


async def test_default_model_is_bge_m3() -> None:
    requests: list[httpx.Request] = []
    client = _client(_seeded(requests))
    await client.embed(["t0"])
    assert json.loads(requests[0].content)["model"] == "BAAI/bge-m3"
    assert client.model == "BAAI/bge-m3"


async def test_embed_orders_vectors_by_index() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        data = [
            {"index": 1, "embedding": _vector(9.0)},
            {"index": 0, "embedding": _vector(1.0)},
        ]
        return httpx.Response(200, json={"data": data})

    assert await _client(handler).embed(["a", "b"]) == [_vector(1.0), _vector(9.0)]


async def test_integer_values_come_back_as_floats() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [1] * EMBEDDING_DIM}]})

    [vector] = await _client(handler).embed(["a"])
    assert all(isinstance(value, float) for value in vector)


async def test_embed_uses_configured_timeout() -> None:
    requests: list[httpx.Request] = []
    await _client(_seeded(requests), default_timeout_s=12.5).embed(["t0"])
    assert requests[0].extensions["timeout"]["read"] == 12.5


async def test_base_url_trailing_slash_is_ignored() -> None:
    requests: list[httpx.Request] = []
    await _client(_seeded(requests), base_url=f"{_BASE_URL}/").embed(["t0"])
    assert str(requests[0].url) == f"{_BASE_URL}/embeddings"


async def test_empty_input_makes_no_request() -> None:
    requests: list[httpx.Request] = []
    assert await _client(_seeded(requests)).embed([]) == []
    assert requests == []


# ---------- lotes ----------


async def test_embed_splits_into_batches_and_keeps_order() -> None:
    requests: list[httpx.Request] = []
    texts = [f"t{n}" for n in range(5)]

    result = await _client(_seeded(requests), batch_size=2).embed(texts)

    assert _inputs(requests) == [["t0", "t1"], ["t2", "t3"], ["t4"]]
    assert result == [_vector(float(n)) for n in range(5)]


async def test_default_batch_size_caps_each_request() -> None:
    requests: list[httpx.Request] = []
    texts = [f"t{n}" for n in range(DEFAULT_EMBEDDING_BATCH_SIZE * 2 + 1)]

    result = await _client(_seeded(requests)).embed(texts)

    assert [len(batch) for batch in _inputs(requests)] == [
        DEFAULT_EMBEDDING_BATCH_SIZE,
        DEFAULT_EMBEDDING_BATCH_SIZE,
        1,
    ]
    assert result == [_vector(float(n)) for n in range(len(texts))]


async def test_failed_batch_fails_the_whole_embed() -> None:
    requests: list[httpx.Request] = []
    seeded = _seeded(requests)

    def handler(request: httpx.Request) -> httpx.Response:
        if requests:
            requests.append(request)
            return httpx.Response(503)
        return seeded(request)

    with pytest.raises(LlmUnavailableError):
        await _client(handler, batch_size=2).embed(["t0", "t1", "t2"])
    assert len(requests) == 2


# ---------- dimensión ----------


@pytest.mark.parametrize("received", [EMBEDDING_DIM - 1, EMBEDDING_DIM + 1, 768])
async def test_wrong_dimension_raises_without_truncating_or_padding(received: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5] * received}]})

    with pytest.raises(EmbeddingDimensionError) as excinfo:
        await _client(handler).embed([_TEXT])
    assert (excinfo.value.expected, excinfo.value.received) == (EMBEDDING_DIM, received)
    assert str(excinfo.value) == (
        f"dimension de embedding inesperada: {received} en vez de {EMBEDDING_DIM}"
    )


async def test_one_vector_with_wrong_dimension_fails_the_batch() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _ok([_vector(0.1), [0.2] * 768, _vector(0.3)])

    with pytest.raises(EmbeddingDimensionError):
        await _client(handler).embed(["a", "b", "c"])


# ---------- respuestas malformadas ----------


def _nan_response(request: httpx.Request) -> httpx.Response:
    vector = [0.0] * EMBEDDING_DIM
    vector[3] = float("nan")
    content = json.dumps({"data": [{"index": 0, "embedding": vector}]}).encode()
    return httpx.Response(200, content=content, headers={"Content-Type": "application/json"})


@pytest.mark.parametrize(
    "body",
    [
        {"model": _MODEL},
        {"data": "x"},
        [],
        {"data": [{"index": 0, "embedding": _vector(0.1)}]},
        {"data": [{"index": 0, "embedding": _vector(0.1)}] * 2},
        {"data": [{"index": 0, "embedding": _vector(0.1)}, {"index": 2, "embedding": _vector(0)}]},
        {"data": [{"index": False, "embedding": _vector(0.1)}, {"index": 1, "embedding": [0]}]},
        {"data": [{"embedding": _vector(0.1)}, {"index": 1, "embedding": _vector(0.2)}]},
        {"data": [{"index": 0, "embedding": "AAAA"}, {"index": 1, "embedding": _vector(0.2)}]},
        {"data": [{"index": 0, "embedding": ["0.1"] * EMBEDDING_DIM}, {"index": 1}]},
        {"data": [{"index": 0, "embedding": [None] * EMBEDDING_DIM}, {"index": 1}]},
        {"data": ["x", "y"]},
    ],
)
async def test_malformed_body_raises_llm_error(body: Any) -> None:
    client = _client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(LlmTransientError) as excinfo:
        await client.embed(["a", "b"])
    assert not isinstance(excinfo.value, EmbeddingDimensionError)
    assert str(excinfo.value) == (
        "falla transitoria del proveedor LLM: respuesta de embeddings malformada"
    )


async def test_non_finite_values_are_rejected() -> None:
    with pytest.raises(LlmTransientError, match="respuesta de embeddings malformada"):
        await _client(_nan_response).embed(["a"])


async def test_non_json_body_raises_llm_error() -> None:
    client = _client(lambda request: httpx.Response(200, content=b"<html>proxy</html>"))
    with pytest.raises(LlmTransientError, match="respuesta de embeddings malformada"):
        await client.embed(["a"])


# ---------- errores HTTP y de red (mismo mapeo que el cliente de chat) ----------


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
        (408, LlmTransientError),
        (425, LlmTransientError),
        (500, LlmUnavailableError),
        (502, LlmUnavailableError),
        (503, LlmUnavailableError),
    ],
)
async def test_http_error_mapping(status: int, expected: type[LlmError]) -> None:
    client = _client(lambda request: httpx.Response(status, json={"error": {"message": "x"}}))
    with pytest.raises(expected):
        await client.embed(["a"])


async def test_401_is_a_credential_error() -> None:
    body = {"error": {"message": f"Incorrect API key provided: {_API_KEY[:10]}...cdef"}}
    client = _client(lambda request: httpx.Response(401, json=body))
    with pytest.raises(LlmAuthError) as excinfo:
        await client.embed([_TEXT])
    assert str(excinfo.value) == "credencial rechazada por el proveedor LLM: HTTP 401"


async def test_429_carries_retry_after_seconds() -> None:
    client = _client(lambda request: httpx.Response(429, headers={"Retry-After": "7"}))
    with pytest.raises(LlmRateLimitError) as excinfo:
        await client.embed(["a"])
    assert excinfo.value.retry_after_s == 7.0


async def test_429_carries_retry_after_http_date() -> None:
    when = format_datetime(datetime.now(UTC) + timedelta(seconds=20), usegmt=True)
    client = _client(lambda request: httpx.Response(429, headers={"Retry-After": when}))
    with pytest.raises(LlmRateLimitError) as excinfo:
        await client.embed(["a"])
    assert excinfo.value.retry_after_s is not None
    assert 18.0 <= excinfo.value.retry_after_s <= 20.0


async def test_429_without_retry_after() -> None:
    client = _client(lambda request: httpx.Response(429))
    with pytest.raises(LlmRateLimitError) as excinfo:
        await client.embed(["a"])
    assert excinfo.value.retry_after_s is None


async def test_input_too_long_is_context_overflow() -> None:
    body = {"error": {"message": "This model's maximum context length is 8192 tokens."}}
    client = _client(lambda request: httpx.Response(400, json=body))
    with pytest.raises(LlmContextOverflowError):
        await client.embed(["a"])


async def test_error_does_not_echo_the_provider_body_or_the_text() -> None:
    body = {"error": {"message": f"input invalido: {_TEXT}"}}
    client = _client(lambda request: httpx.Response(400, json=body))
    with pytest.raises(LlmBadRequestError) as excinfo:
        await client.embed([_TEXT])
    assert _TEXT not in str(excinfo.value)
    assert str(excinfo.value) == "request invalido al LLM: HTTP 400"


@pytest.mark.parametrize(
    ("raised", "expected", "rendered"),
    [
        (httpx.ReadTimeout, LlmTimeoutError, "timeout de inferencia LLM: timeout HTTP"),
        (httpx.ConnectError, LlmUnavailableError, "instancia LLM no disponible: connect error"),
        (httpx.ReadError, LlmUnavailableError, "instancia LLM no disponible: transport error"),
    ],
)
async def test_network_errors_do_not_leak_host_or_key(
    raised: type[httpx.RequestError], expected: type[LlmError], rendered: str
) -> None:
    leaky = f"failed for {_BASE_URL} with Bearer {_API_KEY}"

    def handler(request: httpx.Request) -> httpx.Response:
        raise raised(leaky, request=request)

    with pytest.raises(expected) as excinfo:
        await _client(handler).embed(["a"])
    assert str(excinfo.value) == rendered
    assert isinstance(excinfo.value.__cause__, raised)


# ---------- salud ----------


async def test_health_lists_models_without_spending_tokens() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"data": [{"id": _MODEL}]})

    health = await _client(handler).health()

    [request] = requests
    assert (health.healthy, health.model_name) == (True, _MODEL)
    assert request.method == "GET"
    assert str(request.url) == f"{_BASE_URL}/models"
    assert request.headers["Authorization"] == f"Bearer {_API_KEY}"
    assert request.extensions["timeout"]["read"] == 5.0


@pytest.mark.parametrize("status", [401, 403, 429, 503])
async def test_health_down_on_status(status: int) -> None:
    assert (await _client(lambda request: httpx.Response(status)).health()).healthy is False


@pytest.mark.parametrize("raised", [httpx.ConnectError, httpx.ReadTimeout])
async def test_health_down_on_network_error(raised: type[httpx.RequestError]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised("x", request=request)

    assert (await _client(handler).health()).healthy is False


# ---------- credencial y textos: nunca en logs, repr ni excepciones ----------


async def test_key_and_texts_never_reach_logs_repr_or_errors(
    caplog: pytest.LogCaptureFixture,
) -> None:
    responses = iter(
        [
            _ok([_vector(0.1)]),
            httpx.Response(401, json={"error": {"message": f"bad key {_API_KEY} for {_TEXT}"}}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    client = _client(lambda request: next(responses))
    # El logger de httpx puede venir en WARNING (la factory lo sube con el backend
    # ``api``): se baja a DEBUG junto con el root para capturar cada request.
    with caplog.at_level(logging.DEBUG), caplog.at_level(logging.DEBUG, logger="httpx"):
        await client.embed([_TEXT])
        with pytest.raises(LlmAuthError) as excinfo:
            await client.embed([_TEXT])
        await client.health()
    assert caplog.records  # la captura anda: httpx loguea cada request
    for secret in (_API_KEY, _TEXT):
        assert secret not in caplog.text
        assert secret not in str(excinfo.value)
        assert secret not in repr(client)
    assert repr(client) == "ApiEmbeddingClient(model='BAAI/bge-m3')"
    assert _HOST not in repr(client)


# ---------- construcción ----------


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"base_url": ""}, "URL base"),
        ({"base_url": "http://embeddings.proveedor.test/v1"}, "https"),
        ({"api_key": SecretStr("  ")}, "credencial"),
        ({"model": " "}, "modelo"),
        ({"batch_size": 0}, "al menos un texto"),
    ],
)
def test_constructor_rejects_invalid_config(overrides: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message) as excinfo:
        _client(lambda request: _ok([]), **overrides)
    assert _API_KEY not in str(excinfo.value)


def test_constructor_accepts_http_on_loopback() -> None:
    client = _client(lambda request: _ok([]), base_url="http://localhost:8080/v1")
    assert client.model == "BAAI/bge-m3"


async def test_aclose_closes_http_client() -> None:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: _ok([])))
    client = _client(lambda request: _ok([]), http_client=http)
    await client.aclose()
    assert http.is_closed
