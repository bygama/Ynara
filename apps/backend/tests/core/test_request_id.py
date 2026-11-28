"""Tests del identificador de pedido (``app/core/request_id.py``).

El middleware genera un id cuando no viene, respeta uno válido, descarta uno
inválido sin ecoarlo, lo deja en el contexto mientras corre el request (lo que lee
el formatter de los logs) y lo devuelve en ``X-Request-ID``. Sin DB ni Redis: una
app ASGI mínima y el liveness de la app real.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
import pytest
from httpx import ASGITransport
from starlette.types import Receive, Scope, Send

from app.core.config import get_settings
from app.core.logging_setup import JsonFormatter
from app.core.request_id import (
    REQUEST_ID_HEADER,
    REQUEST_ID_MAX_LENGTH,
    RequestIdMiddleware,
    parse_request_id,
    request_id_var,
)

_GENERATED = re.compile(r"[0-9a-f]{32}")


async def _echo_context_app(scope: Scope, receive: Receive, send: Send) -> None:
    """App ASGI mínima: responde con el id que ve en el contexto."""
    body = (request_id_var.get() or "").encode("ascii")
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [(b"content-type", b"text/plain")],
        }
    )
    await send({"type": "http.response.body", "body": body})


async def _get(headers: dict[str, str] | None = None) -> httpx.Response:
    transport = ASGITransport(app=RequestIdMiddleware(_echo_context_app))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/", headers=headers)


async def test_generates_an_id_when_missing() -> None:
    resp = await _get()

    request_id = resp.headers[REQUEST_ID_HEADER]
    assert _GENERATED.fullmatch(request_id)
    assert resp.text == request_id


async def test_generates_a_different_id_per_request() -> None:
    first = await _get()
    second = await _get()

    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_keeps_a_valid_incoming_id() -> None:
    incoming = "cf-ray_8a1b2c3d.4e5f-ABC"

    resp = await _get({REQUEST_ID_HEADER: incoming})

    assert resp.headers[REQUEST_ID_HEADER] == incoming
    assert resp.text == incoming


@pytest.mark.parametrize(
    "invalid",
    [
        "con espacios",
        "barra\\ninvertida",
        '{"inyeccion":1}',
        "x" * (REQUEST_ID_MAX_LENGTH + 1),
        "",
    ],
)
async def test_replaces_an_invalid_incoming_id_without_echoing_it(invalid: str) -> None:
    resp = await _get({REQUEST_ID_HEADER: invalid})

    request_id = resp.headers[REQUEST_ID_HEADER]
    assert request_id != invalid
    assert _GENERATED.fullmatch(request_id)
    assert resp.text == request_id


async def test_context_is_reset_after_the_request() -> None:
    await _get({REQUEST_ID_HEADER: "req-sintetico-2"})

    assert request_id_var.get() is None


async def test_non_http_scopes_pass_through() -> None:
    seen: list[str | None] = []

    async def lifespan_app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(request_id_var.get())

    async def receive() -> dict[str, str]:
        return {"type": "lifespan.startup"}

    async def send(_message: object) -> None:
        return None

    await RequestIdMiddleware(lifespan_app)({"type": "lifespan"}, receive, send)  # type: ignore[arg-type]

    assert seen == [None]


@pytest.mark.parametrize(
    "raw",
    [
        "abc\ndef",
        "abc\rdef",
        "abc\r\nX-Inyectado: 1",
        "abc\n",
        "abc\tdef",
        "abc\x00def",
        b"abc\ndef",
        b"abc\r\ndef",
    ],
)
def test_parse_request_id_rejects_control_characters(raw: str | bytes) -> None:
    """Un salto de línea real (no la barra invertida) no pasa: ni al final.

    ``fullmatch`` y no ``match`` con ``$``: ``$`` acepta un ``\n`` final.
    """
    assert parse_request_id(raw) is None


async def test_raw_scope_with_newline_in_header_is_not_echoed() -> None:
    """Un cliente ASGI no valida headers como httpx: el scope crudo puede traerlo."""
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-request-id", b"abc\r\nX-Inyectado: 1")],
    }
    await RequestIdMiddleware(_echo_context_app)(scope, receive, send)  # type: ignore[arg-type]

    start = next(m for m in sent if m["type"] == "http.response.start")
    headers = dict(start["headers"])  # type: ignore[arg-type]
    assert _GENERATED.fullmatch(headers[b"x-request-id"].decode("ascii"))


async def test_logs_inside_a_request_carry_its_id() -> None:
    """De punta a punta: un log emitido mientras corre el pedido sale con su id."""
    lines: list[str] = []

    class _ListHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(self.format(record))

    handler = _ListHandler()
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("app.tests.request_id")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    async def logging_app(scope: Scope, receive: Receive, send: Send) -> None:
        logger.info("dentro del pedido")
        await _echo_context_app(scope, receive, send)

    try:
        transport = ASGITransport(app=RequestIdMiddleware(logging_app))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/", headers={REQUEST_ID_HEADER: "req-sintetico-4"})
        logger.info("fuera del pedido")
    finally:
        logger.removeHandler(handler)

    assert resp.headers[REQUEST_ID_HEADER] == "req-sintetico-4"
    inside, outside = (json.loads(line) for line in lines)
    assert inside["message"] == "dentro del pedido"
    assert inside["request_id"] == "req-sintetico-4"
    assert outside["request_id"] is None


def test_parse_request_id_bounds() -> None:
    assert parse_request_id(None) is None
    assert parse_request_id(b"abc-123") == "abc-123"
    assert parse_request_id("a" * REQUEST_ID_MAX_LENGTH) == "a" * REQUEST_ID_MAX_LENGTH
    assert parse_request_id("a" * (REQUEST_ID_MAX_LENGTH + 1)) is None
    assert parse_request_id("ñandú") is None
    assert parse_request_id("\xff".encode("latin-1")) is None


async def test_real_app_returns_request_id_header() -> None:
    """La app real registra el middleware: el liveness devuelve el id."""
    from app.main import app

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        generated = await client.get("/v1/health")
        kept = await client.get("/v1/health", headers={REQUEST_ID_HEADER: "req-sintetico-3"})

    assert _GENERATED.fullmatch(generated.headers[REQUEST_ID_HEADER])
    assert kept.headers[REQUEST_ID_HEADER] == "req-sintetico-3"
    # Los headers de seguridad siguen saliendo con el middleware nuevo por fuera.
    assert kept.headers["X-Content-Type-Options"] == "nosniff"


async def test_real_app_exposes_request_id_to_the_browser() -> None:
    """CORS lo expone: sin eso el JS de la web no puede leer el header."""
    from app.main import app

    origin = get_settings().cors_origins[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/v1/health", headers={"Origin": origin})

    exposed = resp.headers["Access-Control-Expose-Headers"]
    assert REQUEST_ID_HEADER.lower() in exposed.lower()
    assert _GENERATED.fullmatch(resp.headers[REQUEST_ID_HEADER])


async def test_real_app_lets_the_browser_send_a_request_id() -> None:
    """El preflight acepta X-Request-ID: si no, el navegador corta el pedido."""
    from app.main import app

    origin = get_settings().cors_origins[0]
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.options(
            "/v1/health",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "x-request-id",
            },
        )

    assert resp.status_code == 200
    allowed = resp.headers["Access-Control-Allow-Headers"]
    assert REQUEST_ID_HEADER.lower() in allowed.lower()
