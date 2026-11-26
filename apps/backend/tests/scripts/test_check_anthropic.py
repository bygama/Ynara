"""Tests de la prueba de humo de Anthropic (``scripts/check_anthropic.py``).

Sin red: cada caso corre contra un ``httpx.MockTransport`` que contesta con la forma
de la Messages API y datos sintéticos. Cubren:

- el modo lista (no arma cliente) y los flags (sin abreviaturas, https y host
  permitido);
- la fábrica real, que cuelga el ``Recorder`` del cliente;
- el camino de ``--enviar`` de punta a punta, sin ``Settings``;
- el corte por ``health``;
- el resultado de cada caso y la forma del request que verifica;
- cuándo un caso con tools no concluye (sin tool o sin razonamiento firmado);
- la sonda de esfuerzo: solo cuenta si pasa el control con el mismo esfuerzo, y solo un
  400 de request inválido en la confirmación (no un contexto excedido) es respuesta;
- el detalle de un error del proveedor, acotado y limpio;
- la credencial, el texto del modelo, el razonamiento y las firmas, que no se imprimen.

El script vive fuera del paquete ``app``, así que se importa por path con
``importlib``, como ``test_check_doc_catalogs.py``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from app.llm.clients.providers._anthropic_wire import THINKING_BINDING_BETA

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_anthropic.py"
_API_KEY = "sk-test-anthropic-smoke-0123456789"
_MODEL_ID = "modelo-humo-test"
_THINKING_TEXT = "razonamiento-sintetico-humo"
_SIGNATURE = "firma-sintetica-humo"
_REPLY_TEXT = "respuesta-sintetica-humo"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_anthropic", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # ``dataclass`` busca el módulo en ``sys.modules`` para resolver las anotaciones.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ca = _load_module()

Handler = Callable[[httpx.Request], httpx.Response]


# --- respuestas sintéticas ------------------------------------------------


def _message(
    content: list[dict[str, Any]], *, stop_reason: str = "end_turn", **usage: int
) -> dict[str, Any]:
    return {
        "id": "msg_humo",
        "type": "message",
        "role": "assistant",
        "model": _MODEL_ID,
        "content": content,
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 12, "output_tokens": 4, **usage},
    }


_THINKING_BLOCK = {"type": "thinking", "thinking": _THINKING_TEXT, "signature": _SIGNATURE}
_TEXT_BLOCK = {"type": "text", "text": _REPLY_TEXT}
_TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "smoke__echo", "input": {"texto": "hola"}}
_TOOL_REPLY = _message([_THINKING_BLOCK, _TOOL_USE], stop_reason="tool_use")


def _sse(*events: dict[str, Any]) -> bytes:
    lines = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    return ("\n".join(lines) + "\n").encode()


_STREAM = _sse(
    {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 0}}},
    {
        "type": "content_block_start",
        "index": 0,
        "content_block": {"type": "thinking", "thinking": "", "signature": ""},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "thinking_delta", "thinking": _THINKING_TEXT},
    },
    {
        "type": "content_block_delta",
        "index": 0,
        "delta": {"type": "signature_delta", "signature": _SIGNATURE},
    },
    {"type": "content_block_stop", "index": 0},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {
        "type": "content_block_delta",
        "index": 1,
        "delta": {"type": "text_delta", "text": _REPLY_TEXT},
    },
    {"type": "content_block_stop", "index": 1},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
    {"type": "message_stop"},
)


def _has_tool_result(payload: dict[str, Any]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") == "tool_result"
        for message in payload["messages"]
        for block in (message["content"] if isinstance(message["content"], list) else [])
    )


def _api(
    *,
    health_status: int = 200,
    tool_use_reply: dict[str, Any] | None = None,
    after_tool: Callable[[dict[str, Any]], httpx.Response] | None = None,
    cache_read: int = 900,
) -> tuple[Handler, list[dict[str, Any]]]:
    """Un doble de la API: ``/models``, ``/messages`` con tools, sin tools y por SSE."""
    payloads: list[dict[str, Any]] = []
    first_tool_reply = tool_use_reply or _TOOL_REPLY

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-api-key"] == _API_KEY
        if request.url.path.endswith("/models"):
            return httpx.Response(health_status, json={"data": []})
        payload = json.loads(request.content)
        payloads.append(payload)
        if payload.get("stream"):
            return httpx.Response(200, content=_STREAM)
        if "tools" in payload:
            if _has_tool_result(payload):
                if after_tool is not None:
                    return after_tool(payload)
                return httpx.Response(200, json=_message([_THINKING_BLOCK, _TEXT_BLOCK]))
            return httpx.Response(200, json=first_tool_reply)
        if "system" not in payload:
            return httpx.Response(200, json=_message([_THINKING_BLOCK, _TEXT_BLOCK]))
        # El caso de caché: la primera llamada con system crea, la segunda lee.
        second = sum(1 for p in payloads if "system" in p) % 2 == 0
        return httpx.Response(
            200,
            json=_message(
                [_THINKING_BLOCK, _TEXT_BLOCK],
                cache_read_input_tokens=cache_read if second else 0,
                cache_creation_input_tokens=0 if second else 900,
            ),
        )

    return handler, payloads


def _factory(handler: Handler) -> Any:
    def make(thinking: Any, recorder: Any) -> Any:
        return ca.AnthropicClient(
            name="claude",
            base_url="https://api.anthropic.test/v1",
            api_key=SecretStr(_API_KEY),
            model_map={"agente": _MODEL_ID},
            thinking={"agente": thinking},
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handler), event_hooks={"response": [recorder]}
            ),
        )

    return make


def _error(status: int, error_type: str, message: str) -> httpx.Response:
    return httpx.Response(
        status,
        json={"type": "error", "error": {"type": error_type, "message": message}},
        headers={"request-id": "req_humo_1"},
    )


def _bad_request(message: str) -> httpx.Response:
    return _error(400, "invalid_request_error", message)


def _after_control(reply: httpx.Response) -> Callable[[dict[str, Any]], httpx.Response]:
    """La confirmación del control pasa; la de la sonda recibe ``reply``."""
    calls: list[int] = []

    def after_tool(_: dict[str, Any]) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(200, json=_message([_THINKING_BLOCK, _TEXT_BLOCK]))
        return reply

    return after_tool


async def _run(handler: Handler, names: list[str]) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = await ca.run_checks(_factory(handler), names, out=lines.append)
    return code, lines


# --- flags, modo lista y --enviar --------------------------------------------


def test_without_enviar_it_lists_the_cases_and_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def no_client(**_: Any) -> Any:
        raise AssertionError("el modo lista no arma un cliente")

    monkeypatch.setattr(ca, "api_client_factory", no_client)
    assert ca.main(["--modelo", _MODEL_ID]) == 0
    out = capsys.readouterr().out
    assert "no se mandó nada" in out
    assert all(name in out for name in ca.CASES)


def test_an_abbreviated_flag_does_not_turn_on_enviar(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_client(**_: Any) -> Any:
        raise AssertionError("una abreviatura no arma un cliente")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY_CLAUDE", _API_KEY)
    monkeypatch.setattr(ca, "api_client_factory", no_client)
    with pytest.raises(SystemExit) as excinfo:
        ca.main(["--modelo", _MODEL_ID, "--env"])
    assert excinfo.value.code == 2


def test_enviar_runs_the_cases_with_the_key_and_without_settings(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    handler, payloads = _api()
    seen: dict[str, Any] = {}

    def fake_factory(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return _factory(handler)

    def no_settings(*_: Any, **__: Any) -> Any:
        raise AssertionError("el script no usa Settings")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY_CLAUDE", _API_KEY)
    monkeypatch.setattr("app.core.config.Settings.__init__", no_settings)
    monkeypatch.setattr("app.core.config.get_settings", no_settings)
    monkeypatch.setattr(ca, "api_client_factory", fake_factory)
    code = ca.main(["--modelo", _MODEL_ID, "--enviar", "--caso", "adaptativo"])
    assert code == 0
    assert seen["api_key"].get_secret_value() == _API_KEY
    assert seen["base_url"] == "https://api.anthropic.com/v1"
    assert seen["model_id"] == _MODEL_ID
    assert len(payloads) == 1
    out = capsys.readouterr().out
    assert "[OK] adaptativo" in out
    assert _API_KEY not in out


def test_another_host_needs_an_explicit_flag(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def no_client(**_: Any) -> Any:
        raise AssertionError("no arma un cliente contra otro host sin --otro-host")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LLM_API_KEY_CLAUDE", _API_KEY)
    monkeypatch.setattr(ca, "api_client_factory", no_client)
    with pytest.raises(SystemExit) as excinfo:
        ca.main(["--modelo", _MODEL_ID, "--enviar", "--base-url", "https://otro.example/v1"])
    assert "--otro-host" in str(excinfo.value)
    ca.check_base_url("https://otro.example/v1", allow_other_host=True)


@pytest.mark.parametrize("allow_other_host", [False, True])
def test_the_base_url_has_to_be_https(allow_other_host: bool) -> None:
    with pytest.raises(SystemExit) as excinfo:
        ca.check_base_url("http://api.anthropic.com/v1", allow_other_host=allow_other_host)
    assert "https" in str(excinfo.value)


async def test_the_real_factory_hooks_the_recorder_into_the_client() -> None:
    make = ca.api_client_factory(
        base_url=ca.DEFAULT_BASE_URL,
        api_key=SecretStr(_API_KEY),
        model_id=_MODEL_ID,
        name=ca.DEFAULT_NAME,
    )
    recorder = ca.Recorder()
    client = make(ca._ADAPTIVE, recorder)
    try:
        assert isinstance(client, ca.AnthropicClient)
        assert recorder in client._http.event_hooks["response"]
    finally:
        await client.aclose()


async def test_a_failed_health_check_stops_before_any_message() -> None:
    handler, payloads = _api(health_status=401)
    code, lines = await _run(handler, list(ca.CASES))
    assert code == 1
    assert payloads == []
    assert lines[0].startswith("[FALLA] health")
    assert "GET /v1/models -> 401" in lines[1]


# --- casos -----------------------------------------------------------------


async def test_every_case_passes_against_well_formed_replies() -> None:
    handler, _ = _api()
    code, lines = await _run(handler, list(ca.CASES))
    assert code == 0
    results = [line for line in lines if line.startswith("[")]
    assert results[0] == "[OK] health: GET /models respondió 200"
    for name in ca.CASES:
        assert any(line.startswith(f"[OK] {name}:") for line in results), name


async def test_the_adaptive_case_sends_the_beta_header_and_reports_block_types() -> None:
    seen: list[httpx.Request] = []
    handler, _ = _api()

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    _, lines = await _run(spy, ["adaptativo"])
    post = next(r for r in seen if r.method == "POST")
    assert post.headers["anthropic-beta"] == THINKING_BINDING_BETA
    assert json.loads(post.content)["max_tokens"] == 1024
    assert "    bloques: thinking(firmado), text" in lines


async def test_the_tool_round_replays_the_signed_blocks_of_the_first_call() -> None:
    handler, payloads = _api()
    _, lines = await _run(handler, ["vuelta_con_tools"])
    assert lines[1] == "[OK] vuelta_con_tools: 1 tool, 2 llamadas, finish_reason stop"
    assert lines[2] == "    bloques: thinking(firmado), tool_use"
    replayed = payloads[1]["messages"][1]["content"]
    assert replayed[0] == _THINKING_BLOCK


async def test_a_tool_round_without_a_tool_call_does_not_conclude() -> None:
    handler, _ = _api(tool_use_reply=_message([_TEXT_BLOCK]))
    code, lines = await _run(handler, ["vuelta_con_tools", "confirmacion_forzada"])
    assert code == 0
    assert lines[1] == "[NO CONCLUYE] vuelta_con_tools: el modelo no llamó a la tool"
    forced = "[NO CONCLUYE] confirmacion_forzada: el modelo no llamó a la tool"
    assert forced in lines


@pytest.mark.parametrize("case", ["vuelta_con_tools", "confirmacion_forzada"])
async def test_a_tool_case_without_signed_thinking_does_not_conclude(case: str) -> None:
    handler, _ = _api(tool_use_reply=_message([_TOOL_USE], stop_reason="tool_use"))
    code, lines = await _run(handler, [case])
    assert code == 0
    assert lines[1] == (
        f"[NO CONCLUYE] {case}: la primera respuesta no trajo razonamiento firmado: "
        "no hubo bloques que reenviar"
    )


async def test_the_forced_confirmation_goes_with_tool_choice_none_and_thinking_off() -> None:
    handler, payloads = _api()
    _, lines = await _run(handler, ["confirmacion_forzada"])
    assert lines[1].startswith("[OK] confirmacion_forzada: confirmación forzada aceptada")
    forced = payloads[1]
    assert forced["tool_choice"] == {"type": "none"}
    assert forced["thinking"] == {"type": "disabled"}
    assert forced["output_config"] == {"effort": "low"}


async def test_the_effort_probe_reports_that_the_rule_stays_on_a_400() -> None:
    handler, payloads = _api(after_tool=_after_control(_bad_request("effort no coincide")))
    code, lines = await _run(handler, ["cambio_de_esfuerzo"])
    assert code == 0
    assert lines[1] == (
        "[OK] cambio_de_esfuerzo: la API rechaza el cambio de esfuerzo (400): la regla queda"
    )
    assert "effort no coincide" in lines[-1]
    # El control (mismo esfuerzo) va primero; la sonda, después.
    assert [p["output_config"]["effort"] for p in payloads] == ["low", "low", "medium", "low"]


async def test_the_effort_probe_does_not_conclude_when_the_control_fails() -> None:
    handler, payloads = _api(after_tool=lambda _: _bad_request("la confirmación no pasa"))
    code, lines = await _run(handler, ["cambio_de_esfuerzo"])
    assert code == 0
    assert lines[1] == (
        "[NO CONCLUYE] cambio_de_esfuerzo: el control con el mismo esfuerzo no pasó "
        "(FALLA): no concluye"
    )
    assert len(payloads) == 2


async def test_the_effort_probe_does_not_take_a_context_overflow_as_an_answer() -> None:
    overflow = _bad_request("prompt is too long: exceeds the context window")
    handler, _ = _api(after_tool=_after_control(overflow))
    code, lines = await _run(handler, ["cambio_de_esfuerzo"])
    assert code == 1
    assert lines[1].startswith("[FALLA] cambio_de_esfuerzo: LlmContextOverflowError")


async def test_the_effort_probe_does_not_conclude_without_signed_thinking() -> None:
    handler, _ = _api(tool_use_reply=_message([_TOOL_USE], stop_reason="tool_use"))
    code, lines = await _run(handler, ["cambio_de_esfuerzo"])
    assert code == 0
    assert lines[1].startswith("[NO CONCLUYE] cambio_de_esfuerzo: el control con el mismo")


@pytest.mark.parametrize(
    "reply",
    [
        _error(529, "overloaded_error", "sobrecarga"),
        _error(429, "rate_limit_error", "demasiados pedidos"),
    ],
)
async def test_the_effort_probe_fails_on_any_other_error(reply: httpx.Response) -> None:
    handler, _ = _api(after_tool=_after_control(reply))
    code, lines = await _run(handler, ["cambio_de_esfuerzo"])
    assert code == 1
    assert lines[1].startswith("[FALLA] cambio_de_esfuerzo:")


async def test_the_effort_probe_reports_that_the_rule_can_go_when_the_api_accepts_it() -> None:
    handler, _ = _api()
    _, lines = await _run(handler, ["cambio_de_esfuerzo"])
    assert lines[1] == (
        "[OK] cambio_de_esfuerzo: la API acepta el cambio de esfuerzo: la regla se puede relajar"
    )


async def test_the_cache_case_reads_the_second_call_and_marks_the_fixed_system() -> None:
    handler, payloads = _api()
    _, lines = await _run(handler, ["cache"])
    assert lines[1] == "[OK] cache: la segunda llamada leyó 900 tokens de la caché"
    system = payloads[0]["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in system[1]


async def test_a_cache_that_is_created_but_not_read_fails() -> None:
    handler, _ = _api(cache_read=0)
    code, lines = await _run(handler, ["cache"])
    assert code == 1
    assert lines[1] == "[FALLA] cache: creó 900 tokens de caché y la segunda no los leyó"


async def test_a_cache_neither_created_nor_read_does_not_conclude() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json=_message([_TEXT_BLOCK]))

    code, lines = await _run(handler, ["cache"])
    assert code == 0
    assert lines[1].startswith("[NO CONCLUYE] cache: ninguna llamada creó ni leyó caché")


# --- errores del proveedor ------------------------------------------------------


async def test_a_provider_error_shows_its_type_message_and_request_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return _bad_request("thinking.type: valor no admitido\npara este modelo")

    code, lines = await _run(handler, ["apagado"])
    assert code == 1
    assert lines[1].startswith("[FALLA] apagado: LlmBadRequestError; si el modelo no se puede")
    assert lines[2] == (
        "    POST /v1/messages -> 400 | invalid_request_error: thinking.type: valor no "
        "admitido para este modelo | request-id req_humo_1"
    )


async def test_provider_fields_are_bounded_and_printable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(
            400,
            json={"error": {"type": "invalid\x1b[31m_error", "message": "x" * 1000}},
            headers={"request-id": "r" * 500},
        )

    _, lines = await _run(handler, ["apagado"])
    detail = lines[2]
    assert "\x1b" not in detail
    assert "x" * 300 in detail and "x" * 301 not in detail
    assert "r" * 60 in detail and "r" * 61 not in detail


# --- lo que nunca sale --------------------------------------------------------------


async def test_the_key_the_model_text_the_reasoning_and_the_signatures_never_print() -> None:
    handler, _ = _api()
    _, lines = await _run(handler, list(ca.CASES))
    output = "\n".join(lines)
    for secret in (_API_KEY, _REPLY_TEXT, _THINKING_TEXT, _SIGNATURE):
        assert secret not in output


def test_the_key_comes_from_the_environment_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY_CLAUDE=desde-el-archivo\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY_CLAUDE", "desde-el-entorno")
    assert ca.load_api_key("LLM_API_KEY_CLAUDE", env_file).get_secret_value() == "desde-el-entorno"
    monkeypatch.delenv("LLM_API_KEY_CLAUDE")
    assert ca.load_api_key("LLM_API_KEY_CLAUDE", env_file).get_secret_value() == "desde-el-archivo"


def test_a_missing_key_names_the_variable_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LLM_API_KEY_OTRA", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        ca.load_api_key("LLM_API_KEY_OTRA", tmp_path / ".env")
    assert str(excinfo.value) == "falta LLM_API_KEY_OTRA: ponela en el entorno o en .env"
