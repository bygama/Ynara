"""Tests del logging de la API (``app/core/logging_setup.py``) y de ``LOG_LEVEL``.

Cubre el ``JsonFormatter`` (claves fijas, ``request_id``, excepciones sin su
mensaje), el filtro que saca el query string del access log de uvicorn, la
idempotencia de ``configure_logging`` con el estado que deja ``UvicornWorker`` y el
nivel por variable de entorno. Todo con datos sintéticos.
"""

from __future__ import annotations

import io
import json
import logging
import sys
from collections.abc import Callable, Iterator

import pytest
from pydantic import ValidationError
from uvicorn.protocols.utils import get_path_with_query_string

from app.core.config import Settings
from app.core.logging_setup import (
    APP_LOGGER,
    JsonFormatter,
    StripQueryStringFilter,
    configure_logging,
)
from app.core.request_id import request_id_var

_LOGGERS = ("", APP_LOGGER, "uvicorn", "uvicorn.error", "uvicorn.access", "gunicorn.error")


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Guarda y repone handlers, filtros, nivel y propagate de los loggers tocados."""
    saved = []
    for name in _LOGGERS:
        logger = logging.getLogger(name)
        saved.append(
            (logger, list(logger.handlers), list(logger.filters), logger.level, logger.propagate)
        )
    try:
        yield
    finally:
        for logger, handlers, filters, level, propagate in saved:
            logger.handlers = handlers
            logger.filters = filters
            logger.setLevel(level)
            logger.propagate = propagate


def _record(
    msg: str = "evento sintetico",
    *,
    args: tuple[object, ...] | None = None,
    name: str = "app.test",
    level: int = logging.INFO,
    exc_info: object = None,
) -> logging.LogRecord:
    return logging.LogRecord(
        name=name,
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=exc_info,  # type: ignore[arg-type]
    )


def _own_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in logging.getLogger().handlers
        if isinstance(handler.formatter, JsonFormatter)
    ]


# ---------- JsonFormatter ----------


def test_formatter_emits_one_json_line_with_fixed_keys() -> None:
    line = JsonFormatter().format(_record("turno %s: %d ops", args=("abc123", 3)))

    assert "\n" not in line
    payload = json.loads(line)
    assert set(payload) == {"ts", "level", "logger", "message", "request_id"}
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "turno abc123: 3 ops"
    assert payload["ts"].endswith("+00:00")


def test_formatter_request_id_is_null_outside_a_request() -> None:
    payload = json.loads(JsonFormatter().format(_record()))

    assert payload["request_id"] is None


def test_formatter_includes_current_request_id() -> None:
    token = request_id_var.set("req-sintetico-1")
    try:
        payload = json.loads(JsonFormatter().format(_record()))
    finally:
        request_id_var.reset(token)

    assert payload["request_id"] == "req-sintetico-1"


def test_formatter_ignores_extra_fields() -> None:
    record = _record()
    record.user_text = "texto que no tiene que salir"

    line = JsonFormatter().format(record)

    assert "texto que no tiene que salir" not in line


def test_formatter_keeps_non_ascii_readable() -> None:
    line = JsonFormatter().format(_record("memory search: degradó"))

    assert "degradó" in line


def test_formatter_logs_exception_type_and_frames_but_not_its_message() -> None:
    secret = "contenido-sintetico-del-usuario"
    try:
        raise ValueError(secret)
    except ValueError:
        exc_info = sys.exc_info()

    line = JsonFormatter().format(_record("fallo", level=logging.ERROR, exc_info=exc_info))

    payload = json.loads(line)
    assert payload["exc_type"] == "ValueError"
    assert "test_formatter_logs_exception_type_and_frames" in payload["traceback"]
    assert secret not in line


def test_formatter_logs_the_cause_chain_without_messages() -> None:
    cause_secret = "causa-sintetica-del-usuario"
    top_secret = "error-sintetico-del-usuario"

    def _inner() -> None:
        raise ValueError(cause_secret)

    try:
        try:
            _inner()
        except ValueError as exc:
            raise RuntimeError(top_secret) from exc
    except RuntimeError:
        exc_info = sys.exc_info()

    line = JsonFormatter().format(_record("fallo", level=logging.ERROR, exc_info=exc_info))

    payload = json.loads(line)
    assert payload["exc_type"] == "RuntimeError"
    trace = payload["traceback"]
    # La causa primero, como en el traceback de Python, y cada bloque con su tipo.
    assert trace.index("[ValueError]") < trace.index("[RuntimeError]")
    assert "_inner" in trace
    assert cause_secret not in line
    assert top_secret not in line


def test_formatter_uses_exc_info_traceback_when_the_exception_has_none() -> None:
    try:
        raise ValueError("x")
    except ValueError as exc:
        tb = exc.__traceback__
        exc.__traceback__ = None
        record = _record("fallo", level=logging.ERROR, exc_info=(ValueError, exc, tb))

    trace = json.loads(JsonFormatter().format(record))["traceback"]

    assert "test_formatter_uses_exc_info_traceback_when_the_exception_has_none" in trace


def test_formatter_follows_implicit_context_unless_suppressed() -> None:
    def _raise(suppress: bool) -> None:
        try:
            raise KeyError("clave-sintetica")
        except KeyError:
            if suppress:
                raise TypeError("x") from None
            raise TypeError("x")  # noqa: B904 - el contexto implícito es el caso

    traces = {}
    for suppress in (False, True):
        try:
            _raise(suppress)
        except TypeError:
            record = _record("fallo", level=logging.ERROR, exc_info=sys.exc_info())
            traces[suppress] = json.loads(JsonFormatter().format(record))["traceback"]

    assert "[KeyError]" in traces[False]
    assert "[KeyError]" not in traces[True]
    assert "[TypeError]" in traces[True]


# ---------- StripQueryStringFilter ----------


def test_access_filter_strips_query_string() -> None:
    record = _record(
        '%s - "%s %s HTTP/%s" %d',
        args=("10.0.0.1:5000", "GET", "/v1/memory/search?q=texto-sintetico", "1.1", 200),
        name="uvicorn.access",
    )

    assert StripQueryStringFilter().filter(record) is True

    message = record.getMessage()
    assert "/v1/memory/search" in message
    assert "texto-sintetico" not in message
    assert "?" not in message


@pytest.mark.parametrize(
    ("msg", "args"),
    [
        # Handshake de WebSocket en uvicorn.error: el path es el segundo arg.
        ('%s - "WebSocket %s" 403', ("10.0.0.1:5000", "/v1/health?q=texto-sintetico")),
        ('%s - "WebSocket %s" %d', ("10.0.0.1:5000", "/v1/x?q=texto-sintetico", 404)),
        # Si uvicorn cambiara el orden de los args, el filtro sigue andando.
        ("%s %s %s", ("/v1/memory/search?q=texto-sintetico", "GET", "10.0.0.1:5000")),
    ],
)
def test_filter_strips_query_string_in_any_position(msg: str, args: tuple[object, ...]) -> None:
    record = _record(msg, args=args, name="uvicorn.error")

    StripQueryStringFilter().filter(record)

    message = record.getMessage()
    assert "texto-sintetico" not in message
    assert "?" not in message
    assert "10.0.0.1:5000" in message


def test_filter_cuts_every_str_arg_and_keeps_the_rest() -> None:
    record = _record("%s %s %s %d", args=("10.0.0.1:5000", "ruta?q=texto-sintetico", "GET", 200))

    StripQueryStringFilter().filter(record)

    assert record.args == ("10.0.0.1:5000", "ruta", "GET", 200)


@pytest.mark.parametrize(
    "path",
    [
        "/v1/memory/search",
        # Request-target en forma absoluta: uvicorn lo loguea tal como llegó.
        "http://host-sintetico/ws",
        # Sin barra inicial.
        "v1/health",
    ],
)
@pytest.mark.parametrize(
    ("name", "msg", "build_args"),
    [
        (
            "uvicorn.access",
            '%s - "%s %s HTTP/%s" %d',
            lambda p: ("10.0.0.1:5000", "GET", p, "1.1", 200),
        ),
        ("uvicorn.error", '%s - "WebSocket %s" 403', lambda p: ("10.0.0.1:5000", p)),
    ],
)
def test_filter_strips_what_uvicorn_really_logs(
    path: str, name: str, msg: str, build_args: Callable[[str], tuple[object, ...]]
) -> None:
    """Con la salida real de ``get_path_with_query_string``, no un string armado a mano."""
    logged = get_path_with_query_string(
        {"path": path, "query_string": b"q=texto-sintetico"}  # type: ignore[typeddict-item]
    )
    record = _record(msg, args=build_args(logged), name=name)

    StripQueryStringFilter().filter(record)

    assert "texto-sintetico" not in record.getMessage()


def test_access_filter_leaves_path_without_query_untouched() -> None:
    args = ("10.0.0.1:5000", "GET", "/v1/health", "1.1", 200)
    record = _record('%s - "%s %s HTTP/%s" %d', args=args, name="uvicorn.access")

    StripQueryStringFilter().filter(record)

    assert record.args == args


def test_access_filter_tolerates_other_shapes() -> None:
    record = _record("mensaje sin args")

    assert StripQueryStringFilter().filter(record) is True
    assert record.getMessage() == "mensaje sin args"


# ---------- configure_logging ----------


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_sets_app_level_and_leaves_root_level() -> None:
    root_level = logging.getLogger().level

    configure_logging("DEBUG")

    assert logging.getLogger(APP_LOGGER).level == logging.DEBUG
    assert logging.getLogger().level == root_level


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_filters_access_and_error_loggers() -> None:
    configure_logging("INFO")
    configure_logging("INFO")

    for name in ("uvicorn.access", "uvicorn.error"):
        filters = logging.getLogger(name).filters
        assert sum(isinstance(f, StripQueryStringFilter) for f in filters) == 1, name


@pytest.mark.usefixtures("restore_logging")
def test_configure_logging_is_idempotent() -> None:
    configure_logging("INFO")
    configure_logging("INFO")

    assert len(_own_handlers()) == 1
    access_filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(f, StripQueryStringFilter) for f in access_filters) == 1


@pytest.mark.usefixtures("restore_logging")
def test_under_uvicorn_worker_each_record_is_emitted_once_as_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Simula el estado de ``UvicornWorker``: handlers de gunicorn y propagate=False."""
    root = logging.getLogger()
    root.handlers = []
    error_stream, access_stream = io.StringIO(), io.StringIO()
    error_handler = logging.StreamHandler(error_stream)
    access_handler = logging.StreamHandler(access_stream)
    for name, handler in (("uvicorn.error", error_handler), ("uvicorn.access", access_handler)):
        server_logger = logging.getLogger(name)
        server_logger.handlers = [handler]
        server_logger.setLevel(logging.INFO)
        server_logger.propagate = False
    logging.getLogger("gunicorn.error").handlers = [error_handler]

    configure_logging("INFO")
    configure_logging("INFO")

    assert len(_own_handlers()) == 1
    capsys.readouterr()

    logging.getLogger("uvicorn.error").info("Started server process")
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/%s" %d', "10.0.0.1:5000", "GET", "/v1/memory/search?q=x", "1.1", 200
    )

    error_lines = error_stream.getvalue().splitlines()
    access_lines = access_stream.getvalue().splitlines()
    assert len(error_lines) == 1
    assert json.loads(error_lines[0])["message"] == "Started server process"
    assert len(access_lines) == 1
    assert "?q=" not in json.loads(access_lines[0])["message"]
    # Los loggers del servidor no propagan: nada llega al handler del raíz (stderr).
    assert capsys.readouterr().err == ""


