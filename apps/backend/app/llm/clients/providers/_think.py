"""Razonamiento inline ``<think>...</think>`` en el ``content`` de una respuesta.

Algunos modelos de razonamiento (sobre todo los de pesos abiertos) mandan lo que
piensan embebido en el ``content`` como un bloque ``<think>...</think>``, en vez de por
el canal aparte (``reasoning_content`` / ``reasoning``). El cliente lo separa para que
el ``text`` llegue limpio y el razonamiento vaya por ``reasoning``, igual que con el
canal: la forma de ``CompletionResult`` y ``CompletionChunk`` no cambia.

``ThinkStreamSplitter`` separa fragmento a fragmento (un tag puede llegar partido entre
dos deltas) y ``split_inline_thinking`` aplica el mismo splitter a un ``content``
entero, así ``complete()`` y ``stream()`` separan igual:

- El bloque que abre el ``content`` (antes solo puede haber espacios) sale como
  razonamiento a medida que llega. Si queda sin cerrar, porque el límite de tokens
  cortó el razonamiento, todo lo que sigue es razonamiento.
- Un ``<think>`` a mitad del texto se retiene hasta ver el cierre: si cierra, lo de
  adentro es razonamiento; si el ``content`` termina antes, era texto literal (un
  modelo que explica la etiqueta, por ejemplo) y sale tal cual.

Para un bloque cerrado, el texto da lo mismo que ``split_thinking``
(``app/llm/text_utils.py``) una vez recortados los bordes. Solo se reconocen los tags
exactos ``<think>`` y ``</think>`` (sin importar mayúsculas), no un ``<think>`` con
atributos.
"""

from __future__ import annotations

import re
from enum import Enum, auto

_OPEN_TAG = "<think>"
_CLOSE_TAG = "</think>"

# Búsqueda sin importar mayúsculas solo en ASCII: con ``str.lower()`` algunos caracteres
# Unicode cambian de largo y las posiciones dejarían de coincidir con el original.
_TAG_RE = {
    tag: re.compile(re.escape(tag), re.IGNORECASE | re.ASCII) for tag in (_OPEN_TAG, _CLOSE_TAG)
}


class _Mode(Enum):
    START = auto()  # todavía no llegó nada más que espacios
    TEXT = auto()  # texto para el usuario
    THINK = auto()  # adentro del bloque que abrió el content
    HELD = auto()  # adentro de un <think> a mitad del texto, retenido hasta el cierre


class ThinkStreamSplitter:
    """Separa el texto del razonamiento inline de un stream de fragmentos.

    ``feed`` devuelve ``(texto, razonamiento)`` de cada fragmento (los tags no salen
    por ningún lado) y ``flush`` lo retenido al cerrar el stream. Se retiene el final
    de un fragmento que puede ser el principio de un tag (``"<thi"``), los espacios del
    principio del ``content`` (hasta ver si abre con ``<think>``) y un bloque que
    empieza a mitad del texto, hasta ver si cierra. Los espacios que siguen a los tags
    del bloque inicial se descartan, como hace ``split_thinking`` con los bordes.
    """

    def __init__(self) -> None:
        self._mode = _Mode.START
        self._pending = ""
        self._held_tag = ""
        self._skip_whitespace = False
        self._saw_tag = False

    @property
    def saw_tag(self) -> bool:
        """``True`` si ya se separó algún bloque de razonamiento."""
        return self._saw_tag

    def feed(self, fragment: str) -> tuple[str, str]:
        """Suma un fragmento del ``content`` y devuelve ``(texto, razonamiento)``."""
        data = self._pending + fragment
        self._pending = ""
        text: list[str] = []
        reasoning: list[str] = []
        while data:
            if self._mode is _Mode.START:
                data = self._feed_start(data)
            elif self._mode is _Mode.HELD:
                data = self._feed_held(data, reasoning)
            else:
                data = self._feed_open(data, text, reasoning)
        return "".join(text), "".join(reasoning)

    def flush(self) -> tuple[str, str]:
        """Lo retenido al cerrar el stream: lo que no llegó a ser un tag es literal."""
        text: list[str] = []
        reasoning: list[str] = []
        if self._mode is _Mode.HELD:
            # El ``<think>`` a mitad del texto no cerró: era texto, con el tag incluido.
            self._mode = _Mode.TEXT
            self._emit(self._held_tag + self._pending, text)
        else:
            self._emit(self._pending, reasoning if self._mode is _Mode.THINK else text)
        self._pending = ""
        self._held_tag = ""
        return "".join(text), "".join(reasoning)

    def _feed_start(self, data: str) -> str:
        """Antes del primer carácter que no es espacio: ¿el content abre con ``<think>``?"""
        stripped = data.lstrip()
        match = _TAG_RE[_OPEN_TAG].match(stripped)
        if match is not None:
            self._mode = _Mode.THINK
            self._skip_whitespace = True
            self._saw_tag = True
            return stripped[match.end() :]
        if _partial_tag_length(stripped, _OPEN_TAG) == len(stripped):
            self._pending = data  # solo espacios o el principio del tag: todavía no se sabe
            return ""
        self._mode = _Mode.TEXT
        return data

    def _feed_open(self, data: str, text: list[str], reasoning: list[str]) -> str:
        """Texto o bloque inicial: emite hasta el próximo tag que cambia de modo."""
        in_think = self._mode is _Mode.THINK
        out = reasoning if in_think else text
        tag = _CLOSE_TAG if in_think else _OPEN_TAG
        match = _TAG_RE[tag].search(data)
        if match is None:
            held = _partial_tag_length(data, tag)
            self._emit(data[: len(data) - held], out)
            self._pending = data[len(data) - held :]
            return ""
        self._emit(data[: match.start()], out)
        if in_think:
            self._mode = _Mode.TEXT
            self._skip_whitespace = True
        else:
            self._mode = _Mode.HELD
            self._held_tag = match.group(0)
        return data[match.end() :]

    def _feed_held(self, data: str, reasoning: list[str]) -> str:
        """Bloque a mitad del texto: se retiene entero hasta que llega el cierre."""
        match = _TAG_RE[_CLOSE_TAG].search(data)
        if match is None:
            self._pending = data
            return ""
        inner = data[: match.start()].lstrip()
        if inner:
            reasoning.append(inner)
        self._mode = _Mode.TEXT
        self._held_tag = ""
        self._saw_tag = True
        return data[match.end() :]

    def _emit(self, chunk: str, out: list[str]) -> None:
        if self._skip_whitespace:
            chunk = chunk.lstrip()
            if not chunk:
                return
            self._skip_whitespace = False
        if chunk:
            out.append(chunk)


def split_inline_thinking(content: str) -> tuple[str, str | None]:
    """Separa el razonamiento inline de un ``content`` completo.

    Devuelve ``(texto, razonamiento)``. Sin bloques que separar, el ``content`` vuelve
    tal cual y el razonamiento es ``None``; si se separó alguno, el texto y el
    razonamiento vuelven con los bordes recortados (``None`` si el razonamiento queda
    vacío).
    """
    splitter = ThinkStreamSplitter()
    text, reasoning = splitter.feed(content)
    tail_text, tail_reasoning = splitter.flush()
    if not splitter.saw_tag:
        return content, None
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
