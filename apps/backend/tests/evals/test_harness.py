"""Plomería del runner con ``FakeLlmClient``: nunca se llama a un proveedor real."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.config import Settings
from app.enums import Mode
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.resilient import ResilientClient
from app.llm.config import load_llm_config
from app.llm.errors import LlmRateLimitError, ModelNotServedError
from app.llm.schemas import CompletionResult, ToolCall
from evals import client as client_mod
from evals.__main__ import build_parser, main
from evals.cases import FalseConfirmationCase
from evals.client import EvalConfigError, build_eval_client
from evals.harness import CallRecord, is_truncated, run_suite
from evals.report import format_comparison, format_summary, load_report, summarize, write_report
from evals.suites import SUITES
from evals.suites.false_confirmation import FalseConfirmationSuite
from evals.suites.tool_calling import ToolCallingSuite

_PROVIDERS = [
    {
        "name": "principal",
        "base_url": "https://api.principal.test/v1",
        "models": {"conversacional": "org/chat", "agente": "org/agent"},
    },
    {
        "name": "respaldo",
        "base_url": "https://api.respaldo.test/v1",
        "models": {"conversacional": "otro/chat", "agente": "otro/agent"},
    },
]


def _settings(llm_backend: str) -> Settings:
    kwargs: dict[str, object] = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test:test@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        "JWT_SECRET": "test-secret",
        "environment": "development",
        "LLM_BACKEND": llm_backend,
    }
    if llm_backend == "api":
        kwargs["LLM_PROVIDERS"] = json.dumps(_PROVIDERS)
        kwargs["llm_api_keys"] = {"principal": "sk-test-0123", "respaldo": "sk-test-4567"}
    return Settings(**kwargs)  # type: ignore[arg-type]


def _result(
    *,
    text: str = "",
    tool_calls: list[ToolCall] | None = None,
    finish_reason: str = "stop",
    reasoning: str | None = None,
) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        finish_reason=finish_reason,
        prompt_tokens=100,
        completion_tokens=20,
        model_name="org/agent",
        latency_ms=250.0,
        reasoning=reasoning,
    )


def _confirmation_cases(count: int) -> list[FalseConfirmationCase]:
    return FalseConfirmationSuite().load()[:count]


async def test_run_suite_sends_the_production_turn_and_grades_it() -> None:
    suite = ToolCallingSuite()
    case = suite.load()[0]
    fake = FakeLlmClient(models=frozenset({"agente"}))
    call = ToolCall(
        id="c1",
        name="calendar.create_event",
        arguments={
            "title": "Dentista",
            "start_at": "2026-11-26T15:00:00-03:00",
            "duration_min": 60,
        },
    )
    fake.queue_result(_result(tool_calls=[call], finish_reason="tool_calls", reasoning="pienso"))

    results = await run_suite(fake, suite, [case], concurrency=1)

    sent: dict[str, Any] = fake.complete_calls[0]
    assert sent["model"] == "agente"
    assert sent["thinking"] is True
    assert sent["messages"][-1].content == case.text
    assert "calendar.create_event" in {spec.name for spec in sent["tools"]}
    assert results[0].verdict.bucket == "pass"
    assert results[0].record.has_reasoning is True
    assert results[0].record.model_name == "org/agent"


async def test_provider_errors_and_truncation_get_their_own_buckets() -> None:
    """Una caída o un tope de tokens no cuentan como respuesta mala."""
    cases = _confirmation_cases(4)
    fake = FakeLlmClient(models=frozenset({"conversacional"}))
    fake.queue_error(LlmRateLimitError("rate limited"))
    fake.queue_result(_result(text="", finish_reason="length"))
    fake.queue_result(_result(text="La IA está pausada", finish_reason="degraded"))
    fake.queue_result(_result(text="Desde acá no puedo agendar."))

    results = await run_suite(fake, FalseConfirmationSuite(), cases, concurrency=1)

    assert [r.verdict.bucket for r in results] == ["error", "truncated", "error", "pass"]
    assert is_truncated(CallRecord(status="ok", text="", finish_reason="length"))
    # El tool loop del chat no ejecuta las tool calls de una respuesta cortada.
    cut_call = [{"name": "reminder.set", "arguments": {}}]
    assert is_truncated(CallRecord(status="ok", tool_calls=cut_call, finish_reason="length"))
    assert not is_truncated(CallRecord(status="ok", text="Texto cortad", finish_reason="length"))
    assert results[0].verdict.details == ["LlmRateLimitError"]
    summary = summarize(results)
    assert summary["buckets"] == {"pass": 1, "fail": 0, "review": 0, "error": 2, "truncated": 1}
    assert summary["pass_rate"] == 1.0
    assert summary["errors"] == {"LlmRateLimitError": 1, "degraded": 1}


async def test_model_not_served_is_a_config_error_and_propagates() -> None:
    fake = FakeLlmClient(models=frozenset({"conversacional"}))
    fake.queue_error(ModelNotServedError("conversacional"))
    with pytest.raises(ModelNotServedError):
        await run_suite(fake, FalseConfirmationSuite(), _confirmation_cases(1))


async def test_repeat_runs_each_case_several_times() -> None:
    cases = _confirmation_cases(2)
    fake = FakeLlmClient(models=frozenset({"conversacional"}))
    for _ in range(6):
        fake.queue_result(_result(text="Listo, agendado."))
    seen: list[str] = []

    results = await run_suite(
        fake,
        FalseConfirmationSuite(),
        cases,
        repeat=3,
        concurrency=2,
        on_result=lambda result: seen.append(result.case_id),
    )

    assert len(results) == 6
    assert sorted({r.attempt for r in results}) == [1, 2, 3]
    assert len(seen) == 6
    assert summarize(results)["pass_rate"] == 0.0


async def test_report_roundtrip_and_comparison(tmp_path: Path) -> None:
    cases = _confirmation_cases(2)
    suite = FalseConfirmationSuite()

    async def _run(*answers: str) -> list[Any]:
        fake = FakeLlmClient(models=frozenset({"conversacional"}))
        for answer in answers:
            fake.queue_result(_result(text=answer))
        return await run_suite(fake, suite, cases, concurrency=1)

    left = await _run("Desde acá no puedo agendar.", "Listo, agendado.")
    right = await _run("Desde acá no puedo agendar.", "No tengo forma de recordártelo.")
    for name, results in (("a", left), ("b", right)):
        meta = {"suite": suite.name, "provider": name}
        write_report(tmp_path / f"{name}.json", meta=meta, results=results)

    report_a = load_report(tmp_path / "a.json")
    report_b = load_report(tmp_path / "b.json")
    assert report_a["summary"]["pass_rate"] == 0.5
    assert report_a["results"][1]["record"]["text"] == "Listo, agendado."
    assert "50.0%" in format_summary(report_a["meta"], report_a["summary"])

    comparison = format_comparison(report_a, report_b)
    assert "Casos con distinto resultado: 1" in comparison
    assert f"{cases[1].id}: a=fail b=pass" in comparison


def test_eval_client_refuses_the_fake_backend() -> None:
    with pytest.raises(EvalConfigError, match="LLM_BACKEND"):
        build_eval_client(_settings("fake"), load_llm_config(), "principal")


def test_eval_client_refuses_an_unknown_provider() -> None:
    with pytest.raises(EvalConfigError, match="respaldo"):
        build_eval_client(_settings("api"), load_llm_config(), "otro")


async def test_eval_client_keeps_only_the_chosen_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El respaldo no puede contestar por el principal: a la factory le llega uno solo."""
    seen: dict[str, Any] = {}
    real_factory = client_mod.build_llm_client

    def _spy(settings: Settings, config: Any, *, degrade: bool = True) -> Any:
        seen["providers"] = [provider.name for provider in settings.llm_providers]
        seen["degrade"] = degrade
        return real_factory(settings, config, degrade=degrade)

    monkeypatch.setattr(client_mod, "build_llm_client", _spy)
    settings = _settings("api")

    client = build_eval_client(settings, load_llm_config(), "respaldo")
    try:
        assert isinstance(client, ResilientClient)
        assert seen == {"providers": ["respaldo"], "degrade": False}
        # El Settings original no se toca: sigue con los dos proveedores.
        assert [p.name for p in settings.llm_providers] == ["principal", "respaldo"]
    finally:
        await client.aclose()


def test_cli_lists_suites_without_touching_the_network(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["list"]) == 0
    out = capsys.readouterr().out
    for name in SUITES:
        assert name in out


def test_cli_run_requires_suite_and_provider() -> None:
    with pytest.raises(SystemExit):
        build_parser().parse_args(["run", "--suite", "tool_calling"])
    args = build_parser().parse_args(["run", "--suite", "tool_calling", "--provider", "p"])
    assert (args.repeat, args.concurrency, args.limit) == (1, 4, None)


def test_false_confirmation_cases_default_to_the_shared_now() -> None:
    case = FalseConfirmationCase(id="x", text="t", mode=Mode.VIDA)
    assert case.now.isoformat() == "2026-11-25T10:30:00-03:00"
