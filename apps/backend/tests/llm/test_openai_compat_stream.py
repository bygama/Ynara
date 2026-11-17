"""Streaming de ``OpenAICompatClient`` con ``httpx.MockTransport`` (sin red).

Porta los casos de streaming de ``test_vllm_client.py`` y agrega los del proveedor por
API: nombres de tools partidos entre deltas, razonamiento por canal e inline (con el
tag partido, y un ``<think>`` literal a mitad del texto), lo retenido que sale al
cerrar, los status de error con el body leído
(el contexto excedido también se detecta en streaming), un evento de error a mitad del
stream y un corte de red con el stream empezado.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.llm.clients.parsers import OpenAIToolCallParser
from app.llm.clients.providers.openai_compat import OpenAICompatClient
from app.llm.errors import (
    LlmAuthError,
    LlmContextOverflowError,
    LlmError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
    ModelNotServedError,
)
from app.llm.schemas import ChatMessage, CompletionChunk

_FIXTURES = Path(__file__).parent / "fixtures"
_BASE_URL = "https://proveedor.test/v1"
_API_KEY = "sk-test-0123456789abcdef"
_ALIAS = "qwen-3.5-9b"
_MODEL_ID = "org/qwen3.5-9b-instruct"
_SSE_HEADERS = {"content-type": "text/event-stream"}


def _client(handler: Any) -> OpenAICompatClient:
    return OpenAICompatClient(
        name="principal",
        base_url=_BASE_URL,
        api_key=SecretStr(_API_KEY),
        model_map={_ALIAS: _MODEL_ID},
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


def _sse(*events: dict[str, Any] | str) -> bytes:
    """Body SSE: cada evento como ``data: ...`` (un dict va como JSON)."""
    lines = [f"data: {e if isinstance(e, str) else json.dumps(e)}\n\n" for e in events]
    return "".join(lines).encode("utf-8")


def _delta(finish_reason: str | None = None, **delta: Any) -> dict[str, Any]:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}


def _tool_fragment(index: int, name: str | None = None, **extra: Any) -> dict[str, Any]:
    function: dict[str, Any] = {}
    if name is not None:
        function["name"] = name
    if "arguments" in extra:
        function["arguments"] = extra.pop("arguments")
    return {"index": index, "function": function, **extra}


def _serving(body: bytes, captured: dict[str, Any] | None = None) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["request"] = request
            captured["payload"] = json.loads(request.content)
        return httpx.Response(200, content=body, headers=_SSE_HEADERS)

    return handler


async def _collect(client: OpenAICompatClient, **kwargs: Any) -> list[CompletionChunk]:
    messages = [ChatMessage(role="user", content="hola")]
    return [chunk async for chunk in client.stream(model=_ALIAS, messages=messages, **kwargs)]


def _text(chunks: list[CompletionChunk]) -> str:
    return "".join(chunk.delta_text for chunk in chunks)


def _reasoning(chunks: list[CompletionChunk]) -> str:
    return "".join(chunk.reasoning_delta or "" for chunk in chunks)


def _accumulated_calls(chunks: list[CompletionChunk]) -> list[Any]:
    deltas = [chunk.tool_call_delta for chunk in chunks if chunk.tool_call_delta is not None]
    return OpenAIToolCallParser().accumulate(deltas)


# ---------- texto y payload ----------


async def test_stream_text_chunks() -> None:
    body = (_FIXTURES / "stream_text.sse").read_bytes()
    captured: dict[str, Any] = {}
    chunks = await _collect(_client(_serving(body, captured)))
    assert _text(chunks) == "Hola mundo"
    assert chunks[-1].finish_reason == "stop"
    assert all(chunk.reasoning_delta is None for chunk in chunks)
    assert captured["payload"]["stream"] is True
    assert captured["payload"]["model"] == _MODEL_ID
    assert captured["request"].headers["Authorization"] == f"Bearer {_API_KEY}"


async def test_stream_ignores_non_data_lines_and_stops_at_done() -> None:
    body = (
        b": keep-alive\n\n"
        b"event: ping\n\n"
        b"data: esto no es json\n\n"
        b'data: {"choices": [], "usage": {"prompt_tokens": 1}}\n\n'
        + _sse(_delta(content="Hola"), _delta("stop"), "[DONE]", _delta(content="despues"))
    )
    chunks = await _collect(_client(_serving(body)))
    assert [(c.delta_text, c.finish_reason) for c in chunks] == [("Hola", None), ("", "stop")]


async def test_stream_unknown_alias_raises_without_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, content=b"", headers=_SSE_HEADERS)

    client = _client(handler)
    with pytest.raises(ModelNotServedError):
        await _collect_alias(client, "desconocido")
    assert calls == []


async def _collect_alias(client: OpenAICompatClient, alias: str) -> list[CompletionChunk]:
    messages = [ChatMessage(role="user", content="hola")]
    return [chunk async for chunk in client.stream(model=alias, messages=messages)]


# ---------- tool calls ----------


async def test_stream_tool_call_deltas_accumulate() -> None:
    body = (_FIXTURES / "stream_tool_calls.sse").read_bytes()
    calls = _accumulated_calls(await _collect(_client(_serving(body))))
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("call_stream_1", "get_weather", {"city": "Rosario"})
    ]


async def test_stream_tool_name_split_between_deltas() -> None:
    body = _sse(
        _delta(tool_calls=[_tool_fragment(0, "calendar_", id="call_1", arguments="")]),
        _delta(tool_calls=[_tool_fragment(0, "_create_event", arguments='{"title": ')]),
        _delta(tool_calls=[_tool_fragment(0, arguments='"x"}')]),
        _delta("tool_calls"),
        "[DONE]",
    )
    chunks = await _collect(_client(_serving(body)))
    calls = _accumulated_calls(chunks)
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("call_1", "calendar.create_event", {"title": "x"})
    ]
    assert chunks[-1].finish_reason == "tool_calls"
    emitted = json.dumps([c.tool_call_delta for c in chunks if c.tool_call_delta])
    assert "__" not in emitted


async def test_stream_held_underscore_is_released_at_finish() -> None:
    body = _sse(
        _delta(tool_calls=[_tool_fragment(0, "rare_", id="call_1", arguments="{}")]),
        _delta("tool_calls"),
    )
    chunks = await _collect(_client(_serving(body)))
    assert [c.name for c in _accumulated_calls(chunks)] == ["rare_"]
    assert chunks[-1].tool_call_delta is not None


async def test_stream_parallel_tool_calls_keep_their_index() -> None:
    body = _sse(
        _delta(tool_calls=[_tool_fragment(0, "memory_", id="a"), _tool_fragment(1, "task_")]),
        _delta(tool_calls=[_tool_fragment(0, "_add"), _tool_fragment(1, "_list_tasks", id="b")]),
        _delta("tool_calls"),
    )
    calls = _accumulated_calls(await _collect(_client(_serving(body))))
    assert [(c.id, c.name) for c in calls] == [("a", "memory.add"), ("b", "task.list_tasks")]


async def test_stream_stop_with_tool_calls_is_reported_as_tool_calls() -> None:
    body = _sse(
        _delta(tool_calls=[_tool_fragment(0, "task__list_tasks", id="c", arguments="{}")]),
        _delta("stop"),
    )
    chunks = await _collect(_client(_serving(body)))
    assert chunks[-1].finish_reason == "tool_calls"


# ---------- razonamiento ----------


@pytest.mark.parametrize("key", ["reasoning_content", "reasoning"])
async def test_stream_reasoning_channel_deltas(key: str) -> None:
    body = _sse(
        _delta(**{key: "el usuario "}),
        _delta(**{key: "saluda"}),
        _delta(content="Hola"),
        _delta("stop"),
    )
    chunks = await _collect(_client(_serving(body)))
    assert (_text(chunks), _reasoning(chunks)) == ("Hola", "el usuario saluda")


async def test_stream_inline_think_split_across_deltas() -> None:
    fragments = ["<thi", "nk>\npien", "so</th", "ink>\n\nHo", "la"]
    body = _sse(*(_delta(content=f) for f in fragments), _delta("stop"))
    chunks = await _collect(_client(_serving(body)))
    assert (_text(chunks), _reasoning(chunks)) == ("Hola", "pienso")
    assert "<" not in _text(chunks)


async def test_stream_unclosed_think_is_reasoning() -> None:
    body = _sse(_delta(content="<think>pienso sin terminar"), _delta("length"))
    chunks = await _collect(_client(_serving(body)))
    assert (_text(chunks), _reasoning(chunks)) == ("", "pienso sin terminar")
    assert chunks[-1].finish_reason == "length"


async def test_stream_literal_think_mid_text_stays_text() -> None:
    """Un ``<think>`` que no cierra se retiene y sale como texto al terminar."""
    content = "Para HTML usá la etiqueta <think> como ejemplo y después seguí escribiendo."
    fragments = [content[:30], content[30:50], content[50:]]  # el tag llega partido
    body = _sse(*(_delta(content=f) for f in fragments), _delta("stop"))
    chunks = await _collect(_client(_serving(body)))
    assert (_text(chunks), _reasoning(chunks)) == (content, "")


async def test_stream_partial_tag_is_released_when_stream_ends_without_finish() -> None:
    body = _sse(_delta(content="Hola <thi"), "[DONE]")
    chunks = await _collect(_client(_serving(body)))
    assert [(c.delta_text, c.finish_reason) for c in chunks] == [("Hola ", None), ("<thi", None)]


# ---------- errores ----------


async def _streamed_body(data: bytes) -> AsyncIterator[bytes]:
    yield data


@pytest.mark.parametrize(
    ("status", "body", "headers", "expected"),
    [
        (401, {"error": {"message": "bad key"}}, {}, LlmAuthError),
        (403, {"error": {"message": "forbidden"}}, {}, LlmAuthError),
        (429, {"error": {"message": "slow"}}, {"Retry-After": "3"}, LlmRateLimitError),
        (
            400,
            {"error": {"message": "too long", "code": "context_length_exceeded"}},
            {},
            LlmContextOverflowError,
        ),
        (503, {"error": {"message": "down"}}, {}, LlmUnavailableError),
    ],
)
async def test_stream_error_status_reads_body_and_maps(
    status: int, body: dict[str, Any], headers: dict[str, str], expected: type[LlmError]
) -> None:
    """El body del error llega como stream: el cliente lo lee para elegir el tipo."""

    def handler(request: httpx.Request) -> httpx.Response:
        content = _streamed_body(json.dumps(body).encode("utf-8"))
        return httpx.Response(status, content=content, headers=headers)

    with pytest.raises(expected) as excinfo:
        await _collect(_client(handler))
    assert str(excinfo.value).endswith(f"HTTP {status}")
    if isinstance(excinfo.value, LlmRateLimitError):
        assert excinfo.value.retry_after_s == 3.0


async def test_stream_error_event_after_200_is_llm_error() -> None:
    error = {"error": {"message": f"upstream failed for key {_API_KEY}", "code": 502}}
    body = _sse(_delta(content="Hola"), error)
    received: list[CompletionChunk] = []
    messages = [ChatMessage(role="user", content="hola")]
    with pytest.raises(LlmError) as excinfo:
        async for chunk in _client(_serving(body)).stream(model=_ALIAS, messages=messages):
            received.append(chunk)
    assert _text(received) == "Hola"
    assert type(excinfo.value) is LlmError
    assert str(excinfo.value) == "error de inferencia LLM: error del proveedor en el stream"


async def test_stream_read_error_after_first_chunk_is_unavailable() -> None:
    async def body() -> AsyncIterator[bytes]:
        yield _sse(_delta(content="Hola"))
        raise httpx.ReadError(f"connection reset by {_BASE_URL}")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body(), headers=_SSE_HEADERS)

    received: list[CompletionChunk] = []
    messages = [ChatMessage(role="user", content="hola")]
    with pytest.raises(LlmUnavailableError) as excinfo:
        async for chunk in _client(handler).stream(model=_ALIAS, messages=messages):
            received.append(chunk)
    assert _text(received) == "Hola"
    assert str(excinfo.value) == "instancia LLM no disponible: transport error"


@pytest.mark.parametrize(
    ("raised", "expected", "rendered"),
    [
        (httpx.ConnectTimeout, LlmTimeoutError, "timeout de inferencia LLM: timeout HTTP"),
        (httpx.ConnectError, LlmUnavailableError, "instancia LLM no disponible: connect error"),
    ],
)
async def test_stream_network_errors_do_not_leak_host(
    raised: type[httpx.RequestError], expected: type[LlmError], rendered: str
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise raised(f"All connection attempts failed for {_BASE_URL}", request=request)

    with pytest.raises(expected) as excinfo:
        await _collect(_client(handler))
    assert str(excinfo.value) == rendered
    assert isinstance(excinfo.value.__cause__, raised)
