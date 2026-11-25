"""Resumen de una corrida: marcas por tag y por caso, y peor intento de cada caso.

Todo corre con ``FakeLlmClient``: nunca se llama a un proveedor real.
"""

from __future__ import annotations

from pathlib import Path

from app.llm.clients.fakes import FakeLlmClient
from app.llm.errors import LlmProviderRejectedError
from app.llm.schemas import CompletionResult
from evals.harness import REJECTION_FLAG, CaseResult, run_suite
from evals.report import format_comparison, format_summary, load_report, summarize, write_report
from evals.suites.false_confirmation import FalseConfirmationSuite

_SUITE = FalseConfirmationSuite()
_OK = "Desde acá no puedo agendar."
_LIE = "Listo, agendado."


def _result(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        tool_calls=[],
        finish_reason="stop",
        prompt_tokens=100,
        completion_tokens=20,
        model_name="org/model",
        latency_ms=100.0,
    )


async def _run(*answers: str | Exception, repeat: int = 1) -> list[CaseResult]:
    cases = _SUITE.load()[: len(answers) // repeat]
    fake = FakeLlmClient(models=frozenset({"conversacional"}))
    for answer in answers:
        if isinstance(answer, Exception):
            fake.queue_error(answer)
        else:
            fake.queue_result(_result(answer))
    return await run_suite(fake, _SUITE, cases, repeat=repeat, concurrency=1)


async def test_summary_counts_cases_by_their_worst_attempt() -> None:
    """Corrido varias veces, un caso vale lo que su peor intento."""
    results = await _run(_OK, _LIE, _OK, LlmProviderRejectedError("status 404"), repeat=2)

    summary = summarize(results)
    assert summary["buckets"] == {"pass": 2, "fail": 1, "review": 0, "error": 1, "truncated": 0}
    assert summary["worst_by_case"] == {
        "pass": 0,
        "fail": 1,
        "review": 0,
        "error": 1,
        "truncated": 0,
    }
    shown = format_summary({"suite": _SUITE.name, "repeat": 2}, summary)
    assert "Peor intento de cada caso: fallados 1, errores de llamada 1" in shown
    assert f"Casos con cada marca en algún intento: {{'{REJECTION_FLAG}': 1}}" in shown
    # Con una sola corrida por caso no agrega nada: no se muestra.
    assert "Peor intento" not in format_summary({"suite": _SUITE.name}, summary)


async def test_flags_are_counted_by_tag_and_by_case() -> None:
    """Una marca en un intento no se pierde porque otro intento del caso salió peor o mejor."""
    rejected = LlmProviderRejectedError("status 404")
    results = await _run(rejected, rejected, _OK, _OK, repeat=2)
    second_tags = _SUITE.load()[1].tags

    summary = summarize(results)
    assert summary["flags"] == {REJECTION_FLAG: 2}
    assert summary["cases_with_flag"] == {REJECTION_FLAG: 1}
    assert summary["flags_by_tag"][results[0].tags[0]][REJECTION_FLAG] == 2
    # El segundo caso no tuvo marcas: sus tags propios no aparecen.
    only_second = set(second_tags) - set(results[0].tags)
    assert not only_second & summary["flags_by_tag"].keys()


async def test_comparison_shows_the_flags_side_by_side(tmp_path: Path) -> None:
    left = await _run(LlmProviderRejectedError("status 404"), _OK)
    right = await _run(_OK, _OK)
    for name, results in (("a", left), ("b", right)):
        write_report(
            tmp_path / f"{name}.json", meta={"suite": "x", "provider": name}, results=results
        )

    comparison = format_comparison(
        load_report(tmp_path / "a.json"), load_report(tmp_path / "b.json")
    )

    row = next(line for line in comparison.splitlines() if REJECTION_FLAG in line)
    assert row.split()[-2:] == ["1", "0"]
