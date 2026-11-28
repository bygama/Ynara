"""Logging de la API: una línea JSON por registro, con el id del pedido.

``configure_logging`` se llama una vez al importar ``app.main`` (antes de crear la
app). Bajo gunicorn sin ``--preload`` eso pasa en cada worker, después de que
``UvicornWorker`` le pasó a ``uvicorn.error`` y ``uvicorn.access`` los handlers de
gunicorn (con ``propagate=False``). Celery no importa ``app.main`` y arma su propio
logging: esto no lo toca.

Qué hace, sin dependencias nuevas (stdlib):

- Agrega UN handler a stderr en el logger raíz, con ``JsonFormatter``. Es
  idempotente: si ya hay uno nuestro (reimport, ``uvicorn --reload``), no agrega
  otro. No borra handlers ajenos ni usa ``dictConfig`` (desactivaría los loggers
  que ya existen).
- ``LOG_LEVEL`` gobierna el logger ``app`` (todo el código del backend). El raíz
  queda como está (``WARNING`` por default): las librerías de terceros no suben a
  ``INFO``/``DEBUG``, que en las de HTTP incluye URLs y detalles del request.
- A los handlers que uvicorn y gunicorn ya tienen les cambia solo el formatter por
  el JSON: no se suman handlers ni se toca ``propagate``, así nada sale dos veces.
- Le agrega a ``uvicorn.access`` y a ``uvicorn.error`` un filtro que corta en el
  ``?`` los args de cada registro: ``GET /v1/memory/search?q=...`` lleva texto
  del usuario (regla #4), y uvicorn loguea el handshake de WebSocket con su
  query string en ``uvicorn.error``.

Qué emite: ``ts`` (ISO 8601 UTC), ``level``, ``logger``, ``message``,
``request_id`` (``null`` fuera de un request) y, si hay excepción, ``exc_type`` y
``traceback`` (solo los frames, también los de la cadena de causas, cada bloque
con el tipo de su excepción). El mensaje de ninguna excepción sale: un
``str(exc)`` puede traer contenido del usuario, el mismo motivo por el que el
scrubber de Sentry lo ofusca (``app/core/observability.py``).
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

from app.core.request_id import request_id_var

# Logger cuyo nivel fija ``LOG_LEVEL``: el padre de todos los ``getLogger(__name__)``
# del backend.
APP_LOGGER = "app"

# Loggers del servidor cuyos handlers (si ya existen) pasan al formato JSON.
_SERVER_LOGGERS = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "gunicorn.error",
    "gunicorn.access",
)
# Loggers donde uvicorn escribe paths con su query string: el access log de HTTP
# y el handshake de WebSocket (``'%s - "WebSocket %s" 403'``, en ``uvicorn.error``).
_QUERY_STRING_LOGGERS = ("uvicorn.access", "uvicorn.error")


class JsonFormatter(logging.Formatter):
    """Una línea JSON por registro, con un set fijo de claves.

    No se agregan los ``extra=`` del registro: el set fijo hace que ninguna clave
    nueva viaje a los logs sin pasar por una review.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            exc_type, exc, tb = record.exc_info
            payload["exc_type"] = exc_type.__name__
            payload["traceback"] = (
                _format_chain(exc, tb) if exc is not None else "".join(traceback.format_tb(tb))
            )
        return json.dumps(payload, ensure_ascii=False)


def _format_chain(exc: BaseException, tb: TracebackType | None) -> str:
    """Frames de la excepción y de sus causas, sin ningún mensaje.

    Mismo orden que el traceback de Python: primero la causa más interna, al final
    la excepción logueada. Cada bloque arranca con ``[Tipo]``. Sigue ``__cause__``
    (``raise X from Y``) y, si no se suprimió, ``__context__`` (una excepción
    lanzada mientras se manejaba otra). Una cadena con ciclo se corta en la
    primera excepción repetida. Si la logueada no trae ``__traceback__`` (un
    ``exc_info`` armado a mano), sus frames salen del ``tb`` de ``exc_info``.
    """
    chain: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        if current.__cause__ is not None:
            current = current.__cause__
        elif not current.__suppress_context__:
            current = current.__context__
        else:
            current = None
    return "\n".join(
        f"[{type(item).__name__}]\n"
        + "".join(traceback.format_tb(item.__traceback__ or (tb if item is exc else None)))
        for item in reversed(chain)
    )


class StripQueryStringFilter(logging.Filter):
    """Saca el query string de los paths que loguea uvicorn (regla #4).

    Uvicorn loguea ``get_path_with_query_string(scope)``; el query string puede
    traer texto del usuario (``/v1/memory/search?q=...``). Se queda el path solo.
    Corta en el ``?`` todo arg ``str`` del registro, en cualquier posición. No
    exige que empiece con ``/``: uvicorn loguea el request-target tal como llegó
    (``http%3A//host/ruta?q=...`` en forma absoluta, o sin barra inicial). El
    cliente (``ip:puerto``), el método, la versión y el status no llevan ``?``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and any(_has_query(arg) for arg in args):
            record.args = tuple(arg.split("?", 1)[0] if _has_query(arg) else arg for arg in args)
        return True


def _has_query(value: object) -> bool:
    return isinstance(value, str) and "?" in value


class _StderrHandler(logging.StreamHandler):
    """``StreamHandler`` que resuelve ``sys.stderr`` en cada escritura.

    Si se guardara el stream al crear el handler, un reemplazo posterior de
    ``sys.stderr`` (la captura de pytest, por ejemplo) dejaría al handler
    escribiendo en un stream cerrado.
    """

    @property
    def stream(self) -> Any:  # type: ignore[override]
        return sys.stderr

    @stream.setter
    def stream(self, _value: Any) -> None:
        # ``StreamHandler.__init__`` asigna el stream; se ignora a propósito.
        pass


# Marca del handler propio en el raíz: con ella ``configure_logging`` es idempotente.
_HANDLER_MARK = "_ynara_json_handler"


def _has_own_handler(logger: logging.Logger) -> bool:
    return any(getattr(handler, _HANDLER_MARK, False) for handler in logger.handlers)


def configure_logging(level: str) -> None:
    """Deja el logging de la API en JSON con ``request_id``; idempotente.

    ``level`` es el nombre de un nivel de stdlib (``"INFO"``, ``"DEBUG"``…), el
    ``LOG_LEVEL`` ya validado por ``Settings``. Se aplica al logger ``app``.

    El efecto es de proceso: en pytest, cualquier test que importe ``app.main``
    deja el handler JSON en el raíz y el logger ``app`` en ``LOG_LEVEL`` para el
    resto de la corrida. ``caplog`` sigue capturando (suma su propio handler),
    pero para ver ``DEBUG`` de ``app.*`` hay que pedirlo sobre ese logger:
    ``caplog.set_level(logging.DEBUG, logger="app")``.
    """
    formatter = JsonFormatter()

    root = logging.getLogger()
    if not _has_own_handler(root):
        handler = _StderrHandler()
        handler.setFormatter(formatter)
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)

    logging.getLogger(APP_LOGGER).setLevel(level)

    for name in _SERVER_LOGGERS:
        for server_handler in logging.getLogger(name).handlers:
            server_handler.setFormatter(formatter)

    for name in _QUERY_STRING_LOGGERS:
        logger = logging.getLogger(name)
        if not any(isinstance(f, StripQueryStringFilter) for f in logger.filters):
            logger.addFilter(StripQueryStringFilter())
