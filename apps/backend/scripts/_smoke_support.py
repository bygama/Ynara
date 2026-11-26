"""Piezas de la prueba de humo de Anthropic que no hablan con el modelo.

Lo usa ``check_anthropic.py``: el registro de cada request (``Recorder``), el
resultado de un caso, el formato de la salida, la credencial y el host permitido. Va
aparte para que el script quede con los casos.

Todo lo que sale de una respuesta del proveedor (tipos de bloque, ``finish_reason``,
tipo y mensaje de error, ``request-id``) se acota y se limpia de caracteres no
imprimibles antes de llegar a la terminal. Del cuerpo de una respuesta exitosa solo se
guardan los tipos de bloque: nunca el texto, el razonamiento ni las firmas.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from dotenv import dotenv_values
from pydantic import SecretStr

OK = "OK"
FAILED = "FALLA"
INCONCLUSIVE = "NO CONCLUYE"

ANTHROPIC_HOST = "api.anthropic.com"
SIGNED_THINKING = "thinking(firmado)"

_ERROR_MESSAGE_MAX = 300
_SHORT_FIELD_MAX = 60


def clean(value: object, limit: int = _SHORT_FIELD_MAX) -> str | None:
    """Un campo del proveedor en una línea, sin caracteres no imprimibles y acotado."""
    if not isinstance(value, str):
        return None
    flat = " ".join(value.split())
    return "".join(ch for ch in flat if ch.isprintable())[:limit]


def clean_reason(value: object) -> str:
    """Un ``finish_reason`` para imprimir: los desconocidos pasan del proveedor tal cual."""
    return clean(value) or "?"


@dataclass
class Exchange:
    """Un request al proveedor, visto desde la respuesta."""

    method: str
    path: str
    status: int
    request_id: str | None = None
    error_type: str | None = None
    error_message: str | None = None
    block_types: list[str] = field(default_factory=list)


class Recorder:
    """Hook de respuesta de ``httpx``: anota cada request.

    ``raise_for_provider_status`` descarta el body de un error; sin el tipo y el
    mensaje, todos los 400 se ven iguales. De una respuesta JSON exitosa guarda los
    tipos de bloque, para saber si el modelo razonó.
    """

    def __init__(self) -> None:
        self.exchanges: list[Exchange] = []

    async def __call__(self, response: httpx.Response) -> None:
        exchange = Exchange(
            method=response.request.method,
            path=response.request.url.path,
            status=response.status_code,
            request_id=clean(response.headers.get("request-id")),
        )
        if response.status_code >= httpx.codes.BAD_REQUEST:
            await response.aread()
            error = _json_field(response, "error")
            exchange.error_type = clean(error.get("type"))
            exchange.error_message = clean(error.get("message"), _ERROR_MESSAGE_MAX)
        elif response.request.method == "POST" and _is_json(response):
            await response.aread()
            exchange.block_types = _block_types(_json_list(response, "content"))
        self.exchanges.append(exchange)

    @property
    def posts(self) -> list[Exchange]:
        return [e for e in self.exchanges if e.method == "POST"]


def _is_json(response: httpx.Response) -> bool:
    return response.headers.get("content-type", "").startswith("application/json")


def _body(response: httpx.Response) -> dict[str, object]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _json_field(response: httpx.Response, key: str) -> dict[str, object]:
    value = _body(response).get(key)
    return value if isinstance(value, dict) else {}


def _json_list(response: httpx.Response, key: str) -> list[object]:
    value = _body(response).get(key)
    return value if isinstance(value, list) else []


def _block_types(blocks: list[object]) -> list[str]:
    types = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = clean(block.get("type")) or "?"
        if kind == "thinking" and block.get("signature"):
            kind = SIGNED_THINKING
        types.append(kind)
    return types


@dataclass
class CaseResult:
    """Resultado de un caso, sin texto del modelo."""

    name: str
    outcome: str
    detail: str
    finish_reason: str | None = None
    usage: dict[str, int] = field(default_factory=dict)
    block_types: list[str] = field(default_factory=list)
    exchanges: list[Exchange] = field(default_factory=list)


def format_exchange(exchange: Exchange) -> str:
    parts = [f"{exchange.method} {exchange.path} -> {exchange.status}"]
    if exchange.error_type or exchange.error_message:
        parts.append(f"{exchange.error_type or '?'}: {exchange.error_message or ''}".strip())
    if exchange.request_id:
        parts.append(f"request-id {exchange.request_id}")
    return " | ".join(parts)


def format_result(result: CaseResult) -> list[str]:
    lines = [f"[{result.outcome}] {result.name}: {result.detail}"]
    if result.usage:
        usage = ", ".join(f"{key} {value}" for key, value in result.usage.items())
        lines.append(f"    tokens: {usage}")
    if result.block_types:
        lines.append(f"    bloques: {', '.join(result.block_types)}")
    lines.extend(
        f"    {format_exchange(e)}" for e in result.exchanges if e.status >= httpx.codes.BAD_REQUEST
    )
    return lines


def load_api_key(variable: str, env_file: Path) -> SecretStr:
    """La credencial del entorno o, si no está, del ``.env``. Nunca la muestra."""
    value = os.environ.get(variable)
    if not value and env_file.is_file():
        value = dotenv_values(env_file).get(variable)
    if not value or not value.strip():
        raise SystemExit(f"falta {variable}: ponela en el entorno o en {env_file.name}")
    return SecretStr(value.strip())


def check_base_url(base_url: str, *, allow_other_host: bool) -> None:
    """``https`` y solo ``api.anthropic.com`` salvo pedido explícito.

    La credencial viaja a ese host en cada request.
    """
    parts = urlsplit(base_url)
    if parts.scheme != "https":
        raise SystemExit("la base URL tiene que ser https: la credencial viajaría en claro")
    if parts.hostname != ANTHROPIC_HOST and not allow_other_host:
        raise SystemExit(
            f"la base URL no es {ANTHROPIC_HOST}: la credencial iría a otro host. "
            "Si es a propósito, sumá --otro-host."
        )
