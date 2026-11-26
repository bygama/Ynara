"""Traducción entre los schemas del backend y el wire de la Messages API (ADR-031).

Helpers puros que comparten ``complete()`` y ``stream()`` de ``AnthropicClient``:

- ``encode_system`` y ``encode_messages`` arman el ``system`` y los ``messages`` del
  request: el system en bloques con la caché en el fijo (D6), el mensaje ``assistant``
  con sus bloques reenviados tal cual o rearmados desde ``tool_calls`` (D5) y los
  resultados de tools agrupados en un solo mensaje ``user`` (D5).
- ``thinking_fields`` traduce el modo de razonamiento del alias a los campos del
  request (D3) y al ``max_tokens`` con su piso (D4).
- ``parse_message`` lee la respuesta: texto, razonamiento, tool calls, bloques crudos,
  motivo de corte (D7) y uso con el desglose de caché (D8).

Los nombres de tools viajan con ``__`` en vez de ``.`` (``_tool_names``), igual que en
el cliente compatible con OpenAI.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.core.llm_providers import ThinkingMode
from app.llm.clients.providers._tool_names import (
    from_api_tool_name,
    to_api_history_tool_name,
    to_api_tool_name,
)
from app.llm.clients.providers._wire import token_count
from app.llm.errors import LlmBadRequestError, LlmError, ToolParsingError
from app.llm.schemas import (
    ChatMessage,
    CompletionResult,
    ProviderBlocks,
    ToolCall,
    ToolChoice,
    ToolSpec,
)

logger = logging.getLogger(__name__)

# Header beta que pide ``thinking.block_binding`` (ADR-031 D1 y D5).
THINKING_BINDING_BETA = "thinking-binding-controls-2026-08-01"

# ``stop_reason`` de la Messages API -> ``finish_reason`` del contrato (ADR-031 D7).
_STOP_REASONS: dict[str, str] = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "model_context_window_exceeded": "length",
    "tool_use": "tool_calls",
    "refusal": "content_filter",
}

# Forma de un ``stop_reason`` que se puede loguear tal cual: es un valor de un enum de
# la API, nunca texto del modelo, pero se verifica antes de escribirlo en un log.
_LOGGABLE_REASON = re.compile(r"[a-z_]{1,40}")


def encode_system(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Los mensajes ``system`` como bloques de texto del ``system`` del request.

    Con dos o más (el fijo y el variable del router, ADR-031 D6) el primero lleva
    ``cache_control``: tools y system fijo quedan en el prefijo cacheado. Con uno solo
    no se marca caché: el cliente no sabe si ese texto es estable, y escribir caché que
    nadie lee cuesta más que no cachear. Un system vacío no se manda.
    """
    blocks: list[dict[str, Any]] = [
        {"type": "text", "text": m.content} for m in messages if m.role == "system" and m.content
    ]
    if len(blocks) >= 2:
        blocks[0]["cache_control"] = {"type": "ephemeral"}
    return blocks


def encode_messages(messages: list[ChatMessage], *, provider: str) -> list[dict[str, Any]]:
    """Los mensajes que no son ``system``, en el formato de la Messages API.

    - ``user``: su texto.
    - ``assistant``: los ``provider_blocks`` tal cual si los produjo ``provider`` (la
      misma entrada de ``LLM_PROVIDERS``); si no, el texto y un ``tool_use`` por cada
      ``tool_calls``, sin razonamiento (ADR-031 D5). Un ``assistant`` sin texto ni
      tools no se manda: la API rechaza un mensaje vacío.
    - ``tool``: los consecutivos van juntos en un solo ``user`` con un ``tool_result``
      cada uno, con ``is_error`` si el resultado tiene la forma de ``tool_error``.

    Un historial recortado por presupuesto puede empezar con un ``assistant`` cuyo
    pedido quedó afuera; esos ``assistant`` iniciales no se mandan. El recorte es el
    mismo en cada llamada de la vuelta, así que el prefijo no cambia entre llamadas.
    """
    encoded: list[dict[str, Any]] = []
    for message in messages:
        if message.role == "system":
            continue
        if message.role == "tool":
            result = _tool_result(message)
            previous = encoded[-1] if encoded else None
            if previous is not None and _is_tool_results(previous):
                previous["content"].append(result)
            else:
                encoded.append({"role": "user", "content": [result]})
            continue
        if message.role == "assistant":
            if not encoded:
                continue
            content = _assistant_content(message, provider=provider)
            if content:
                encoded.append({"role": "assistant", "content": content})
            continue
        encoded.append({"role": "user", "content": message.content or ""})
    return encoded


def encode_tools(tools: list[ToolSpec] | None, tool_choice: ToolChoice) -> dict[str, Any]:
    """``tools`` y ``tool_choice`` del request (nada si no hay tools).

    ``auto`` es el default de la API y no se manda; ``none`` deja las mismas tools en
    el prefijo y le pide al modelo que conteste sin llamarlas (ADR-031 D5).
    """
    if not tools:
        return {}
    fields: dict[str, Any] = {
        "tools": [
            {
                "name": to_api_tool_name(tool.name),
                "description": tool.description,
                "input_schema": tool.parameters,
            }
            for tool in tools
        ]
    }
    if tool_choice == "none":
        fields["tool_choice"] = {"type": "none"}
    return fields


