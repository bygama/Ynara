"""Streaming SSE de la Messages API: de eventos con tipo a ``CompletionChunk`` (ADR-031 D9).

Cada línea ``data:`` del stream trae un evento JSON con su ``type``:

- ``content_block_start`` abre un bloque (texto, razonamiento o ``tool_use``);
- ``content_block_delta`` lo va llenando: ``text_delta`` sale como ``delta_text``,
  ``thinking_delta`` como ``reasoning_delta``, y ``signature_delta`` e
  ``input_json_delta`` se acumulan;
- ``content_block_stop`` cierra el bloque: un ``tool_use`` completo sale como
  ``tool_call_delta``, con la forma de fragmento de Chat Completions que junta
  ``ToolCallParser.accumulate`` (la llamada entera en un solo fragmento);
- ``message_delta`` trae el ``stop_reason``;
- ``error`` corta el stream con un ``LlmError``.

El resto (``message_start``, ``ping``, ``message_stop``) no trae nada para el consumer.
Los bloques completos quedan en ``blocks`` con la forma de la respuesta sin streaming.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.clients.providers._anthropic_wire import finish_reason, tool_call_from_block
from app.llm.clients.providers._http import SPEND_LIMIT_CODE
from app.llm.errors import (
    LlmBadRequestError,
    LlmError,
    LlmRateLimitError,
    LlmSpendLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
)
from app.llm.schemas import CompletionChunk

# ``error.type`` de un evento ``error`` -> excepción. Lo que no está acá es un
# ``LlmError`` genérico: el stream se corta igual, con una etiqueta fija.
_STREAM_ERRORS: dict[str, type[LlmError]] = {
    "overloaded_error": LlmUnavailableError,
    "api_error": LlmUnavailableError,
    "timeout_error": LlmTimeoutError,
    "rate_limit_error": LlmRateLimitError,
    # El request no sirve: el respaldo fallaría igual (el resiliente no lo reintenta).
    "invalid_request_error": LlmBadRequestError,
}


class AnthropicStreamDecoder:
    """Convierte los eventos de un stream de la Messages API en ``CompletionChunk``."""

    def __init__(self) -> None:
        self._blocks: dict[int, dict[str, Any]] = {}
        self._partial_json: dict[int, str] = {}
        self._tool_calls = 0
        self.done = False

    @property
    def blocks(self) -> list[dict[str, Any]]:
        """Los bloques del mensaje, en orden, como en la respuesta sin streaming."""
        return [self._blocks[index] for index in sorted(self._blocks)]

    def feed(self, data: str) -> CompletionChunk | None:
        """Un evento ``data:``; ``None`` si no trae nada para el consumer.

        Un evento que no es JSON se ignora; un evento ``error`` levanta la excepción de
        su tipo con una etiqueta fija (nunca el mensaje del proveedor).
        """
        try:
            event = json.loads(data)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        kind = event.get("type")
        if kind == "content_block_start":
            self._start(event)
        elif kind == "content_block_delta":
            return self._delta(event)
        elif kind == "content_block_stop":
            return self._stop(event)
        elif kind == "message_delta":
            delta = event.get("delta")
            reason = finish_reason(delta.get("stop_reason") if isinstance(delta, dict) else None)
            # Como en ``parse_message``: un cierre normal que trae tool calls es
            # ``tool_calls``, o el consumer no las ejecutaría.
            if reason == "stop" and self._tool_calls:
                reason = "tool_calls"
            if reason is not None:
                return CompletionChunk(delta_text="", finish_reason=reason)
        elif kind == "message_stop":
            self.done = True
        elif kind == "error":
            raise _stream_error(event.get("error"))
        return None

    # ---------- helpers internos ----------

    def _start(self, event: dict[str, Any]) -> None:
        index = event.get("index")
        block = event.get("content_block")
        if isinstance(index, int) and isinstance(block, dict):
            self._blocks[index] = dict(block)

    def _delta(self, event: dict[str, Any]) -> CompletionChunk | None:
        index = event.get("index")
        delta = event.get("delta")
        if not isinstance(index, int) or not isinstance(delta, dict):
            return None
        block = self._blocks.setdefault(index, {})
        kind = delta.get("type")
        if kind == "text_delta" and isinstance(delta.get("text"), str):
            block["text"] = block.get("text", "") + delta["text"]
            return CompletionChunk(delta_text=delta["text"]) if delta["text"] else None
        if kind == "thinking_delta" and isinstance(delta.get("thinking"), str):
            block["thinking"] = block.get("thinking", "") + delta["thinking"]
            if not delta["thinking"]:
                return None
            return CompletionChunk(delta_text="", reasoning_delta=delta["thinking"])
        if kind == "signature_delta" and isinstance(delta.get("signature"), str):
            block["signature"] = block.get("signature", "") + delta["signature"]
        elif kind == "input_json_delta" and isinstance(delta.get("partial_json"), str):
            self._partial_json[index] = self._partial_json.get(index, "") + delta["partial_json"]
        return None

    def _stop(self, event: dict[str, Any]) -> CompletionChunk | None:
        index = event.get("index")
        block = self._blocks.get(index) if isinstance(index, int) else None
        if block is None or block.get("type") != "tool_use":
            return None
        raw = self._partial_json.pop(index, "")  # type: ignore[arg-type]
        try:
            block["input"] = json.loads(raw) if raw.strip() else {}
        except ValueError:
            block["input"] = None  # ``tool_call_from_block`` lo rechaza.
        call = tool_call_from_block(block)
        fragment = {
            "index": self._tool_calls,
            "id": call.id,
            "type": "function",
            "function": {
                "name": call.name,
                "arguments": json.dumps(call.arguments, ensure_ascii=False),
            },
        }
        self._tool_calls += 1
        wire_choice = {"index": 0, "delta": {"tool_calls": [fragment]}, "finish_reason": None}
        return CompletionChunk(delta_text="", tool_call_delta={"choices": [wire_choice]})


def _stream_error(error: object) -> LlmError:
    """La excepción de un evento ``error`` del stream, con una etiqueta fija."""
    if not isinstance(error, dict):
        return LlmError("error del proveedor en el stream")
    details = error.get("details")
    if isinstance(details, dict) and details.get("error_code") == SPEND_LIMIT_CODE:
        return LlmSpendLimitError("error del proveedor en el stream")
    kind = error.get("type")
    exc_type = _STREAM_ERRORS.get(kind) if isinstance(kind, str) else None
    if exc_type is None:
        return LlmError("error del proveedor en el stream")
    return exc_type("error del proveedor en el stream")
