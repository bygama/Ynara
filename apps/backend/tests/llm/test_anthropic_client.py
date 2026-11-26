"""Contract tests de ``AnthropicClient`` con ``httpx.MockTransport`` (sin red, ADR-031).

Datos sintéticos: los bodies de respuesta tienen la forma de los ejemplos de la
documentación de la Messages API, con textos inventados. Cubren el request (headers,
razonamiento por alias, piso de ``max_tokens``, system con caché, tools, historial con
bloques reenviados o rearmados, resultados agrupados), la respuesta (texto,
razonamiento, tool calls, bloques crudos, motivo de corte, uso con caché), los errores,
``health`` y, con el tool loop real, que el historial de la vuelta es solo agregado. El
streaming está en ``test_anthropic_stream.py``.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.core.llm_protocol import LLMClientProtocol
from app.core.llm_providers import AliasThinking, ThinkingMode
from app.llm.clients.base import LLMClient
from app.llm.clients.providers.anthropic import ANTHROPIC_API_VERSION, AnthropicClient
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmContextOverflowError,
    LlmProviderRejectedError,
    LlmRateLimitError,
    LlmSpendLimitError,
    LlmUnavailableError,
    ModelNotServedError,
    ToolParsingError,
)
from app.llm.schemas import ChatMessage, ProviderBlocks, ToolCall, ToolSpec
from app.llm.tool_loop import run_tool_loop
from app.llm.tools.registry import ToolRegistry

_BASE_URL = "https://api.anthropic.test/v1"
_API_KEY = "sk-test-anthropic-0123456789"
_MODEL_ID = "modelo-agente-test"
_BETA = "thinking-binding-controls-2026-08-01"

_ADAPTIVE = ThinkingMode(type="adaptive", effort="medium")
_DISABLED = ThinkingMode(type="disabled", effort="medium")
_THINKING = {
    "agente": AliasThinking(on=_ADAPTIVE, off=_DISABLED, max_tokens_floor=4096),
    "conversacional": AliasThinking(
        on=ThinkingMode(type="between_tools"), off=ThinkingMode(type="disabled")
    ),
}
_MODELS = {"agente": _MODEL_ID, "conversacional": "modelo-chat-test"}

_SPEC = ToolSpec(
    name="calendar.create_event",
    description="crea un evento",
    parameters={"type": "object", "properties": {"title": {"type": "string"}}},
)


def _message(
    content: list[dict[str, Any]], *, stop_reason: str = "end_turn", **usage: int
) -> dict[str, Any]:
    """Body de ``POST /messages`` con la forma de la documentación."""
    return {
        "id": "msg_test",
        "type": "message",
        "role": "assistant",
        "model": _MODEL_ID,
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 10, "output_tokens": 5, **usage},
    }


_TEXT_REPLY = _message([{"type": "text", "text": "Hola, ¿en qué te ayudo?"}])


def _client(handler: Any, **overrides: Any) -> AnthropicClient:
    kwargs: dict[str, Any] = {
        "name": "claude",
        "base_url": _BASE_URL,
        "api_key": SecretStr(_API_KEY),
        "model_map": _MODELS,
        "thinking": _THINKING,
        "http_client": httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    }
    kwargs.update(overrides)
    return AnthropicClient(**kwargs)


def _capturing(
    bodies: list[dict[str, Any]], captured: list[httpx.Request], status: int = 200
) -> Any:
    """Handler que guarda cada request y responde los bodies en orden."""
    queue = list(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(status, json=queue.pop(0) if queue else bodies[-1])

    return handler


def _payload(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content)


async def _send(
    *,
    messages: list[ChatMessage] | None = None,
    body: dict[str, Any] | None = None,
    **kwargs: Any,
) -> tuple[httpx.Request, Any]:
    captured: list[httpx.Request] = []
    client = _client(_capturing([body or _TEXT_REPLY], captured))
    kwargs.setdefault("model", "agente")
    result = await client.complete(
        messages=messages or [ChatMessage(role="user", content="hola")], **kwargs
    )
    await client.aclose()
    return captured[0], result


# ---------- contrato y construcción ----------


def test_satisfies_the_llm_client_contracts() -> None:
    client = _client(lambda _r: httpx.Response(200))
    assert isinstance(client, LLMClient)
    assert isinstance(client, LLMClientProtocol)
    assert client.serves_model("agente")
    assert not client.serves_model("otro")


def test_repr_hides_the_key() -> None:
    client = _client(lambda _r: httpx.Response(200))
    assert repr(client) == "AnthropicClient(name='claude')"
    assert _API_KEY not in repr(client._auth)


def test_every_alias_needs_its_thinking() -> None:
    with pytest.raises(ValueError, match="falta el razonamiento del alias: conversacional"):
        _client(lambda _r: httpx.Response(200), thinking={"agente": _THINKING["agente"]})


async def test_unknown_alias_does_not_send_a_request() -> None:
    captured: list[httpx.Request] = []
    client = _client(_capturing([_TEXT_REPLY], captured))
    with pytest.raises(ModelNotServedError):
        await client.complete(model="otro", messages=[ChatMessage(role="user", content="x")])
    assert captured == []


# ---------- request: headers y campos ----------


async def test_request_carries_the_key_and_the_api_version() -> None:
    request, _ = await _send()
    assert request.url == f"{_BASE_URL}/messages"
    assert request.headers["x-api-key"] == _API_KEY
    assert request.headers["anthropic-version"] == ANTHROPIC_API_VERSION
    assert "authorization" not in request.headers


async def test_request_never_sends_sampling_parameters() -> None:
    """``temperature`` se ignora con este protocolo (D3)."""
    request, _ = await _send(temperature=0.1)
    payload = _payload(request)
    assert not {"temperature", "top_p", "top_k"} & set(payload)
    assert payload["model"] == _MODEL_ID
    assert payload["stream"] is False


@pytest.mark.parametrize("thinking", [True, None])
async def test_adaptive_mode_sends_summary_binding_effort_and_beta(thinking: bool | None) -> None:
    request, _ = await _send(thinking=thinking, max_tokens=512)
    payload = _payload(request)
    assert payload["thinking"] == {
        "type": "adaptive",
        "display": "summarized",
        "block_binding": {"prefix_mismatch_behavior": "error"},
    }
    assert payload["output_config"] == {"effort": "medium"}
    assert request.headers["anthropic-beta"] == _BETA
    # El razonamiento cuenta dentro de ``max_tokens``: sube al piso del alias (D4).
    assert payload["max_tokens"] == 4096


async def test_adaptive_mode_keeps_a_max_tokens_above_the_floor() -> None:
    request, _ = await _send(thinking=True, max_tokens=8000)
    assert _payload(request)["max_tokens"] == 8000


async def test_disabled_mode_sends_only_the_type_and_the_callers_max_tokens() -> None:
    request, _ = await _send(thinking=False, max_tokens=512)
    payload = _payload(request)
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["output_config"] == {"effort": "medium"}
    assert payload["max_tokens"] == 512
    assert "anthropic-beta" not in request.headers


async def test_between_tools_without_effort_sends_no_output_config() -> None:
    request, _ = await _send(model="conversacional", thinking=True, max_tokens=512)
    payload = _payload(request)
    assert payload["thinking"] == {"type": "between_tools"}
    assert "output_config" not in payload
    assert payload["max_tokens"] == 512
    assert "anthropic-beta" not in request.headers


# ---------- request: system y caché ----------


async def test_two_system_messages_cache_the_fixed_one() -> None:
    messages = [
        ChatMessage(role="system", content="prompt del modo"),
        ChatMessage(role="system", content="Fecha y hora actual: ..."),
        ChatMessage(role="user", content="hola"),
    ]
    request, _ = await _send(messages=messages)
    payload = _payload(request)
    assert payload["system"] == [
        {"type": "text", "text": "prompt del modo", "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": "Fecha y hora actual: ..."},
    ]
    assert payload["messages"] == [{"role": "user", "content": "hola"}]


async def test_a_single_system_message_is_not_cached() -> None:
    messages = [
        ChatMessage(role="system", content="extraé hechos"),
        ChatMessage(role="user", content="hola"),
    ]
    request, _ = await _send(messages=messages)
    assert _payload(request)["system"] == [{"type": "text", "text": "extraé hechos"}]


async def test_without_system_messages_there_is_no_system_field() -> None:
    request, _ = await _send()
    assert "system" not in _payload(request)


# ---------- request: tools ----------


async def test_tools_go_with_api_names_and_input_schema() -> None:
    request, _ = await _send(tools=[_SPEC])
    payload = _payload(request)
    assert payload["tools"] == [
        {
            "name": "calendar__create_event",
            "description": "crea un evento",
            "input_schema": _SPEC.parameters,
        }
    ]
    # ``auto`` es el default de la API: no se manda.
    assert "tool_choice" not in payload


async def test_tool_choice_none_keeps_the_tools() -> None:
    request, _ = await _send(tools=[_SPEC], tool_choice="none")
    payload = _payload(request)
    assert payload["tool_choice"] == {"type": "none"}
    assert [t["name"] for t in payload["tools"]] == ["calendar__create_event"]


async def test_without_tools_there_is_no_tool_choice() -> None:
    request, _ = await _send(tool_choice="none")
    assert not {"tools", "tool_choice"} & set(_payload(request))


# ---------- request: historial de la vuelta (D5) ----------

_OWN_BLOCKS = [
    {"type": "thinking", "thinking": "", "signature": "firma-sintetica"},
    {
        "type": "tool_use",
        "id": "toolu_1",
        "name": "calendar__create_event",
        "input": {"title": "dentista"},
    },
]


def _assistant(provider: str, *, content: str | None = None) -> ChatMessage:
    return ChatMessage(
        role="assistant",
        content=content,
        tool_calls=[
            ToolCall(id="toolu_1", name="calendar.create_event", arguments={"title": "dentista"})
        ],
        provider_blocks=ProviderBlocks(provider=provider, blocks=_OWN_BLOCKS),
    )


async def test_own_blocks_are_replayed_untouched() -> None:
    messages = [
        ChatMessage(role="user", content="agendame el dentista"),
        _assistant("claude"),
        ChatMessage(role="tool", content='{"status": "ok"}', tool_call_id="toolu_1"),
    ]
    request, _ = await _send(messages=messages)
    sent = _payload(request)["messages"]
    assert sent[1] == {"role": "assistant", "content": _OWN_BLOCKS}


async def test_blocks_from_another_entry_are_rebuilt_without_thinking() -> None:
    """El pool cambió de entrada a mitad de la vuelta: se pierde el razonamiento."""
    messages = [
        ChatMessage(role="user", content="agendame el dentista"),
        _assistant("otra_cuenta", content="Ya lo agendo."),
        ChatMessage(role="tool", content='{"status": "ok"}', tool_call_id="toolu_1"),
    ]
    request, _ = await _send(messages=messages)
    assert _payload(request)["messages"][1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Ya lo agendo."},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "calendar__create_event",
                "input": {"title": "dentista"},
            },
        ],
    }


async def test_rebuilt_assistant_without_text_has_no_empty_text_block() -> None:
    messages = [
        ChatMessage(role="user", content="agendame"),
        ChatMessage(
            role="assistant",
            content="",
            tool_calls=[ToolCall(id="t1", name="task.create", arguments={})],
        ),
        ChatMessage(role="tool", content="{}", tool_call_id="t1"),
    ]
    request, _ = await _send(messages=messages)
    content = _payload(request)["messages"][1]["content"]
    assert [block["type"] for block in content] == ["tool_use"]
    assert content[0]["name"] == "task__create"


async def test_consecutive_tool_results_go_in_one_user_message() -> None:
    messages = [
        ChatMessage(role="user", content="hacé dos cosas"),
        ChatMessage(
            role="assistant",
            tool_calls=[
                ToolCall(id="t1", name="task.create", arguments={}),
                ToolCall(id="t2", name="task.create", arguments={}),
            ],
        ),
        ChatMessage(role="tool", content='{"status": "ok"}', tool_call_id="t1"),
        ChatMessage(
            role="tool",
            content='{"error": {"code": "call_limit"}}',
            tool_call_id="t2",
            is_error=True,
        ),
    ]
    request, _ = await _send(messages=messages)
    sent = _payload(request)["messages"]
    assert len(sent) == 3
    assert sent[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": '{"status": "ok"}'},
            {
                "type": "tool_result",
                "tool_use_id": "t2",
                "content": '{"error": {"code": "call_limit"}}',
                "is_error": True,
            },
        ],
    }


async def test_a_tool_result_without_its_call_id_is_rejected() -> None:
    messages = [ChatMessage(role="user", content="x"), ChatMessage(role="tool", content="{}")]
    with pytest.raises(LlmBadRequestError):
        await _send(messages=messages)


async def test_leading_and_empty_assistant_messages_are_left_out() -> None:
    """Un historial recortado puede empezar con la respuesta a un pedido que quedó afuera."""
    messages = [
        ChatMessage(role="system", content="prompt"),
        ChatMessage(role="assistant", content="respuesta vieja"),
        ChatMessage(role="user", content="hola"),
        ChatMessage(role="assistant", content=""),
        ChatMessage(role="user", content="¿seguís?"),
    ]
    request, _ = await _send(messages=messages)
    assert _payload(request)["messages"] == [
        {"role": "user", "content": "hola"},
        {"role": "user", "content": "¿seguís?"},
    ]


# ---------- respuesta ----------


async def test_response_text_reasoning_and_usage_with_cache() -> None:
    body = _message(
        [
            {"type": "thinking", "thinking": "Pienso un momento.", "signature": "s1"},
            {"type": "redacted_thinking", "data": "opaco"},
            {"type": "text", "text": "Listo, "},
            {"type": "text", "text": "quedó agendado."},
        ],
        cache_read_input_tokens=300,
        cache_creation_input_tokens=50,
    )
    _, result = await _send(body=body)
    assert result.text == "Listo, quedó agendado."
    assert result.reasoning == "Pienso un momento."
    assert result.finish_reason == "stop"
    # D8: el contexto real del request es la suma de las tres partes.
    assert result.prompt_tokens == 10 + 300 + 50
    assert (result.cache_read_tokens, result.cache_creation_tokens) == (300, 50)
    assert result.completion_tokens == 5
    assert result.model_name == _MODEL_ID
    assert result.provider_blocks is not None
    assert result.provider_blocks.provider == "claude"
    assert result.provider_blocks.blocks == body["content"]


async def test_response_tool_calls_come_back_with_internal_names() -> None:
    body = _message(
        [
            {"type": "text", "text": ""},
            {
                "type": "tool_use",
                "id": "toolu_9",
                "name": "calendar__create_event",
                "input": {"title": "dentista"},
            },
        ],
        stop_reason="tool_use",
    )
    _, result = await _send(body=body, tools=[_SPEC])
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls == [
        ToolCall(id="toolu_9", name="calendar.create_event", arguments={"title": "dentista"})
    ]
    assert result.reasoning is None


async def test_a_tool_use_with_non_object_input_is_a_parsing_error() -> None:
    body = _message(
        [{"type": "tool_use", "id": "t", "name": "task__create", "input": "no"}],
        stop_reason="tool_use",
    )
    with pytest.raises(ToolParsingError):
        await _send(body=body)


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("end_turn", "stop"),
        ("stop_sequence", "stop"),
        ("max_tokens", "length"),
        ("model_context_window_exceeded", "length"),
        ("refusal", "content_filter"),
    ],
)
async def test_stop_reason_mapping(stop_reason: str, expected: str) -> None:
    _, result = await _send(body=_message([{"type": "text", "text": "x"}], stop_reason=stop_reason))
    assert result.finish_reason == expected


async def test_unknown_stop_reason_passes_through_and_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = _message([{"type": "text", "text": "x"}], stop_reason="pause_turn")
    with caplog.at_level(logging.WARNING):
        _, result = await _send(body=body)
    assert result.finish_reason == "pause_turn"
    assert "stop_reason desconocido (pause_turn)" in caplog.text


# ---------- errores ----------


def _error_body(error_type: str, message: str, error_code: str | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"type": error_type, "message": message}
    if error_code is not None:
        error["details"] = {"error_code": error_code}
    return {"type": "error", "error": error}


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (401, _error_body("authentication_error", "invalid x-api-key"), LlmAuthError),
        (402, _error_body("billing_error", "sin saldo"), LlmProviderRejectedError),
        (413, _error_body("request_too_large", "muy grande"), LlmProviderRejectedError),
        (
            400,
            _error_body("invalid_request_error", "prompt is too long: 9 tokens > 8 maximum"),
            LlmContextOverflowError,
        ),
        (
            429,
            _error_body("rate_limit_error", "spend cap hit", "enforced_spend_limit_reached"),
            LlmSpendLimitError,
        ),
        (429, _error_body("rate_limit_error", "más despacio"), LlmRateLimitError),
        (529, _error_body("overloaded_error", "sobrecarga"), LlmUnavailableError),
    ],
)
async def test_errors_map_to_the_taxonomy_without_the_body(
    status: int, body: dict[str, Any], expected: type[Exception]
) -> None:
    captured: list[httpx.Request] = []
    client = _client(_capturing([body], captured, status=status))
    with pytest.raises(expected) as excinfo:
        await client.complete(model="agente", messages=[ChatMessage(role="user", content="x")])
    # El tipo exacto: un 429 común no puede salir como tope de gasto, ni al revés.
    assert type(excinfo.value) is expected
    assert str(status) in str(excinfo.value)
    assert body["error"]["message"] not in str(excinfo.value)
    assert _API_KEY not in str(excinfo.value)


# ---------- health ----------


async def test_health_lists_the_models_with_the_key_and_version() -> None:
    captured: list[httpx.Request] = []
    client = _client(_capturing([{"data": []}], captured))
    health = await client.health()
    assert health.healthy
    assert health.model_name == "claude"
    assert captured[0].method == "GET"
    assert captured[0].url == f"{_BASE_URL}/models"
    assert captured[0].headers["x-api-key"] == _API_KEY
    assert captured[0].headers["anthropic-version"] == ANTHROPIC_API_VERSION


async def test_health_is_unhealthy_with_a_rejected_key() -> None:
    client = _client(_capturing([_error_body("authentication_error", "no")], [], status=401))
    assert not (await client.health()).healthy


async def test_health_is_unhealthy_without_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    assert not (await _client(handler).health()).healthy


# ---------- la vuelta con tools es solo agregado (ADR-031 D5) ----------


class _CalendarTool:
    name = "calendar.create_event"
    namespace = "calendar"
    description = "crea un evento"
    parameters: dict[str, Any] = {"type": "object", "properties": {}}  # noqa: RUF012

    async def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"status": "ok", "event_id": "ev-1"}


async def test_tool_loop_history_is_append_only_between_calls() -> None:
    """Cada request repite entero el anterior (system, tools y mensajes) y le agrega."""
    first = _message(
        [
            {"type": "thinking", "thinking": "Agendo.", "signature": "firma-1"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "calendar__create_event",
                "input": {"title": "dentista"},
            },
        ],
        stop_reason="tool_use",
    )
    second = _message([{"type": "text", "text": "Listo, quedó agendado."}])
    captured: list[httpx.Request] = []
    client = _client(_capturing([first, second], captured))
    registry = ToolRegistry()
    registry.register(_CalendarTool())  # type: ignore[arg-type]

    result = await run_tool_loop(
        llm_client=client,
        model="agente",
        messages=[
            ChatMessage(role="system", content="prompt del modo"),
            ChatMessage(role="system", content="Fecha y hora actual: ..."),
            ChatMessage(role="user", content="agendame el dentista"),
        ],
        specs=[_SPEC],
        registries=(registry, None),
        thinking=True,
        fallback_text="fallback",
    )

    assert result.text == "Listo, quedó agendado."
    one, two = (_payload(request) for request in captured)
    assert one["system"] == two["system"]
    assert one["tools"] == two["tools"]
    assert one["thinking"] == two["thinking"]
    assert two["messages"][: len(one["messages"])] == one["messages"]
    # Lo agregado: el assistant con sus bloques tal cual y el resultado de la tool.
    assert two["messages"][len(one["messages"])] == {
        "role": "assistant",
        "content": first["content"],
    }
    assert two["messages"][-1]["content"][0]["tool_use_id"] == "toolu_1"


async def test_forced_confirmation_keeps_the_prefix_with_tool_choice_none() -> None:
    """Tras acciones sin texto, la confirmación manda las mismas tools con ``none``.

    Pide ``thinking=False``: sale el modo apagado del alias (sin ``block_binding`` ni
    header beta), y el historial sigue siendo solo agregado.
    """
    first = _message(
        [
            {"type": "thinking", "thinking": "Agendo.", "signature": "firma-1"},
            {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "calendar__create_event",
                "input": {"title": "dentista"},
            },
        ],
        stop_reason="tool_use",
    )
    empty = _message([{"type": "text", "text": ""}])
    confirmation = _message([{"type": "text", "text": "Listo, quedó agendado."}])
    captured: list[httpx.Request] = []
    client = _client(_capturing([first, empty, confirmation], captured))
    registry = ToolRegistry()
    registry.register(_CalendarTool())  # type: ignore[arg-type]

    result = await run_tool_loop(
        llm_client=client,
        model="agente",
        messages=[
            ChatMessage(role="system", content="prompt del modo"),
            ChatMessage(role="system", content="Fecha y hora actual: ..."),
            ChatMessage(role="user", content="agendame el dentista"),
        ],
        specs=[_SPEC],
        registries=(registry, None),
        thinking=True,
        fallback_text="fallback",
    )

    assert result.text == "Listo, quedó agendado."
    assert len(captured) == 3
    two, three = _payload(captured[1]), _payload(captured[2])
    assert three["tool_choice"] == {"type": "none"}
    assert three["tools"] == two["tools"]
    assert three["system"] == two["system"]
    assert three["thinking"] == {"type": "disabled"}
    assert "anthropic-beta" not in captured[2].headers
    assert three["messages"][: len(two["messages"])] == two["messages"]
