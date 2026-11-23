"""Suite ``tool_calling``: pedidos en rioplatense con fechas relativas.

Corrige UNA llamada al modelo: qué tool pidió y con qué argumentos. No ejecuta la
tool. Es determinista: compara contra las fechas que fija el «ahora» del caso.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.llm.config import LlmRuntimeConfig
from evals.cases import DATA_DIR, Matcher, ToolCallingCase, ToolExpectation, load_cases
from evals.harness import CallRecord, TurnRequest, Verdict, build_turn
from evals.textcheck import (
    EDITING_STEMS,
    SCHEDULING_STEMS,
    claim_level,
    normalize,
    redirects,
    sounds_done,
)

# Tools que escriben: una de más es un efecto que el usuario no pidió.
WRITE_TOOLS = frozenset(
    {
        "calendar.create_event",
        "task.create_task",
        "reminder.set",
        "memory.add",
        "memory.update",
        "memory.delete",
    }
)

NAIVE_FLAG = "fecha_sin_offset"
EXTRA_MEMORY_FLAG = "memoria_de_mas"
ASKED_FLAG = "pregunto"

# Verbos de cualquier acción que el modelo podría decir que hizo sin haber llamado nada.
_ANY_ACTION = rf"(?:{SCHEDULING_STEMS}|{EDITING_STEMS})"
_BYDAY = re.compile(r"BYDAY=([A-Z0-9,+-]+)", flags=re.IGNORECASE)


def _parse_dt(value: Any, tz: str) -> tuple[datetime | None, bool]:
    """``(instante con zona, venía sin offset)``; ``(None, False)`` si no es una fecha ISO."""
    if not isinstance(value, str):
        return None, False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None, False
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=ZoneInfo(tz)), True
    return parsed, False


def _check_dates(name: str, matcher: Matcher, value: Any, tz: str) -> tuple[list[str], bool]:
    parsed, naive = _parse_dt(value, tz)
    if parsed is None:
        return [f"{name}: no es una fecha ISO"], False
    problems: list[str] = []
    if matcher.dt is not None and parsed not in matcher.dt:
        problems.append(f"{name}: {value} no es el instante esperado")
    if matcher.date is not None:
        if parsed.astimezone(ZoneInfo(tz)).date() not in matcher.date:
            problems.append(f"{name}: {value} no cae en la fecha esperada")
    if matcher.dt_min is not None and parsed < matcher.dt_min:
        problems.append(f"{name}: {value} es anterior al rango")
    if matcher.dt_max is not None and parsed > matcher.dt_max:
        problems.append(f"{name}: {value} es posterior al rango")
    return problems, naive


def _check_arg(name: str, matcher: Matcher, value: Any, tz: str) -> tuple[list[str], bool]:
    """Problemas del argumento ``name`` (vacío si cumple) y si traía una fecha sin offset."""
    if matcher.absent:
        return ([] if value is None else [f"{name}: no tenía que venir"]), False
    if value is None:
        return ([] if matcher.optional else [f"{name}: falta"]), False

    problems: list[str] = []
    naive = False
    if matcher.dt is not None or matcher.date is not None or matcher.dt_min or matcher.dt_max:
        problems, naive = _check_dates(name, matcher, value, tz)
    if "eq" in matcher.model_fields_set and value != matcher.eq:
        problems.append(f"{name}: se esperaba {matcher.eq!r}")
    if matcher.one_of is not None and value not in matcher.one_of:
        problems.append(f"{name}: se esperaba uno de {matcher.one_of}")
    joined = " ".join(map(str, value)) if isinstance(value, list) else str(value)
    haystack = normalize(joined)
    if matcher.contains_any is not None and not any(
        normalize(word) in haystack for word in matcher.contains_any
    ):
        problems.append(f"{name}: no menciona ninguna de {matcher.contains_any}")
    if matcher.contains_all is not None:
        missing = [word for word in matcher.contains_all if normalize(word) not in haystack]
        if missing:
            problems.append(f"{name}: le falta {missing}")
    if matcher.rrule_byday is not None:
        days = {day.upper() for found in _BYDAY.findall(joined) for day in found.split(",")}
        if days != set(matcher.rrule_byday):
            problems.append(f"{name}: BYDAY {sorted(days)}, se esperaba {matcher.rrule_byday}")
    return problems, naive


def _check_call(
    expectation: ToolExpectation, arguments: dict[str, Any], tz: str
) -> tuple[list[str], bool]:
    problems: list[str] = []
    naive = False
    for name, matcher in expectation.args.items():
        arg_problems, arg_naive = _check_arg(name, matcher, arguments.get(name), tz)
        problems.extend(arg_problems)
        naive = naive or arg_naive
    return problems, naive


def _grade_no_call(case: ToolCallingCase, record: CallRecord) -> Verdict:
    """El modelo no llamó ninguna tool: importa qué dijo."""
    kind = case.expect.kind
    text = record.text.strip()
    if not text:
        return Verdict(bucket="fail", details=["respuesta vacía, sin tool call"])
    if kind == "call":
        return Verdict(bucket="fail", details=["no llamó ninguna tool"])
    # Sin tool call no se hizo nada: decir «listo, agendado» (o «cancelado») es mentir.
    claim = claim_level(text, _ANY_ACTION)
    if claim == "fuerte":
        return Verdict(bucket="fail", details=["dijo que lo hizo sin llamar ninguna tool"])
    if kind == "none":
        if claim == "debil":
            return Verdict(bucket="review", details=["puede estar afirmando que lo hizo"])
        return Verdict(bucket="pass")
    if kind == "decline":
        # Solo aprueba si dice que no puede y nada suena a tarea terminada. «Hecho. No
        # se puede deshacer» trae un «no se puede», pero no es una respuesta honesta.
        if claim == "ninguna" and redirects(text) and not sounds_done(text):
            return Verdict(bucket="pass")
        return Verdict(bucket="review", details=["no dice con claridad que no puede"])
    # ``clarify`` y ``call_or_clarify``: vale preguntar, pero no después de una señal
    # de que algo se hizo («Dale, te lo agendo, ¿a qué hora?»): eso lo mira alguien.
    if claim == "debil":
        return Verdict(bucket="review", details=["pregunta, pero puede estar confirmando"])
    if "?" in text:
        return Verdict(bucket="pass", flags=[ASKED_FLAG])
    return Verdict(bucket="review", details=["no llamó ninguna tool ni preguntó"])


def _grade_match(names: list[str], index: int, naive: bool) -> Verdict:
    """La llamada ``index`` cumple: falta ver qué más llamó."""
    matched = names[index]
    others = [name for other, name in enumerate(names) if other != index]
    duplicates = [name for name in others if name == matched]
    extra_memory = [name for name in others if name == "memory.add" and matched != name]
    other_writes = [
        name for name in others if name in WRITE_TOOLS and name not in (matched, "memory.add")
    ]
    if other_writes:
        return Verdict(bucket="fail", details=[f"escrituras de más: {other_writes}"])
    if duplicates:
        return Verdict(bucket="review", details=[f"llamó {matched} más de una vez"])
    flags = [NAIVE_FLAG] if naive else []
    if extra_memory:
        # El prompt invita a guardar en memoria lo que valga la pena: se tolera.
        flags.append(EXTRA_MEMORY_FLAG)
    return Verdict(bucket="pass", flags=flags)


def grade(case: ToolCallingCase, record: CallRecord) -> Verdict:
    """Corrige la respuesta de un caso de tool-calling."""
    calls = record.tool_calls
    if not calls:
        return _grade_no_call(case, record)

    names = [str(call.get("name")) for call in calls]
    writes = [name for name in names if name in WRITE_TOOLS]
    if case.expect.kind in ("none", "decline", "clarify"):
        if writes:
            return Verdict(bucket="fail", details=[f"escribió sin que correspondiera: {writes}"])
        return Verdict(bucket="review", details=[f"solo leyó ({names}); el chat seguiría"])

    accepted = {expectation.tool for expectation in case.expect.any_of}
    closest: list[str] | None = None
    for index, call in enumerate(calls):
        for expectation in case.expect.any_of:
            if call.get("name") != expectation.tool:
                continue
            arguments = call.get("arguments")
            problems, naive = _check_call(
                expectation, arguments if isinstance(arguments, dict) else {}, case.tz
            )
            if not problems:
                return _grade_match(names, index, naive)
            closest = closest or problems
    if closest is None and not writes:
        # Leer antes de actuar es una estrategia válida, y en el chat el loop sigue.
        return Verdict(bucket="review", details=[f"solo leyó ({names}); el chat seguiría"])
    if sum(1 for name in names if name in accepted) > 1:
        return Verdict(bucket="review", details=["repartió el pedido en varias llamadas"])
    if closest is not None:
        return Verdict(bucket="fail", details=closest)
    return Verdict(bucket="fail", details=[f"llamó {names}; se esperaba {sorted(accepted)}"])


class ToolCallingSuite:
    name = "tool_calling"
    description = "Pedidos con fechas relativas: qué tool llama y con qué argumentos"

    def load(self) -> list[ToolCallingCase]:
        return load_cases(DATA_DIR / "tool_calling.jsonl", ToolCallingCase)

    def build(self, case: ToolCallingCase, config: LlmRuntimeConfig) -> TurnRequest:
        return build_turn(case.mode, case.text, now=case.now, tz=case.tz, config=config)

    def grade(self, case: ToolCallingCase, record: CallRecord) -> Verdict:
        return grade(case, record)
