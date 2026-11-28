"""Identificador de pedido: uno por request HTTP, en el contexto y en la respuesta.

``RequestIdMiddleware`` toma el ``X-Request-ID`` entrante si es válido o genera uno,
lo deja en ``request_id_var`` mientras corre el request (el formatter de los logs lo
lee de ahí, ver ``app/core/logging_setup.py``) y lo devuelve en el header
``X-Request-ID`` de la respuesta. Así una línea de log se cruza con el pedido que la
generó y con lo que ve el cliente.

Validación del header entrante: solo ``[A-Za-z0-9._-]``, de 1 a
``REQUEST_ID_MAX_LENGTH`` caracteres. Un valor fuera de eso se descarta y se genera
uno nuevo: no se loguea ni se ecoa (podría traer un salto de línea para inyectar
líneas de log, o texto arbitrario del cliente, regla #4).

El id sirve para correlacionar, no para auditar: un cliente puede mandar el que
quiera (dentro del formato), repetirlo o copiar el de otro pedido. Nada que decida
permisos o que quede en ``audit_log`` se apoya en él.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_MAX_LENGTH = 64
_REQUEST_ID_PATTERN = re.compile(rf"[A-Za-z0-9._-]{{1,{REQUEST_ID_MAX_LENGTH}}}")
_REQUEST_ID_HEADER_KEY = REQUEST_ID_HEADER.lower().encode("latin-1")

# ``None`` fuera de un request (arranque, lifespan, tests sin middleware): el
# formatter lo emite como ``null``.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id() -> str:
    """Id nuevo y opaco: 32 caracteres hex de un UUID4."""
    return uuid.uuid4().hex


def parse_request_id(raw: bytes | str | None) -> str | None:
    """El id entrante si es válido; ``None`` si falta o no cumple el formato."""
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("ascii")
        except UnicodeDecodeError:
            return None
    if _REQUEST_ID_PATTERN.fullmatch(raw) is None:
        return None
    return raw


def _incoming_request_id(scope: Scope) -> bytes | None:
    for name, value in scope.get("headers") or ():
        if name == _REQUEST_ID_HEADER_KEY:
            return value
    return None


class RequestIdMiddleware:
    """Middleware ASGI puro: fija el id del pedido y lo devuelve en la respuesta.

    ASGI puro (como ``SecurityHeadersMiddleware`` en ``app/main.py``) para no
    bufferear el body: el stream SSE de ``/chat/stream`` sigue andando y sus logs
    llevan el id, porque el contexto vive hasta que termina la respuesta.

    Se registra último en ``app/main.py`` para quedar por fuera del resto de los
    middlewares de usuario: los logs de CORS y de los headers de seguridad también
    llevan el id. Mismo gap aceptado que los headers de seguridad: un 500 no
    manejado lo arma el ``ServerErrorMiddleware`` de Starlette, que está por fuera,
    así que esa respuesta no lleva ``X-Request-ID`` y el log del servidor que la
    acompaña sale con ``request_id`` nulo (Sentry tiene su propio evento).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = parse_request_id(_incoming_request_id(scope)) or new_request_id()

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        token = request_id_var.set(request_id)
        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)
