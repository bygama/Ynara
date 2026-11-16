"""Razonamiento inline ``<think>...</think>`` en el ``content`` de una respuesta.

Algunos modelos de razonamiento (sobre todo los de pesos abiertos) mandan lo que
piensan embebido en el ``content`` como un bloque ``<think>...</think>``, en vez de por
el canal aparte (``reasoning_content`` / ``reasoning``). El cliente lo separa para que
el ``text`` llegue limpio y el razonamiento vaya por ``reasoning``, igual que con el
canal: la forma de ``CompletionResult`` y ``CompletionChunk`` no cambia.

``ThinkStreamSplitter`` separa fragmento a fragmento (un tag puede llegar partido entre
dos deltas) y ``split_inline_thinking`` aplica el mismo splitter a un ``content``
entero, así ``complete()`` y ``stream()`` separan igual. Para un bloque cerrado da lo
mismo que ``split_thinking`` (``app/llm/text_utils.py``); además cubre el bloque que
queda sin cerrar cuando el límite de tokens corta el razonamiento.
"""

from __future__ import annotations

import re

_OPEN_TAG = "<think>"
_CLOSE_TAG = "</think>"

# Búsqueda sin importar mayúsculas solo en ASCII: con ``str.lower()`` algunos caracteres
# Unicode cambian de largo y las posiciones dejarían de coincidir con el original.
_TAG_RE = {
    tag: re.compile(re.escape(tag), re.IGNORECASE | re.ASCII) for tag in (_OPEN_TAG, _CLOSE_TAG)
}


class ThinkStreamSplitter:
    """Separa el texto del razonamiento inline de un stream de fragmentos.

    ``feed`` devuelve ``(texto, razonamiento)`` de cada fragmento: lo que está fuera de
    ``<think>...</think>`` es texto y lo de adentro, razonamiento (los tags no salen
    por ningún lado). Si un fragmento termina con el principio de un tag (``"<thi"``),
    esa parte se retiene hasta ver el fragmento siguiente; ``flush`` la devuelve al
    cerrar el stream. Los espacios y saltos de línea que siguen a un tag se descartan,
    como hace ``split_thinking`` con los bordes. Los tags se reconocen sin importar
    mayúsculas.
    """

    def __init__(self) -> None:
        self._in_think = False
        self._pending = ""
        self._skip_whitespace = False
        self._saw_tag = False

    @property
    def saw_tag(self) -> bool:
        """``True`` si ya apareció algún tag ``<think>`` o ``</think>``."""
        return self._saw_tag

    def feed(self, fragment: str) -> tuple[str, str]:
        """Suma un fragmento del ``content`` y devuelve ``(texto, razonamiento)``."""
        data = self._pending + fragment
        self._pending = ""
        text: list[str] = []
        reasoning: list[str] = []
        while data:
            tag = _CLOSE_TAG if self._in_think else _OPEN_TAG
            match = _TAG_RE[tag].search(data)
            if match is None:
                held = _partial_tag_length(data, tag)
                self._emit(data[: len(data) - held], text, reasoning)
                self._pending = data[len(data) - held :]
                break
            self._emit(data[: match.start()], text, reasoning)
            data = data[match.end() :]
            self._in_think = not self._in_think
            self._skip_whitespace = True
            self._saw_tag = True
        return "".join(text), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        """Lo retenido al cerrar el stream: un tag que no se completó es texto literal."""
        text: list[str] = []
        reasoning: list[str] = []
        self._emit(self._pending, text, reasoning)
        self._pending = ""
        return "".join(text), "".join(reasoning)

    def _emit(self, chunk: str, text: list[str], reasoning: list[str]) -> None:
        if self._skip_whitespace:
            chunk = chunk.lstrip()
            if not chunk:
                return
            self._skip_whitespace = False
        if chunk:
            (reasoning if self._in_think else text).append(chunk)


def split_inline_thinking(content: str) -> tuple[str, str | None]:
    """Separa el razonamiento inline de un ``content`` completo.

    Devuelve ``(texto, razonamiento)``. Sin tags, el ``content`` vuelve tal cual y el
    razonamiento es ``None``; con tags, el texto y el razonamiento vuelven con los
    bordes recortados (``None`` si el razonamiento queda vacío).
    """
    splitter = ThinkStreamSplitter()
    text, reasoning = splitter.feed(content)
    if not splitter.saw_tag:
        return content, None
    tail_text, tail_reasoning = splitter.flush()
    clean_reasoning = (reasoning + tail_reasoning).strip()
    return (text + tail_text).strip(), clean_reasoning or None


def _partial_tag_length(data: str, tag: str) -> int:
    """Largo del sufijo de ``data`` que es el principio de ``tag`` (0 si no hay).

    Compara solo sufijos ASCII, igual que la búsqueda de tags (``_TAG_RE``).
    """
    for length in range(min(len(tag) - 1, len(data)), 0, -1):
        suffix = data[-length:]
        if suffix.isascii() and suffix.lower() == tag[:length]:
            return length
    return 0
