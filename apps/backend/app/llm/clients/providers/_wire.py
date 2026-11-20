"""Traducción entre los schemas del backend y el wire de Chat Completions.

Helpers puros que comparten ``complete()`` y ``stream()`` de ``OpenAICompatClient``:
armar mensajes y tools para el request (con los nombres de tools mapeados) y leer los
campos de la respuesta de forma defensiva, porque cada proveedor completa el formato a
su manera (``content`` como lista de partes, ``usage`` en ``null``, el razonamiento en
``reasoning_content`` o en ``reasoning``).
"""

from __future__ import annotations

import json
import math
from typing import Any

import httpx

from app.llm.clients.providers._tool_names import (
    from_api_tool_name,
    to_api_history_tool_name,
    to_api_tool_name,
)
from app.llm.errors import LlmError
from app.llm.schemas import ChatMessage, ToolSpec

# Canales de razonamiento separados del ``content``, en orden de preferencia: DeepSeek y
# vLLM usan ``reasoning_content``; otros proveedores, ``reasoning``.
_REASONING_KEYS = ("reasoning_content", "reasoning")


def encode_message(message: ChatMessage) -> dict[str, Any]:
    """``ChatMessage`` -> mensaje del wire, con los nombres de tools mapeados.

    El assistant que llamó tools las re-envía con ``arguments`` como string JSON (sin
    eso el proveedor rechaza el ``role="tool"`` siguiente); una lista vacía no emite la
    clave. El ``name`` de un ``role="tool"`` es el de la tool, así que también se mapea.
    """
    encoded: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_call_id is not None:
        encoded["tool_call_id"] = message.tool_call_id
    if message.name is not None:
        is_tool = message.role == "tool"
        encoded["name"] = to_api_history_tool_name(message.name) if is_tool else message.name
    if message.tool_calls:
        encoded["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": to_api_history_tool_name(call.name),
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    return encoded


def encode_tool(tool: ToolSpec) -> dict[str, Any]:
    """``ToolSpec`` -> definición de función del wire (el nombre se valida al mapearlo)."""
    return {
        "type": "function",
        "function": {
            "name": to_api_tool_name(tool.name),
            "description": tool.description,
            "parameters": tool.parameters,
        },
    }


def decode_tool_names(message: dict[str, Any]) -> dict[str, Any]:
    """Copia del ``message`` con los nombres de ``tool_calls`` revertidos para el parser."""
    raw_calls = message.get("tool_calls")
    if not isinstance(raw_calls, list):
        return message
    decoded: list[Any] = []
    for call in raw_calls:
        function = call.get("function") if isinstance(call, dict) else None
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            name = from_api_tool_name(function["name"])
            call = {**call, "function": {**function, "name": name}}
        decoded.append(call)
    return {**message, "tool_calls": decoded}


def json_body(response: httpx.Response) -> Any:
    """Body JSON de una respuesta; ``LlmError`` si no es JSON."""
    try:
        return response.json()
    except ValueError as exc:
        raise LlmError("respuesta malformada") from exc


def content_text(content: object) -> str:
    """``content`` del wire como texto: string, lista de partes de texto o nada."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part["text"]
            for part in content
            if isinstance(part, dict)
            and part.get("type", "text") == "text"
            and isinstance(part.get("text"), str)
        )
    raise LlmError("respuesta malformada")


def reasoning_channel(container: dict[str, Any]) -> str | None:
    """Razonamiento del canal separado de un ``message`` o un ``delta`` (o ``None``)."""
    for key in _REASONING_KEYS:
        value = container.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def finish_reason(raw: object, has_tool_calls: bool) -> str | None:
    """``finish_reason`` del wire; ``"stop"`` con tool calls se informa como ``"tool_calls"``.

    Algunos proveedores cierran con ``"stop"`` aunque el mensaje traiga tool calls, y el
    tool loop trata ``"stop"`` como fin del turno: las tools no se ejecutarían.
    """
    if not isinstance(raw, str) or not raw:
        return None
    if raw == "stop" and has_tool_calls:
        return "tool_calls"
    return raw


def token_count(raw: object) -> int:
    """Un contador de ``usage`` como ``int`` (``0`` si falta o no es un número)."""
    if isinstance(raw, bool) or not isinstance(raw, int | float) or not math.isfinite(raw):
        return 0
    return max(int(raw), 0)
