"""Nombres de tools en el wire de la API de los proveedores (ADR-028 D3).

Las tools del agente se llaman ``namespace.accion`` (``calendar.create_event``), pero la
API de Chat Completions exige nombres que cumplan ``^[a-zA-Z0-9_-]{1,64}$``: el punto no
entra. Para la API, cada ``.`` se manda como ``__`` (``calendar__create_event``), y al
parsear la respuesta se revierte, también cuando el nombre llega en fragmentos de un
stream (``ToolNameStreamDecoder``).

El mapeo es reversible si y solo si el nombre original no trae ``__`` ni un ``_`` justo
antes de un ``.`` (``a_.b`` iría como ``a___b``, que vuelve como ``a._b``).
``to_api_tool_name`` lo verifica en cada request; un test recorre todas las tools del
código para que ninguna rompa la regla.
"""

from __future__ import annotations

import re

from app.llm.errors import LlmBadRequestError

# Separador que reemplaza al ``.`` del nombre interno en el wire de la API.
_API_SEPARATOR = "__"

# Forma que la API de Chat Completions exige para el nombre de una función.
_API_NAME_RE = re.compile(r"[a-zA-Z0-9_-]{1,64}")


def to_api_tool_name(name: str) -> str:
    """Nombre interno de una tool -> nombre para la API (``.`` -> ``__``).

    Levanta ``LlmBadRequestError`` si el resultado no cumple la forma de la API o si no
    se puede revertir sin ambigüedad: el request no se puede expresar en el wire.
    """
    api_name = name.replace(".", _API_SEPARATOR)
    if not _API_NAME_RE.fullmatch(api_name) or from_api_tool_name(api_name) != name:
        raise LlmBadRequestError("nombre de tool no mapeable a la API")
    return api_name


def to_api_history_tool_name(name: str) -> str:
    """Nombre de una tool del historial -> nombre para la API, sin validar.

    El historial repite los nombres que mandó el propio modelo, también uno inventado
    que no cumpla la forma de la API: se le devuelve mapeado tal cual, en vez de cortar
    el turno con un error local. Las definiciones de tools sí se validan
    (``to_api_tool_name``).
    """
    return name.replace(".", _API_SEPARATOR)


def from_api_tool_name(api_name: str) -> str:
    """Nombre de la API -> nombre interno de la tool (``__`` -> ``.``).

    Un nombre que el modelo inventa (sin ``__``) queda igual; el tool loop lo resuelve
    como tool desconocida.
    """
    return api_name.replace(_API_SEPARATOR, ".")


class ToolNameStreamDecoder:
    """Revierte los nombres de tools que llegan en fragmentos de un stream.

    En streaming, el nombre de cada tool call (identificada por su ``index``) puede venir
    partido en varios deltas, y un ``_`` al final de un fragmento puede ser la mitad de un
    ``__``. Por eso los ``_`` finales se retienen hasta ver el fragmento siguiente o hasta
    el cierre del stream (``flush``). Lo emitido, concatenado por ``index``, es siempre el
    nombre interno revertido.
    """

    def __init__(self) -> None:
        self._raw: dict[int, str] = {}
        self._emitted: dict[int, int] = {}

    def feed(self, index: int, fragment: str) -> str:
        """Suma un fragmento del nombre y devuelve la parte revertida lista para emitir."""
        raw = self._raw.get(index, "") + fragment
        self._raw[index] = raw
        return self._advance(index, raw.rstrip("_"))

    def flush(self) -> dict[int, str]:
        """Lo retenido al cerrar el stream (los ``_`` finales), por ``index``."""
        pending: dict[int, str] = {}
        for index, raw in self._raw.items():
            rest = self._advance(index, raw)
            if rest:
                pending[index] = rest
        return pending

    def _advance(self, index: int, stable_raw: str) -> str:
        """Parte nueva del nombre revertido de ``stable_raw`` (sin repetir lo emitido)."""
        decoded = from_api_tool_name(stable_raw)
        emitted = self._emitted.get(index, 0)
        self._emitted[index] = max(emitted, len(decoded))
        return decoded[emitted:]
