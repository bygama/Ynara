"""Streaming SSE de Chat Completions: de eventos ``data:`` a ``CompletionChunk``.

``sse_data`` saca el payload de cada línea del stream y ``StreamDecoder`` lo convierte
en chunks con la misma forma que ``VllmClient``: texto, razonamiento, fragmentos de
tool calls (``tool_call_delta``, que el consumer junta con ``ToolCallParser.accumulate``)
y ``finish_reason``.
"""

from __future__ import annotations

import json
from typing import Any

from app.llm.clients.providers._think import ThinkStreamSplitter
from app.llm.clients.providers._tool_names import ToolNameStreamDecoder
from app.llm.clients.providers._wire import content_text, finish_reason, reasoning_channel
from app.llm.errors import LlmError
from app.llm.schemas import CompletionChunk

SSE_DONE = "[DONE]"
_SSE_DATA_PREFIX = "data:"


def sse_data(line: str) -> str | None:
    """Payload de una línea ``data:`` de SSE; ``None`` para el resto (comentarios, etc.)."""
    stripped = line.strip()
    if not stripped.startswith(_SSE_DATA_PREFIX):
        return None
    return stripped[len(_SSE_DATA_PREFIX) :].strip() or None


class StreamDecoder:
    """Convierte los eventos de un stream en ``CompletionChunk``.

    Separa el ``<think>`` inline del texto y revierte los nombres de tools, que pueden
    llegar partidos entre deltas. Lo que esos decoders retienen sale con el chunk que
    trae el ``finish_reason`` o, si el stream cierra sin él, en ``finish()``.
    """

    def __init__(self) -> None:
        self._think = ThinkStreamSplitter()
        self._tool_names = ToolNameStreamDecoder()
        self._saw_tool_calls = False

    def feed(self, data: str) -> CompletionChunk | None:
        """Un evento ``data:``; ``None`` si no trae nada para el consumer.

        Un evento que no es JSON se ignora, como en ``VllmClient``. Un evento de error
        sin ``choices`` (el proveedor corta el stream después del 200) levanta
        ``LlmError`` con una etiqueta fija.
        """
        try:
            event = json.loads(data)
        except ValueError:
            return None
        if not isinstance(event, dict):
            return None
        choices = event.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            if "error" in event:
                raise LlmError("error del proveedor en el stream")
            return None
        choice = choices[0]
        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = {}
        text, reasoning = self._think.feed(content_text(delta.get("content")))
        tool_calls = self._decode_tool_calls(delta.get("tool_calls"))
        reason = finish_reason(choice.get("finish_reason"), self._saw_tool_calls)
        if reason is not None:
            tail_text, tail_reasoning, released = self._flush()
            text += tail_text
            reasoning += tail_reasoning
            tool_calls += released
        return _chunk(
            text=text,
            reasoning=(reasoning_channel(delta) or "") + reasoning,
            tool_calls=tool_calls,
            choice=choice,
            finish_reason=reason,
        )

    def finish(self) -> CompletionChunk | None:
        """Lo retenido al cerrar el stream sin ``finish_reason`` (o ``None``)."""
        text, reasoning, released = self._flush()
        return _chunk(
            text=text, reasoning=reasoning, tool_calls=released, choice={}, finish_reason=None
        )

    def _decode_tool_calls(self, fragments: object) -> list[dict[str, Any]]:
        if not isinstance(fragments, list):
            return []
        decoded: list[dict[str, Any]] = []
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            function = fragment.get("function")
            # Mismo default de ``index`` que ``OpenAIToolCallParser.accumulate``.
            index = fragment.get("index", 0)
            if (
                isinstance(index, int)
                and isinstance(function, dict)
                and isinstance(function.get("name"), str)
            ):
                name = self._tool_names.feed(index, function["name"])
                fragment = {**fragment, "function": {**function, "name": name}}
            decoded.append(fragment)
        self._saw_tool_calls = self._saw_tool_calls or bool(decoded)
        return decoded

    def _flush(self) -> tuple[str, str, list[dict[str, Any]]]:
        text, reasoning = self._think.flush()
        released = [
            {"index": index, "function": {"name": rest}}
            for index, rest in self._tool_names.flush().items()
        ]
        return text, reasoning, released


def _chunk(
    *,
    text: str,
    reasoning: str,
    tool_calls: list[dict[str, Any]],
    choice: dict[str, Any],
    finish_reason: str | None,
) -> CompletionChunk | None:
    """Arma el chunk, o ``None`` si no hay nada que mandar."""
    if not text and not reasoning and not tool_calls and finish_reason is None:
        return None
    tool_call_delta = None
    if tool_calls:
        # Solo los fragmentos ya revertidos: el ``content`` crudo del delta (con un
        # posible ``<think>``) no viaja en el delta de tools.
        wire_choice = {
            **choice,
            "delta": {"tool_calls": tool_calls},
            "finish_reason": finish_reason,
        }
        tool_call_delta = {"choices": [wire_choice]}
    return CompletionChunk(
        delta_text=text,
        tool_call_delta=tool_call_delta,
        finish_reason=finish_reason,
        reasoning_delta=reasoning or None,
    )
