"""Tests de la prueba de humo de Anthropic (``scripts/check_anthropic.py``).

Sin red: cada caso corre contra un ``httpx.MockTransport`` que contesta con la forma
de la Messages API y datos sintéticos. Cubren el modo lista (no hace requests), el
corte por ``health``, el resultado de cada caso, el detalle de un error del proveedor,
los casos que no concluyen, la credencial (nunca se imprime) y que el script no lee
``Settings``.

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

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_anthropic.py"
_API_KEY = "sk-test-anthropic-smoke-0123456789"
_MODEL_ID = "modelo-humo-test"


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


_THINKING_BLOCK = {"type": "thinking", "thinking": "razonamiento de prueba", "signature": "sig"}
_TEXT_BLOCK = {"type": "text", "text": "listo"}
_TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "smoke__echo", "input": {"texto": "hola"}}


def _sse(*events: dict[str, Any]) -> bytes:
    lines = []
    for event in events:
        lines += [f"event: {event['type']}", f"data: {json.dumps(event)}", ""]
    return ("\n".join(lines) + "\n").encode()


_STREAM = _sse(
    {"type": "message_start", "message": {"usage": {"input_tokens": 12, "output_tokens": 0}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "listo"}},
    {"type": "content_block_stop", "index": 0},
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
) -> tuple[Handler, list[dict[str, Any]]]:
    """Un doble de la API: ``/models``, ``/messages`` con tools, sin tools y por SSE."""
    payloads: list[dict[str, Any]] = []
    first_tool_reply = tool_use_reply or _message(
        [_THINKING_BLOCK, _TOOL_USE], stop_reason="tool_use"
    )

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
        with_system = sum(1 for p in payloads if "system" in p)
        cached = 900 if with_system % 2 == 0 else 0
        return httpx.Response(
            200,
            json=_message(
                [_THINKING_BLOCK, _TEXT_BLOCK],
                cache_read_input_tokens=cached,
                cache_creation_input_tokens=900 - cached,
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


def _bad_request(message: str) -> httpx.Response:
    return httpx.Response(
        400,
        json={"type": "error", "error": {"type": "invalid_request_error", "message": message}},
        headers={"request-id": "req_humo_1"},
    )


async def _run(handler: Handler, names: list[str]) -> tuple[int, list[str]]:
    lines: list[str] = []
    code = await ca.run_checks(_factory(handler), names, out=lines.append)
    return code, lines


# --- modo lista y health ---------------------------------------------------


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
    assert post.headers["anthropic-beta"] == "thinking-binding-controls-2026-08-01"
    assert json.loads(post.content)["max_tokens"] == 1024
    assert "    bloques: thinking(firmado), text" in lines


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


async def test_the_tool_round_replays_the_blocks_of_the_first_call() -> None:
    handler, payloads = _api()
    _, lines = await _run(handler, ["vuelta_con_tools"])
    assert lines[1] == "[OK] vuelta_con_tools: 1 tool, 2 llamadas, finish_reason stop"
    replayed = payloads[1]["messages"][1]["content"]
    assert replayed[0] == _THINKING_BLOCK


async def test_a_tool_round_without_a_tool_call_does_not_conclude() -> None:
    handler, _ = _api(tool_use_reply=_message([_TEXT_BLOCK]))
    code, lines = await _run(handler, ["vuelta_con_tools", "confirmacion_forzada"])
    assert code == 0
    assert lines[1] == "[NO CONCLUYE] vuelta_con_tools: el modelo no llamó a la tool"
    assert lines[2].startswith("[NO CONCLUYE] confirmacion_forzada: el modelo no llamó")


async def test_the_forced_confirmation_goes_with_tool_choice_none_and_thinking_off() -> None:
    handler, payloads = _api()
    _, lines = await _run(handler, ["confirmacion_forzada"])
    assert lines[1].startswith("[OK] confirmacion_forzada: confirmación forzada aceptada")
    forced = payloads[1]
    assert forced["tool_choice"] == {"type": "none"}
    assert forced["thinking"] == {"type": "disabled"}
    assert forced["output_config"] == {"effort": "low"}


async def test_the_effort_probe_reports_that_the_rule_stays_when_the_api_rejects_it() -> None:
    handler, payloads = _api(after_tool=lambda _: _bad_request("effort no coincide"))
    code, lines = await _run(handler, ["cambio_de_esfuerzo"])
    assert code == 0
    assert lines[1] == (
        "[OK] cambio_de_esfuerzo: la API rechaza el cambio de esfuerzo "
        "(LlmBadRequestError): la regla queda"
    )
    assert "effort no coincide" in lines[2]
    assert payloads[0]["output_config"] == {"effort": "medium"}
    assert payloads[1]["output_config"] == {"effort": "low"}


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


async def test_a_cache_below_the_minimum_does_not_conclude() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/models"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json=_message([_TEXT_BLOCK]))

    code, lines = await _run(handler, ["cache"])
    assert code == 0
    assert lines[1].startswith("[NO CONCLUYE] cache: sin caché")


# --- credencial --------------------------------------------------------------


async def test_the_api_key_and_the_model_text_are_never_printed() -> None:
    handler, _ = _api()
    _, lines = await _run(handler, list(ca.CASES))
    output = "\n".join(lines)
    assert _API_KEY not in output
    assert "razonamiento de prueba" not in output
    assert "sig" not in output.replace("signature", "")


def test_the_key_comes_from_the_environment_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY_CLAUDE=desde-el-archivo\n", encoding="utf-8")
    monkeypatch.setenv("LLM_API_KEY_CLAUDE", "desde-el-entorno")
    assert ca.load_api_key("claude", env_file).get_secret_value() == "desde-el-entorno"
    monkeypatch.delenv("LLM_API_KEY_CLAUDE")
    assert ca.load_api_key("claude", env_file).get_secret_value() == "desde-el-archivo"


def test_a_missing_key_names_the_variable_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("LLM_API_KEY_OTRA", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        ca.load_api_key("otra", tmp_path / ".env")
    assert str(excinfo.value) == "falta LLM_API_KEY_OTRA: ponela en el entorno o en .env"
