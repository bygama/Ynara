"""Wire de ``OpenAICompatClient.complete()``: tools, razonamiento y respuestas raras.

Con ``httpx.MockTransport`` (sin red): los nombres de tools en el request y en la
respuesta (``calendar.create_event`` <-> ``calendar__create_event``), el historial con
tool calls, ``reasoning_effort`` según la config del proveedor, el razonamiento por
canal o inline, y las respuestas que cada proveedor completa a su manera. El contrato,
los errores y la credencial están en ``test_openai_compat_client.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.llm.clients.providers.openai_compat import OpenAICompatClient
from app.llm.errors import LlmBadRequestError, LlmError, ToolParsingError
from app.llm.schemas import ChatMessage, ProviderBlocks, ToolCall, ToolSpec

_FIXTURES = Path(__file__).parent / "fixtures"
_BASE_URL = "https://proveedor.test/v1"
_ALIAS = "agente"
_MODEL_ID = "org/agent-model"

Handler = Any


def _load(name: str) -> Any:
    return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _client(handler: Handler, **overrides: Any) -> OpenAICompatClient:
    kwargs: dict[str, Any] = {
        "name": "principal",
        "base_url": _BASE_URL,
        "api_key": SecretStr("sk-test-wire"),
        "model_map": {_ALIAS: _MODEL_ID},
        "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    }
    kwargs.update(overrides)
    return OpenAICompatClient(**kwargs)


def _capturing(body: Any, captured: dict[str, Any]) -> Handler:
    """Handler que guarda el payload del request y responde ``body``."""

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json=body)

    return handler


def _completion(message: dict[str, Any], **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": _MODEL_ID,
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    body.update(extra)
    return body


async def _complete(client: OpenAICompatClient, **kwargs: Any) -> Any:
    messages = [ChatMessage(role="user", content="hola")]
    return await client.complete(model=_ALIAS, messages=messages, **kwargs)


# ---------- tools: nombres en el wire ----------


_CREATE_EVENT = ToolSpec(
    name="calendar.create_event",
    description="Crea un evento",
    parameters={"type": "object", "properties": {"title": {"type": "string"}}},
)


async def test_complete_encodes_tool_definitions_with_api_names() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    await _complete(client, tools=[_CREATE_EVENT])
    payload = captured["payload"]
    assert payload["tool_choice"] == "auto"
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "calendar__create_event",
                "description": "Crea un evento",
                "parameters": _CREATE_EVENT.parameters,
            },
        }
    ]


async def test_complete_decodes_tool_call_names() -> None:
    message = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "calendar__create_event", "arguments": '{"title": "x"}'},
            },
            {"type": "function", "function": {"name": "task__list_tasks", "arguments": ""}},
        ],
    }
    body = _completion(message)
    body["choices"][0]["finish_reason"] = "tool_calls"
    client = _client(lambda request: httpx.Response(200, json=body))
    result = await _complete(client, tools=[_CREATE_EVENT])
    assert result.tool_calls == [
        ToolCall(id="call_1", name="calendar.create_event", arguments={"title": "x"}),
        # Sin id, el parser usa el nombre: el interno, no el del wire.
        ToolCall(id="task.list_tasks", name="task.list_tasks", arguments={}),
    ]
    assert result.finish_reason == "tool_calls"


async def test_complete_multi_tool_call_fixture() -> None:
    body = _load("completion_multi_tool_call.json")
    client = _client(lambda request: httpx.Response(200, json=body))
    result = await _complete(client)
    assert [call.name for call in result.tool_calls] == ["get_weather", "create_reminder"]


async def test_complete_stop_with_tool_calls_is_reported_as_tool_calls() -> None:
    """Un ``"stop"`` con tool calls haría que el tool loop no las ejecute."""
    body = _load("completion_tool_call.json")
    body["choices"][0]["finish_reason"] = "stop"
    client = _client(lambda request: httpx.Response(200, json=body))
    result = await _complete(client)
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].arguments == {"city": "Buenos Aires", "units": "celsius"}


async def test_complete_encodes_history_with_api_names() -> None:
    """El assistant re-envía sus tool calls y el ``role="tool"`` correlaciona (wire API)."""
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    messages = [
        ChatMessage(role="system", content="sos un asistente"),
        ChatMessage(role="user", content="recordame algo"),
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="call_1", name="reminder.set", arguments={"text": "pan"})],
        ),
        ChatMessage(role="tool", tool_call_id="call_1", name="reminder.set", content="{}"),
    ]
    await client.complete(model=_ALIAS, messages=messages)
    wire = captured["payload"]["messages"]
    assert "tool_calls" not in wire[0]
    assert wire[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "reminder__set", "arguments": '{"text": "pan"}'},
            }
        ],
    }
    assert wire[3] == {
        "role": "tool",
        "content": "{}",
        "tool_call_id": "call_1",
        "name": "reminder__set",
    }


async def test_complete_omits_empty_tool_calls_from_payload() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    messages = [
        ChatMessage(role="user", content="hola"),
        ChatMessage(role="assistant", content="respuesta sin tools", tool_calls=[]),
    ]
    await client.complete(model=_ALIAS, messages=messages)
    assert "tool_calls" not in captured["payload"]["messages"][1]


async def test_history_with_invented_tool_name_is_sent_as_is() -> None:
    """Un nombre que inventó el modelo vuelve al proveedor en vez de cortar el turno acá."""
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    invented = ToolCall(id="call_9", name="buscar clima", arguments={})
    messages = [
        ChatMessage(role="assistant", content=None, tool_calls=[invented]),
        ChatMessage(role="tool", tool_call_id="call_9", name="buscar clima", content="{}"),
    ]
    await client.complete(model=_ALIAS, messages=messages)
    wire = captured["payload"]["messages"]
    assert wire[0]["tool_calls"][0]["function"]["name"] == "buscar clima"
    assert wire[1]["name"] == "buscar clima"


async def test_unmappable_tool_definition_is_bad_request_without_request() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=_load("completion_text.json"))

    client = _client(handler)
    bad = ToolSpec(name="memory__search", description="x", parameters={})
    with pytest.raises(LlmBadRequestError):
        await _complete(client, tools=[bad])
    assert calls == []


async def test_complete_propagates_tool_parsing_error() -> None:
    body = _load("completion_bad_arguments.json")
    client = _client(lambda request: httpx.Response(200, json=body))
    with pytest.raises(ToolParsingError):
        await _complete(client)


# ---------- razonamiento ----------


@pytest.mark.parametrize(
    ("effort_on", "effort_off", "thinking", "expected"),
    [
        (None, None, True, None),
        (None, None, False, None),
        ("medium", None, True, "medium"),
        ("medium", None, False, None),
        ("medium", None, None, None),
        ("medium", "none", False, "none"),
        ("high", "minimal", True, "high"),
        ("high", "minimal", None, None),
    ],
)
async def test_reasoning_effort_only_when_configured(
    effort_on: str | None, effort_off: str | None, thinking: bool | None, expected: str | None
) -> None:
    captured: dict[str, Any] = {}
    client = _client(
        _capturing(_load("completion_text.json"), captured),
        reasoning_effort_on=effort_on,
        reasoning_effort_off=effort_off,
    )
    await _complete(client, thinking=thinking)
    payload = captured["payload"]
    assert payload.get("reasoning_effort") == expected
    assert ("reasoning_effort" in payload) is (expected is not None)
    assert "chat_template_kwargs" not in payload


@pytest.mark.parametrize("key", ["reasoning_content", "reasoning"])
async def test_complete_reads_reasoning_channel(key: str) -> None:
    message = {"role": "assistant", "content": "Hola", key: "el usuario saluda"}
    client = _client(lambda request: httpx.Response(200, json=_completion(message)))
    result = await _complete(client)
    assert (result.text, result.reasoning) == ("Hola", "el usuario saluda")


async def test_complete_splits_inline_think() -> None:
    message = {"role": "assistant", "content": "<think>\nsaluda\n</think>\n\nHola"}
    client = _client(lambda request: httpx.Response(200, json=_completion(message)))
    result = await _complete(client)
    assert (result.text, result.reasoning) == ("Hola", "saluda")


async def test_complete_channel_wins_over_inline_think() -> None:
    message = {
        "role": "assistant",
        "content": "<think>inline</think>Hola",
        "reasoning_content": "canal",
    }
    client = _client(lambda request: httpx.Response(200, json=_completion(message)))
    result = await _complete(client)
    assert (result.text, result.reasoning) == ("Hola", "canal")


async def test_complete_ignores_non_string_reasoning() -> None:
    message = {"role": "assistant", "content": "Hola", "reasoning": {"summary": []}}
    client = _client(lambda request: httpx.Response(200, json=_completion(message)))
    assert (await _complete(client)).reasoning is None


# ---------- respuestas que cada proveedor arma a su manera ----------


async def test_complete_content_as_text_parts() -> None:
    content = [
        {"type": "text", "text": "Hola "},
        {"type": "image_url", "image_url": {"url": "https://x"}},
        {"type": "text", "text": "mundo"},
    ]
    body = _completion({"role": "assistant", "content": content})
    client = _client(lambda request: httpx.Response(200, json=body))
    assert (await _complete(client)).text == "Hola mundo"


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (None, (0, 0)),
        ({}, (0, 0)),
        ({"prompt_tokens": None, "completion_tokens": None}, (0, 0)),
        ({"prompt_tokens": 12.0, "completion_tokens": 3}, (12, 3)),
        ({"prompt_tokens": True, "completion_tokens": "7"}, (0, 0)),
    ],
)
async def test_complete_usage_is_defensive(usage: Any, expected: tuple[int, int]) -> None:
    body = _completion({"role": "assistant", "content": "ok"}, usage=usage)
    client = _client(lambda request: httpx.Response(200, json=body))
    result = await _complete(client)
    assert (result.prompt_tokens, result.completion_tokens) == expected


async def test_complete_null_finish_reason_defaults_to_stop() -> None:
    body = _completion({"role": "assistant", "content": "ok"})
    body["choices"][0]["finish_reason"] = None
    client = _client(lambda request: httpx.Response(200, json=body))
    assert (await _complete(client)).finish_reason == "stop"


@pytest.mark.parametrize(
    ("response", "rendered"),
    [
        (httpx.Response(200, text="<html>gateway</html>"), "respuesta malformada"),
        (httpx.Response(200, json=["no", "es", "un", "objeto"]), "respuesta malformada"),
        (httpx.Response(200, json={"choices": []}), "respuesta sin choices"),
        (httpx.Response(200, json={"choices": "x"}), "respuesta sin choices"),
        (
            httpx.Response(200, json=_completion({"role": "assistant", "content": 42})),
            "respuesta malformada",
        ),
    ],
)
async def test_complete_malformed_body_is_llm_error(
    response: httpx.Response, rendered: str
) -> None:
    client = _client(lambda request: response)
    with pytest.raises(LlmError) as excinfo:
        await _complete(client)
    assert type(excinfo.value) is LlmError
    assert str(excinfo.value) == f"error de inferencia LLM: {rendered}"


# ---------- tool_choice y system partido (ADR-031) ----------


async def test_complete_sends_tool_choice_none_with_the_same_tools() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    await _complete(client, tools=[_CREATE_EVENT], tool_choice="none")
    payload = captured["payload"]
    assert payload["tool_choice"] == "none"
    assert payload["tools"][0]["function"]["name"] == "calendar__create_event"


async def test_complete_without_tools_sends_no_tool_choice() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    await _complete(client, tool_choice="none")
    assert "tool_choice" not in captured["payload"]
    assert "tools" not in captured["payload"]


async def test_consecutive_system_messages_are_sent_joined() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    messages = [
        ChatMessage(role="system", content="Parte fija."),
        ChatMessage(role="system", content="Parte variable."),
        ChatMessage(role="user", content="hola"),
    ]
    await client.complete(model=_ALIAS, messages=messages)
    assert captured["payload"]["messages"] == [
        {"role": "system", "content": "Parte fija.\n\nParte variable."},
        {"role": "user", "content": "hola"},
    ]


async def test_provider_blocks_and_is_error_do_not_reach_the_wire() -> None:
    captured: dict[str, Any] = {}
    client = _client(_capturing(_load("completion_text.json"), captured))
    call = ToolCall(id="call_1", name="calendar.create_event", arguments={"title": "x"})
    messages = [
        ChatMessage(role="user", content="hola"),
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[call],
            provider_blocks=ProviderBlocks(provider="otro", blocks=[{"type": "thinking"}]),
        ),
        ChatMessage(
            role="tool",
            tool_call_id="call_1",
            name="calendar.create_event",
            content='{"error": {"code": "x", "message": "y"}}',
            is_error=True,
        ),
    ]
    await client.complete(model=_ALIAS, messages=messages)
    sent = captured["payload"]["messages"]
    assert all("provider_blocks" not in m and "is_error" not in m for m in sent)
    assert "thinking" not in json.dumps(sent)
