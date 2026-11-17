"""Tests del mapeo de nombres de tools al wire de la API (``providers/_tool_names.py``).

La API de Chat Completions no acepta el ``.`` de ``calendar.create_event``: se manda
como ``__`` y se revierte al parsear, también con el nombre partido en fragmentos de un
stream. Un test recorre todas las tools del código para que ninguna rompa el mapeo.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re

import pytest

import app.llm.tools as tools_package
from app.llm.clients.parsers import OpenAIToolCallParser
from app.llm.clients.providers._tool_names import (
    ToolNameStreamDecoder,
    from_api_tool_name,
    to_api_history_tool_name,
    to_api_tool_name,
)
from app.llm.errors import LlmBadRequestError

_API_NAME_RE = re.compile(r"[a-zA-Z0-9_-]{1,64}")


def _all_tool_names() -> set[str]:
    """Nombres de todas las clases de tools de ``app.llm.tools`` (stubs y reales).

    Una tool es una clase con ``name`` y ``namespace`` de tipo ``str`` (Protocol
    ``Tool``). Recorrer el paquete entero cubre también las tools que se agreguen.
    """
    names: set[str] = set()
    for module_info in pkgutil.iter_modules(tools_package.__path__):
        module = importlib.import_module(f"{tools_package.__name__}.{module_info.name}")
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module.__name__:
                continue
            name = getattr(cls, "name", None)
            if isinstance(name, str) and isinstance(getattr(cls, "namespace", None), str):
                names.add(name)
    return names


# ---------- ida y vuelta ----------


def test_to_api_replaces_dot() -> None:
    assert to_api_tool_name("calendar.create_event") == "calendar__create_event"


def test_from_api_reverts() -> None:
    assert from_api_tool_name("calendar__create_event") == "calendar.create_event"


def test_name_without_dot_is_unchanged() -> None:
    assert to_api_tool_name("get_weather") == "get_weather"
    assert from_api_tool_name("get_weather") == "get_weather"


@pytest.mark.parametrize(
    "name",
    [
        "memory__search",  # ya trae el separador
        "a_.b",  # '_' justo antes del '.': iría como 'a___b', que vuelve como 'a._b'
        "a._.b",
        "calendar create",  # espacio
        "tool.ñandú",  # fuera de [a-zA-Z0-9_-]
        "x" * 65,  # más de 64
        "",
    ],
)
def test_unmappable_name_is_bad_request(name: str) -> None:
    with pytest.raises(LlmBadRequestError):
        to_api_tool_name(name)


@pytest.mark.parametrize("name", ["a._b", "a..b", "memo_x.add", "a._"])
def test_underscore_after_dot_round_trips(name: str) -> None:
    """Un ``_`` después del ``.`` (o puntos seguidos) sí se revierte sin ambigüedad."""
    assert from_api_tool_name(to_api_tool_name(name)) == name


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("calendar.create_event", "calendar__create_event"),
        ("buscar clima", "buscar clima"),  # inventado por el modelo: no se valida
        ("memory__search", "memory__search"),
    ],
)
def test_history_name_is_mapped_without_validation(name: str, expected: str) -> None:
    assert to_api_history_tool_name(name) == expected


def test_every_tool_in_the_code_round_trips() -> None:
    """Ninguna tool del código puede romper el mapeo (sino el request no sale)."""
    names = _all_tool_names()
    assert "calendar.create_event" in names
    for name in names:
        api_name = to_api_tool_name(name)
        assert _API_NAME_RE.fullmatch(api_name), name
        assert "." not in api_name
        assert from_api_tool_name(api_name) == name


# ---------- streaming ----------


def test_stream_decoder_full_name_in_one_fragment() -> None:
    decoder = ToolNameStreamDecoder()
    assert decoder.feed(0, "calendar__create_event") == "calendar.create_event"
    assert decoder.flush() == {}


def test_stream_decoder_name_split_between_underscores() -> None:
    """``calendar_`` + ``_create_event``: el ``_`` final se retiene hasta el siguiente."""
    decoder = ToolNameStreamDecoder()
    first = decoder.feed(0, "calendar_")
    second = decoder.feed(0, "_create_event")
    assert first == "calendar"
    assert first + second == "calendar.create_event"
    assert decoder.flush() == {}


@pytest.mark.parametrize("cut", range(1, len("task__create_task")))
def test_stream_decoder_any_split_point(cut: int) -> None:
    raw = "task__create_task"
    decoder = ToolNameStreamDecoder()
    emitted = decoder.feed(0, raw[:cut]) + decoder.feed(0, raw[cut:])
    emitted += decoder.flush().get(0, "")
    assert emitted == "task.create_task"


def test_stream_decoder_flush_returns_trailing_underscores() -> None:
    """Un ``_`` final que nunca se completa sale en el ``flush`` del cierre."""
    decoder = ToolNameStreamDecoder()
    assert decoder.feed(0, "rare_") == "rare"
    assert decoder.flush() == {0: "_"}


def test_stream_decoder_tracks_each_index() -> None:
    decoder = ToolNameStreamDecoder()
    parts = {
        0: decoder.feed(0, "memory_") + decoder.feed(0, "_add"),
        1: decoder.feed(1, "task__list_tasks"),
    }
    assert parts == {0: "memory.add", 1: "task.list_tasks"}


def test_stream_decoder_output_accumulates_with_parser() -> None:
    """Los fragmentos revertidos se acumulan con el parser como cualquier delta OpenAI."""
    decoder = ToolNameStreamDecoder()
    deltas = [
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "id": "call_1",
                                "function": {"name": decoder.feed(0, "reminder_")},
                            }
                        ]
                    }
                }
            ]
        },
        {
            "choices": [
                {
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {
                                    "name": decoder.feed(0, "_set"),
                                    "arguments": '{"text": "x"}',
                                },
                            }
                        ]
                    }
                }
            ]
        },
    ]
    calls = OpenAIToolCallParser().accumulate(deltas)
    assert [(c.id, c.name, c.arguments) for c in calls] == [
        ("call_1", "reminder.set", {"text": "x"})
    ]
