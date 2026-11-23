"""Corrector de la suite ``tool_calling``: una respuesta de ejemplo por cada resultado."""

from __future__ import annotations

from typing import Any

from evals.cases import ToolCallingCase
from evals.harness import CallRecord
from evals.suites.tool_calling import EXTRA_MEMORY_FLAG, NAIVE_FLAG, grade

_START = "2026-11-26T15:00:00-03:00"


def _case(expect: dict[str, Any]) -> ToolCallingCase:
    return ToolCallingCase.model_validate({"id": "t", "text": "pedido", "expect": expect})


def _event_case(**extra_args: Any) -> ToolCallingCase:
    args = {"start_at": {"dt": [_START]}, "title": {"contains_any": ["dentista"]}, **extra_args}
    return _case({"kind": "call", "any_of": [{"tool": "calendar.create_event", "args": args}]})


def _record(*calls: tuple[str, dict[str, Any]], text: str = "") -> CallRecord:
    return CallRecord(
        status="ok",
        text=text,
        tool_calls=[{"name": name, "arguments": arguments} for name, arguments in calls],
        finish_reason="tool_calls" if calls else "stop",
    )


def _event(start_at: str, **more: Any) -> tuple[str, dict[str, Any]]:
    return ("calendar.create_event", {"title": "Turno Dentista", "start_at": start_at, **more})


def test_exact_call_passes() -> None:
    verdict = grade(_event_case(), _record(_event(_START, duration_min=60)))
    assert verdict.bucket == "pass"
    assert verdict.flags == []


def test_same_instant_in_another_offset_passes() -> None:
    assert grade(_event_case(), _record(_event("2026-11-26T18:00:00+00:00"))).bucket == "pass"


def test_datetime_without_offset_passes_but_is_flagged() -> None:
    verdict = grade(_event_case(), _record(_event("2026-11-26T15:00:00")))
    assert verdict.bucket == "pass"
    assert verdict.flags == [NAIVE_FLAG]


def test_local_time_sent_as_utc_fails() -> None:
    """El error clásico: la hora local con sufijo Z corre el evento tres horas."""
    verdict = grade(_event_case(), _record(_event("2026-11-26T15:00:00Z")))
    assert verdict.bucket == "fail"
    assert "start_at" in verdict.details[0]


def test_wrong_day_fails() -> None:
    assert grade(_event_case(), _record(_event("2026-11-27T15:00:00-03:00"))).bucket == "fail"


def test_title_keywords_ignore_case_and_accents() -> None:
    case = _case(
        {
            "kind": "call",
            "any_of": [
                {"tool": "calendar.create_event", "args": {"title": {"contains_any": ["medico"]}}}
            ],
        }
    )
    call = ("calendar.create_event", {"title": "Turno MÉDICO", "start_at": _START})
    assert grade(case, _record(call)).bucket == "pass"


def test_stated_duration_is_checked() -> None:
    case = _event_case(duration_min={"eq": 90})
    assert grade(case, _record(_event(_START, duration_min=90))).bucket == "pass"
    assert grade(case, _record(_event(_START, duration_min=60))).bucket == "fail"


def test_absent_and_optional_arguments() -> None:
    case = _case(
        {
            "kind": "call",
            "any_of": [
                {
                    "tool": "task.create_task",
                    "args": {
                        "title": {"contains_any": ["yerba"]},
                        "scheduled_at": {"absent": True},
                        "duration_min": {"eq": 30, "optional": True},
                    },
                }
            ],
        }
    )
    assert grade(case, _record(("task.create_task", {"title": "Comprar yerba"}))).bucket == "pass"
    with_date = ("task.create_task", {"title": "Comprar yerba", "scheduled_at": _START})
    assert grade(case, _record(with_date)).bucket == "fail"