@pytest.mark.usefixtures("restore_logging")
def test_app_records_reach_root_handler_once(capsys: pytest.CaptureFixture[str]) -> None:
    logging.getLogger().handlers = []
    configure_logging("INFO")
    configure_logging("INFO")

    logging.getLogger("app.services.sintetico").info("evento %s", "id-opaco")
    logging.getLogger("app.services.sintetico").debug("debug que no sale")

    lines = [line for line in capsys.readouterr().err.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["logger"] == "app.services.sintetico"
    assert payload["message"] == "evento id-opaco"


# ---------- LOG_LEVEL ----------


def _settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        DATABASE_URL="postgresql://test:test@localhost/test",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET="x" * 40,
    )


def test_log_level_defaults_to_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    assert _settings().log_level == "INFO"


@pytest.mark.parametrize(
    ("raw", "expected"), [("debug", "DEBUG"), (" Warning ", "WARNING"), ("", "INFO")]
)
def test_log_level_from_env_is_case_insensitive(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: str
) -> None:
    monkeypatch.setenv("LOG_LEVEL", raw)

    assert _settings().log_level == expected


def test_log_level_rejects_unknown_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "verbose")

    with pytest.raises(ValidationError):
        _settings()


@pytest.mark.usefixtures("restore_logging")
def test_log_level_from_env_reaches_app_logger(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "error")

    configure_logging(_settings().log_level)

    assert logging.getLogger(APP_LOGGER).level == logging.ERROR
