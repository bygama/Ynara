"""Control de las etiquetas del set de evaluación (``evals/data``).

Un caso mal etiquetado mide mal sin avisar. Estos tests atrapan lo mecánico: días de
la semana que no coinciden con la fecha, tools o argumentos que no existen, y casos
de un modo que no corresponde a la suite.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from app.llm.config import load_llm_config
from evals.cases import WEEKDAYS_ES, Matcher
from evals.harness import build_turn
from evals.suites.false_confirmation import FalseConfirmationSuite
from evals.suites.tool_calling import ToolCallingSuite

_TOOL_CASES = ToolCallingSuite().load()
_CONFIRMATION_CASES = FalseConfirmationSuite().load()


def _matchers() -> list[tuple[str, str, Matcher]]:
    return [
        (case.id, case.tz, matcher)
        for case in _TOOL_CASES
        for expectation in case.expect.any_of
        for matcher in expectation.args.values()
    ]


def test_suites_have_the_reference_sizes() -> None:
    assert len(_TOOL_CASES) >= 100
    assert len(_CONFIRMATION_CASES) == 30


def test_weekday_labels_match_the_expected_dates() -> None:
    """El día nombrado en el caso es el de CADA fecha aceptada, en el huso del caso."""
    checked = 0
    for case_id, tz, matcher in _matchers():
        if matcher.weekday is None:
            continue
        local_dates = [moment.astimezone(ZoneInfo(tz)).date() for moment in matcher.dt or []]
        local_dates += matcher.date or []
        assert local_dates, f"{case_id}: weekday sin fecha que controlar"
        for local_date in local_dates:
            assert WEEKDAYS_ES[local_date.weekday()] == matcher.weekday, (
                f"{case_id}: {local_date} no es {matcher.weekday}"
            )
        checked += 1
    assert checked >= 30


def test_expected_dates_are_not_before_the_case_now() -> None:
    """Nadie pide agendar en el pasado: cada pedido acepta al menos una fecha futura."""
    for case in _TOOL_CASES:
        for expectation in case.expect.any_of:
            if not expectation.tool.endswith((".create_event", ".set", ".create_task")):
                continue
            for name, matcher in expectation.args.items():
                later = [moment for moment in matcher.dt or [] if moment >= case.now]
                if matcher.dt is not None:
                    assert later, f"{case.id}: {name} no acepta ninguna fecha futura"


def test_expected_tools_and_arguments_exist_in_the_mode() -> None:
    """Cada tool esperada existe en el modo del caso, con esos nombres de argumento."""
    config = load_llm_config()
    for case in _TOOL_CASES:
        turn = build_turn(case.mode, case.text, now=case.now, tz=case.tz, config=config)
        specs = {spec.name: spec for spec in turn.tools or []}
        for expectation in case.expect.any_of:
            assert expectation.tool in specs, f"{case.id}: {expectation.tool} no está en el modo"
            properties = specs[expectation.tool].parameters.get("properties", {})
            for name in expectation.args:
                assert name in properties, f"{case.id}: {expectation.tool} no tiene {name}"


def test_false_confirmation_cases_use_modes_without_tools() -> None:
    config = load_llm_config()
    modes = {case.mode for case in _CONFIRMATION_CASES}
    assert len(modes) == 3
    for mode in modes:
        assert config.modes[mode.value].tools_enabled == []