def test_date_only_matcher_accepts_any_time_that_day() -> None:
    case = _case(
        {
            "kind": "call_or_clarify",
            "any_of": [
                {
                    "tool": "calendar.create_event",
                    "args": {"start_at": {"date": ["2026-11-28"], "weekday": "sábado"}},
                }
            ],
        }
    )
    assert grade(case, _record(_event("2026-11-28T00:00:00-03:00"))).bucket == "pass"
    assert grade(case, _record(_event("2026-11-29T00:00:00-03:00"))).bucket == "fail"
    asked = grade(case, _record(text="¿A qué hora?"))
    assert asked.bucket == "pass"
    assert asked.flags == ["pregunto"]
    # Afirmar que se agendó sin haber llamado nada falla, aunque después pregunte.
    lied = grade(case, _record(text="Listo, agendé el cumple. ¿Querés que te avise antes?"))
    assert lied.bucket == "fail"


def test_window_matcher_checks_the_range() -> None:
    case = _case(
        {
            "kind": "call",
            "any_of": [
                {
                    "tool": "calendar.list_events",
                    "args": {
                        "from_dt": {
                            "dt_min": "2026-11-26T00:00:00-03:00",
                            "dt_max": "2026-11-26T09:00:00-03:00",
                        },
                        "to_dt": {
                            "dt_min": "2026-11-26T18:00:00-03:00",
                            "dt_max": "2026-11-27T00:00:00-03:00",
                        },
                    },
                }
            ],
        }
    )
    good = {"from_dt": "2026-11-26T00:00:00-03:00", "to_dt": "2026-11-26T23:59:59-03:00"}
    late = {"from_dt": "2026-11-26T00:00:00-03:00", "to_dt": "2026-11-30T00:00:00-03:00"}
    assert grade(case, _record(("calendar.list_events", good))).bucket == "pass"
    assert grade(case, _record(("calendar.list_events", late))).bucket == "fail"


def test_another_tool_fails_and_names_what_was_expected() -> None:
    verdict = grade(_event_case(), _record(("reminder.set", {"text": "x", "when": _START})))
    assert verdict.bucket == "fail"
    assert "calendar.create_event" in verdict.details[0]


def test_extra_write_call_fails() -> None:
    record = _record(_event(_START), ("reminder.set", {"text": "dentista", "when": _START}))
    assert grade(_event_case(), record).bucket == "fail"


def test_extra_memory_add_is_tolerated_and_flagged() -> None:
    """El prompt invita a guardar en memoria lo que valga la pena."""
    record = _record(_event(_START), ("memory.add", {"content": "Va al dentista"}))
    verdict = grade(_event_case(), record)
    assert verdict.bucket == "pass"
    assert verdict.flags == [EXTRA_MEMORY_FLAG]


def test_same_tool_twice_goes_to_review() -> None:
    assert grade(_event_case(), _record(_event(_START), _event(_START))).bucket == "review"
    # Repartir el pedido en varias llamadas que no cumplen solas también lo mira alguien.
    split = _record(_event("2026-11-27T15:00:00-03:00"), _event("2026-11-28T15:00:00-03:00"))
    assert grade(_event_case(), split).bucket == "review"


def test_reading_first_goes_to_review() -> None:
    """Leer antes de actuar es válido: en el chat el loop sigue después de la lectura."""
    window = {"from_dt": "2026-11-26T00:00:00-03:00", "to_dt": "2026-11-27T00:00:00-03:00"}
    only_read = _record(("calendar.list_events", window))
    assert grade(_event_case(), only_read).bucket == "review"
    assert grade(_case({"kind": "clarify"}), only_read).bucket == "review"