def thinking_fields(
    mode: ThinkingMode, *, max_tokens: int, max_tokens_floor: int | None
) -> tuple[dict[str, Any], bool]:
    """Campos del request para un modo de razonamiento, y si hace falta el header beta.

    - ``adaptive``: razonamiento adaptativo con resumen (sin ``display`` la API manda
      el bloque sin texto) y ``block_binding`` en ``error``, que pide el header beta.
      ``max_tokens`` sube al piso del alias si el llamador pidió menos (D4).
    - ``disabled`` y ``between_tools``: solo el ``type``; ``max_tokens`` tal cual.

    El esfuerzo va en ``output_config.effort`` cuando el modo lo tiene.
    """
    fields: dict[str, Any] = {}
    adaptive = mode.type == "adaptive"
    if adaptive:
        fields["thinking"] = {
            "type": "adaptive",
            "display": "summarized",
            "block_binding": {"prefix_mismatch_behavior": "error"},
        }
        fields["max_tokens"] = max(max_tokens, max_tokens_floor or 0)
    else:
        fields["thinking"] = {"type": mode.type}
        fields["max_tokens"] = max_tokens
    if mode.effort is not None:
        fields["output_config"] = {"effort": mode.effort}
    return fields, adaptive


def finish_reason(stop_reason: object) -> str | None:
    """``finish_reason`` para un ``stop_reason``; uno desconocido pasa tal cual y se loguea."""
    if not isinstance(stop_reason, str) or not stop_reason:
        return None
    mapped = _STOP_REASONS.get(stop_reason)
    if mapped is not None:
        return mapped
    label = stop_reason if _LOGGABLE_REASON.fullmatch(stop_reason) else "ilegible"
    logger.warning("anthropic: stop_reason desconocido (%s)", label)
    return stop_reason


def tool_call_from_block(block: dict[str, Any]) -> ToolCall:
    """Un bloque ``tool_use`` como ``ToolCall``, con el nombre interno de la tool."""
    call_id = block.get("id")
    name = block.get("name")
    arguments = block.get("input")
    if not isinstance(call_id, str) or not isinstance(name, str):
        raise ToolParsingError("tool_use sin id o sin nombre")
    if not isinstance(arguments, dict):
        raise ToolParsingError("tool_use con input que no es un objeto")
    return ToolCall(id=call_id, name=from_api_tool_name(name), arguments=arguments)


def parse_message(
    body: object, *, provider: str, model_id: str, latency_ms: float
) -> CompletionResult:
    """La respuesta de ``POST /messages`` como ``CompletionResult``.

    El texto es la suma de los bloques ``text``; el razonamiento, la de los
    ``thinking`` (los ``redacted_thinking`` no tienen texto). Los bloques crudos van en
    ``provider_blocks`` para reenviarlos dentro de la vuelta. ``prompt_tokens`` suma la
    entrada sin cachear, la leída de caché y la escrita en caché (D8).
    """
    if not isinstance(body, dict):
        raise LlmError("respuesta malformada")
    content = body.get("content")
    if not isinstance(content, list):
        raise LlmError("respuesta sin content")
    blocks = [block for block in content if isinstance(block, dict)]
    text = "".join(
        block["text"]
        for block in blocks
        if block.get("type") == "text" and isinstance(block.get("text"), str)
    )
    reasoning = "\n\n".join(
        block["thinking"]
        for block in blocks
        if block.get("type") == "thinking"
        and isinstance(block.get("thinking"), str)
        and block["thinking"]
    )
    tool_calls = [
        tool_call_from_block(block) for block in blocks if block.get("type") == "tool_use"
    ]
    reason = finish_reason(body.get("stop_reason"))
    if reason is None or (reason == "stop" and tool_calls):
        reason = "tool_calls" if tool_calls else "stop"
    usage = body.get("usage")
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = token_count(usage.get("input_tokens"))
    cache_read = token_count(usage.get("cache_read_input_tokens"))
    cache_creation = token_count(usage.get("cache_creation_input_tokens"))
    model_name = body.get("model")
    if not isinstance(model_name, str) or not model_name:
        model_name = model_id
    return CompletionResult(
        text=text,
        tool_calls=tool_calls,
        finish_reason=reason,
        prompt_tokens=input_tokens + cache_read + cache_creation,
        completion_tokens=token_count(usage.get("output_tokens")),
        model_name=model_name,
        latency_ms=latency_ms,
        reasoning=reasoning or None,
        provider_blocks=ProviderBlocks(provider=provider, blocks=blocks) if blocks else None,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
    )


# ---------- helpers internos ----------


def _assistant_content(message: ChatMessage, *, provider: str) -> list[dict[str, Any]]:
    """Bloques de un ``assistant``: los crudos de la misma entrada o rearmados."""
    blocks = message.provider_blocks
    if blocks is not None and blocks.provider == provider and blocks.blocks:
        return [dict(block) for block in blocks.blocks]
    content: list[dict[str, Any]] = []
    if message.content:
        content.append({"type": "text", "text": message.content})
    content.extend(
        {
            "type": "tool_use",
            "id": call.id,
            "name": to_api_history_tool_name(call.name),
            "input": call.arguments,
        }
        for call in message.tool_calls or []
    )
    return content


def _tool_result(message: ChatMessage) -> dict[str, Any]:
    """Un mensaje ``tool`` como bloque ``tool_result``."""
    if message.tool_call_id is None:
        raise LlmBadRequestError("resultado de tool sin tool_call_id")
    result: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": message.tool_call_id,
        "content": message.content or "",
    }
    if message.is_error:
        result["is_error"] = True
    return result


def _is_tool_results(message: dict[str, Any]) -> bool:
    """``True`` si es un ``user`` armado solo con ``tool_result`` (para sumar al grupo)."""
    content = message.get("content")
    return (
        message.get("role") == "user"
        and isinstance(content, list)
        and all(block.get("type") == "tool_result" for block in content)
    )
