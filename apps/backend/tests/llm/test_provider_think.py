"""Tests del razonamiento inline ``<think>...</think>`` (``providers/_think.py``).

El splitter de streaming tiene que dar lo mismo que el ``content`` entero sin importar
dónde corte el proveedor los fragmentos (un tag puede llegar partido), y para un
bloque cerrado al principio tiene que coincidir con ``split_thinking``.
"""

from __future__ import annotations

import pytest

from app.llm.clients.providers._think import ThinkStreamSplitter, split_inline_thinking
from app.llm.text_utils import split_thinking

_CONTENT = "<think>\nEl usuario saluda.\nRespondo corto.\n</think>\n\nHola, ¿cómo andás?"
_TEXT = "Hola, ¿cómo andás?"
_REASONING = "El usuario saluda.\nRespondo corto."


def _run(fragments: list[str]) -> tuple[str, str]:
    splitter = ThinkStreamSplitter()
    text: list[str] = []
    reasoning: list[str] = []
    for fragment in fragments:
        chunk_text, chunk_reasoning = splitter.feed(fragment)
        text.append(chunk_text)
        reasoning.append(chunk_reasoning)
    tail_text, tail_reasoning = splitter.flush()
    return "".join(text) + tail_text, "".join(reasoning) + tail_reasoning


# ---------- splitter de streaming ----------


def test_text_without_tags_passes_through() -> None:
    splitter = ThinkStreamSplitter()
    assert splitter.feed("Hola ") == ("Hola ", "")
    assert splitter.feed("mundo") == ("mundo", "")
    assert splitter.flush() == ("", "")
    assert not splitter.saw_tag


def test_block_in_one_fragment() -> None:
    splitter = ThinkStreamSplitter()
    assert splitter.feed("<think>pienso</think>Hola") == ("Hola", "pienso")
    assert splitter.saw_tag


def test_whitespace_after_tags_is_dropped() -> None:
    text, reasoning = _run([_CONTENT])
    assert text == _TEXT
    assert reasoning == _REASONING + "\n"


@pytest.mark.parametrize("cut", range(1, len(_CONTENT)))
def test_any_split_point_gives_the_same_result(cut: int) -> None:
    assert _run([_CONTENT[:cut], _CONTENT[cut:]]) == _run([_CONTENT])


def test_char_by_char_gives_the_same_result() -> None:
    assert _run(list(_CONTENT)) == _run([_CONTENT])


def test_partial_tag_is_held_until_next_fragment() -> None:
    splitter = ThinkStreamSplitter()
    assert splitter.feed("<thi") == ("", "")
    assert splitter.feed("nk>razono</thi") == ("", "razono")
    assert splitter.feed("nk>Listo") == ("Listo", "")


def test_unfinished_partial_tag_is_literal_text_on_flush() -> None:
    splitter = ThinkStreamSplitter()
    assert splitter.feed("Hola <thi") == ("Hola ", "")
    assert splitter.flush() == ("<thi", "")
    assert not splitter.saw_tag


def test_angle_bracket_that_is_not_a_tag() -> None:
    assert _run(list("si a < b y b <t c")) == ("si a < b y b <t c", "")


def test_tags_are_case_insensitive() -> None:
    assert _run(["<THINK>x</Think>y"]) == ("y", "x")


def test_unclosed_block_is_reasoning() -> None:
    """El límite de tokens cortó el razonamiento: nada de eso es texto para el usuario."""
    assert _run(["<think>pienso sin ", "terminar"]) == ("", "pienso sin terminar")


def test_two_blocks() -> None:
    assert _run(["<think>a</think>b<think>c</think>d"]) == ("bd", "ac")


def test_close_tag_without_open_stays_text() -> None:
    """Un cierre sin apertura no se reconoce (igual que ``split_thinking``)."""
    assert _run(["x</think>y"]) == ("x</think>y", "")


def test_unicode_before_tag_keeps_positions() -> None:
    """Caracteres que cambian de largo con ``lower()`` no corren las posiciones."""
    assert _run(["İ<think>x</think>y"]) == ("İy", "x")
    assert _run(["İ", "<thi", "nk>x</th", "ink>y"]) == ("İy", "x")


# ---------- content entero ----------


def test_split_inline_without_tags_returns_content_verbatim() -> None:
    assert split_inline_thinking("  hola\n") == ("  hola\n", None)


def test_split_inline_trims_edges() -> None:
    assert split_inline_thinking(_CONTENT) == (_TEXT, _REASONING)


def test_split_inline_empty_block_is_none() -> None:
    assert split_inline_thinking("<think>\n</think>Hola") == ("Hola", None)


def test_split_inline_unclosed_block() -> None:
    assert split_inline_thinking("<think>pienso sin terminar") == ("", "pienso sin terminar")


@pytest.mark.parametrize(
    "content",
    [
        _CONTENT,
        "<think>x</think>Hola",
        "<THINK>razono</THINK> Respuesta.",
        "<think>\n\n</think>\n\nSolo texto",
        "Sin bloque de razonamiento",
    ],
)
def test_split_inline_matches_split_thinking(content: str) -> None:
    """Para un bloque cerrado al principio da el mismo texto que ``split_thinking``."""
    text, reasoning = split_inline_thinking(content)
    expected_text, block = split_thinking(content)
    assert text.strip() == expected_text
    if block is None:
        assert reasoning is None
    else:
        inner = block[block.index(">") + 1 : block.rindex("<")].strip()
        assert reasoning == (inner or None)