def test_one_of_and_rrule_byday_matchers() -> None:
    case = _event_case(
        time_zone={"one_of": ["America/Argentina/Buenos_Aires", "America/Buenos_Aires"]},
        recurrence={"contains_all": ["FREQ=WEEKLY"], "rrule_byday": ["MO", "WE", "FR"]},
    )

    def _with(time_zone: str, rule: str) -> Any:
        return _record(_event(_START, time_zone=time_zone, recurrence=[rule]))

    full = "RRULE:FREQ=WEEKLY;BYDAY=MO,WE,FR"
    assert grade(case, _with("America/Buenos_Aires", full)).bucket == "pass"
    assert grade(case, _with("America/Argentina/Buenos_Aires", full.lower())).bucket == "pass"
    assert grade(case, _with("UTC", full)).bucket == "fail"
    # «WE» está dentro de «WEEKLY» y «FR» dentro de «FREQ»: se lee el BYDAY, no el texto.
    only_monday = "RRULE:FREQ=WEEKLY;BYDAY=MO"
    assert grade(case, _with("America/Buenos_Aires", only_monday)).bucket == "fail"


def test_extra_read_call_is_tolerated() -> None:
    window = {"from_dt": "2026-11-26T00:00:00-03:00", "to_dt": "2026-11-27T00:00:00-03:00"}
    record = _record(("calendar.list_events", window), _event(_START))
    assert grade(_event_case(), record).bucket == "pass"


def test_no_call_when_one_was_expected_fails() -> None:
    assert grade(_event_case(), _record(text="Listo, agendado.")).bucket == "fail"
    assert grade(_event_case(), _record(text="")).bucket == "fail"


def test_clarify_expects_a_question_and_no_tool() -> None:
    case = _case({"kind": "clarify"})
    assert grade(case, _record(text="¿Qué día y a qué hora?")).bucket == "pass"
    assert grade(case, _record(text="Dale.")).bucket == "review"
    assert grade(case, _record(_event(_START))).bucket == "fail"
    assert grade(case, _record(text="Listo, agendado. ¿Algo más?")).bucket == "fail"


def test_claim_and_question_in_one_sentence_fails() -> None:
    case = _case({"kind": "clarify"})
    same_sentence = "Listo, agendé el cumple, ¿querés que te avise antes?"
    assert grade(case, _record(text=same_sentence)).bucket == "fail"
    # Ofrecer agendar no es afirmar que se agendó.
    either = _case(
        {"kind": "call_or_clarify", "any_of": [{"tool": "calendar.create_event", "args": {}}]}
    )
    assert grade(either, _record(text="¿Te lo agendo a las 7 o a las 19?")).bucket == "pass"


def test_decline_only_passes_when_it_says_it_cannot() -> None:
    """Sin tool que cubra el pedido, la respuesta buena es decir que no puede."""
    case = _case({"kind": "decline"})

    def _bucket(text: str) -> str:
        return grade(case, _record(text=text)).bucket

    assert _bucket("Todavía no puedo cancelar eventos.") == "pass"
    assert _bucket("No tengo una herramienta para mandar mails.") == "pass"
    assert _bucket("Listo, ya está.") == "review"
    assert _bucket("Te mando un borrador para que lo copies.") == "review"
    assert _bucket("Listo, cancelé la reunión de mañana.") == "fail"
    assert _bucket("Para que no te olvides, ya te cancelé la reunión.") == "fail"
    assert grade(case, _record(_event(_START))).bucket == "fail"
    assert grade(case, _record(("task.list_tasks", {}))).bucket == "review"


def test_none_fails_on_writes_and_sends_reads_to_review() -> None:
    case = _case({"kind": "none"})
    assert grade(case, _record(text="De nada.")).bucket == "pass"
    assert grade(case, _record(text="Todavía no puedo cancelar eventos.")).bucket == "pass"
    # Sin tool de cancelar, decir que canceló es justo la falla que importa.
    assert grade(case, _record(text="Listo, cancelé la reunión de mañana.")).bucket == "fail"
    assert grade(case, _record(text="Reunión cancelada.")).bucket == "review"
    assert grade(case, _record(_event(_START))).bucket == "fail"
    assert grade(case, _record(("task.list_tasks", {}))).bucket == "review"
