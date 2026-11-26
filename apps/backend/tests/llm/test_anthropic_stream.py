"""Streaming de ``AnthropicClient`` con ``httpx.MockTransport`` (sin red, ADR-031 D9).

Los eventos SSE tienen la forma de la documentación de la Messages API, con textos
inventados: texto y razonamiento como deltas, ``tool_use`` armado con
``input_json_delta`` y emitido entero al cerrar el bloque (en la forma de fragmento
que junta ``OpenAIToolCallParser.accumulate``), el ``stop_reason`` del
``message_delta``, el evento ``error`` a mitad del stream y el status de error con el
body leído.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.llm_providers import AliasThinking, ThinkingMode
from app.llm.clients.parsers import OpenAIToolCallParser
from app.llm.clients.providers._anthropic_stream import AnthropicStreamDecoder
from app.llm.clients.providers.anthropic import AnthropicClient
from app.llm.errors import (
    LlmBadRequestError,
    LlmError,
    LlmRateLimitError,
    LlmSpendLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from app.llm.schemas import ChatMessage, CompletionChunk, ToolCall

_THINKING = {
    "agente": AliasThinking(
        on=ThinkingMode(type="adaptive", effort="low"),
        off=ThinkingMode(type="adaptive", effort="low"),
        max_tokens_floor=2048,
    )
}


def _sse(*events: dict[str, Any]) -> bytes:
    """Un stream SSE con ``event:`` y ``data:`` por evento, como lo manda la API."""
    lines = []
    for event in events:
        lines.append(f"event: {event['type']}")
        lines.append(f"data: {json.dumps(event)}")
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def _client(handler: Any) -> AnthropicClient:
    return AnthropicClient(
        name="claude",
        base_url="https://api.anthropic.test/v1",
        api_key=SecretStr("sk-test-anthropic-stream"),
        model_map={"agente": "modelo-agente-test"},
        thinking=_THINKING,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )


async def _collect(body: bytes, status: int = 200) -> tuple[list[CompletionChunk], dict]:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(status, content=body)

    client = _client(handler)
    chunks = [
        chunk
        async for chunk in client.stream(
            model="agente", messages=[ChatMessage(role="user", content="hola")]
        )
    ]
    return chunks, captured


def _start(index: int, block: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": block}


def _delta(index: int, delta: dict[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_delta", "index": index, "delta": delta}


def _stop(index: int) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


_MESSAGE_START = {"type": "message_start", "message": {"id": "msg_test", "content": []}}


async def test_stream_text_and_reasoning_deltas() -> None:
    body = _sse(
        _MESSAGE_START,
        _start(0, {"type": "thinking", "thinking": "", "signature": ""}),
        _delta(0, {"type": "thinking_delta", "thinking": "Pienso."}),
        _delta(0, {"type": "signature_delta", "signature": "firma"}),
        _stop(0),
        _start(1, {"type": "text", "text": ""}),
        {"type": "ping"},
        _delta(1, {"type": "text_delta", "text": "Hola, "}),
        _delta(1, {"type": "text_delta", "text": "¿qué hacemos?"}),
        _stop(1),
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {}},
        {"type": "message_stop"},
    )
    chunks, captured = await _collect(body)
    assert captured["payload"]["stream"] is True
    assert "".join(c.reasoning_delta or "" for c in chunks) == "Pienso."
    assert "".join(c.delta_text for c in chunks) == "Hola, ¿qué hacemos?"
    assert chunks[-1].finish_reason == "stop"
    assert all(c.tool_call_delta is None for c in chunks)


async def test_stream_tool_use_comes_out_whole_at_block_stop() -> None:
    body = _sse(
        _MESSAGE_START,
        _start(0, {"type": "tool_use", "id": "toolu_1", "name": "calendar__create_event"}),
        _delta(0, {"type": "input_json_delta", "partial_json": '{"title": '}),
        _delta(0, {"type": "input_json_delta", "partial_json": '"dentista"}'}),
        _stop(0),
        _start(1, {"type": "tool_use", "id": "toolu_2", "name": "task__create", "input": {}}),
        _stop(1),
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    )
    chunks, _ = await _collect(body)
    deltas = [c.tool_call_delta for c in chunks if c.tool_call_delta is not None]
    assert len(deltas) == 2
    assert chunks[-1].finish_reason == "tool_calls"
    # La forma es la de un fragmento de Chat Completions: la junta el parser de siempre.
    calls = OpenAIToolCallParser().accumulate(deltas)
    assert calls == [
        ToolCall(id="toolu_1", name="calendar.create_event", arguments={"title": "dentista"}),
        ToolCall(id="toolu_2", name="task.create", arguments={}),
    ]


async def test_stream_end_turn_with_tool_use_is_tool_calls() -> None:
    """Como sin streaming: un cierre normal con tool calls se informa como ``tool_calls``."""
    body = _sse(
        _start(0, {"type": "tool_use", "id": "t1", "name": "task__create", "input": {}}),
        _stop(0),
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}},
        {"type": "message_stop"},
    )
    chunks, _ = await _collect(body)
    assert chunks[-1].finish_reason == "tool_calls"


def test_decoder_keeps_the_complete_blocks() -> None:
    decoder = AnthropicStreamDecoder()
    for event in (
        _start(0, {"type": "thinking", "thinking": "", "signature": ""}),
        _delta(0, {"type": "thinking_delta", "thinking": "Pienso."}),
        _delta(0, {"type": "signature_delta", "signature": "fir"}),
        _delta(0, {"type": "signature_delta", "signature": "ma"}),
        _stop(0),
        _start(1, {"type": "tool_use", "id": "t1", "name": "task__create", "input": {}}),
        _delta(1, {"type": "input_json_delta", "partial_json": '{"a": 1}'}),
        _stop(1),
    ):
        decoder.feed(json.dumps(event))
    assert decoder.blocks == [
        {"type": "thinking", "thinking": "Pienso.", "signature": "firma"},
        {"type": "tool_use", "id": "t1", "name": "task__create", "input": {"a": 1}},
    ]


def test_decoder_ignores_what_is_not_json() -> None:
    assert AnthropicStreamDecoder().feed("no es json") is None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"type": "overloaded_error", "message": "x"}, LlmUnavailableError),
        ({"type": "api_error", "message": "x"}, LlmUnavailableError),
        ({"type": "timeout_error", "message": "x"}, LlmTimeoutError),
        ({"type": "rate_limit_error", "message": "x"}, LlmRateLimitError),
        (
            {
                "type": "rate_limit_error",
                "message": "x",
                "details": {"error_code": "enforced_spend_limit_reached"},
            },
            LlmSpendLimitError,
        ),
        ({"type": "invalid_request_error", "message": "x"}, LlmBadRequestError),
        ({"type": "otro_error", "message": "x"}, LlmError),
    ],
)
async def test_stream_error_event_maps_to_the_taxonomy(
    error: dict[str, Any], expected: type[LlmError]
) -> None:
    body = _sse(
        _MESSAGE_START,
        _start(0, {"type": "text", "text": ""}),
        _delta(0, {"type": "text_delta", "text": "Hola"}),
        {"type": "error", "error": error},
    )
    with pytest.raises(expected) as excinfo:
        await _collect(body)
    assert type(excinfo.value) is expected
    assert str(excinfo.value).endswith("error del proveedor en el stream")


async def test_stream_error_status_reads_the_body_to_label_the_spend_limit() -> None:
    body = json.dumps(
        {
            "type": "error",
            "error": {
                "type": "rate_limit_error",
                "message": "x",
                "details": {"error_code": "enforced_spend_limit_reached"},
            },
        }
    ).encode()
    with pytest.raises(LlmSpendLimitError):
        await _collect(body, status=429)


async def test_stream_stops_at_message_stop() -> None:
    """Lo que llegue después de ``message_stop`` no se lee."""
    body = _sse(
        _start(0, {"type": "text", "text": ""}),
        _delta(0, {"type": "text_delta", "text": "fin"}),
        {"type": "message_stop"},
        _delta(0, {"type": "text_delta", "text": " de más"}),
    )
    chunks, _ = await _collect(body)
    assert "".join(c.delta_text for c in chunks) == "fin"
