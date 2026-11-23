"""Modelos de los casos de evaluación y su carga desde JSONL.

Cada línea de ``data/<suite>.jsonl`` es un caso. Los modelos son estrictos
(``extra='forbid'``): un campo mal escrito en un caso falla al cargar, en vez de pasar
en silencio como un caso que nadie corrige.
"""

from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.enums import Mode
from app.llm.prompts.datetime_context import APP_TIMEZONE

DATA_DIR = Path(__file__).resolve().parent / "data"

# «Ahora» de los casos que no traen el suyo: miércoles 25 de noviembre de 2026, 10:30,
# hora de Buenos Aires. Fijo, para que las fechas relativas tengan una sola respuesta.
DEFAULT_NOW = _dt.datetime.fromisoformat("2026-11-25T10:30:00-03:00")

WEEKDAYS_ES = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Matcher(_Strict):
    """Qué tiene que cumplir un argumento de la tool call.

    - ``dt``: instantes aceptados (con offset); el argumento tiene que ser uno de ellos.
    - ``date``: fechas aceptadas, a cualquier hora (pedidos sin hora).
    - ``dt_min`` / ``dt_max``: rango aceptado (ventanas de listar).
    - ``weekday``: nombre del día de la semana de ``dt`` / ``date``. No corrige al
      modelo: es el control de las etiquetas (lo verifica ``tests/evals``).
    - ``eq``: igualdad exacta. ``one_of``: uno de varios valores exactos.
    - ``contains_any`` / ``contains_all``: palabras clave, sin distinguir mayúsculas ni
      tildes.
    - ``rrule_byday``: los días (``MO``, ``TU``…) del ``BYDAY`` de una regla RRULE.
    - ``absent``: el argumento no tiene que venir. ``optional``: puede faltar.
    """

    dt: list[_dt.datetime] | None = None
    date: list[_dt.date] | None = None
    dt_min: _dt.datetime | None = None
    dt_max: _dt.datetime | None = None
    weekday: str | None = None
    eq: Any = None
    one_of: list[Any] | None = None
    contains_any: list[str] | None = None
    contains_all: list[str] | None = None
    rrule_byday: list[str] | None = None
    absent: bool = False
    optional: bool = False

    @model_validator(mode="after")
    def _check(self) -> Matcher:
        if self.weekday is not None and self.weekday not in WEEKDAYS_ES:
            raise ValueError(f"weekday desconocido: {self.weekday}")
        for value in [*(self.dt or []), self.dt_min, self.dt_max]:
            if value is not None and value.tzinfo is None:
                raise ValueError("las fechas esperadas llevan offset")
        return self


class ToolExpectation(_Strict):
    """Una tool call aceptada: el nombre de la tool y lo que cumplen sus argumentos."""

    tool: str
    args: dict[str, Matcher] = Field(default_factory=dict)


class Expect(_Strict):
    """Comportamiento esperado de un caso de tool-calling.

    - ``call``: tiene que llamar a una de las tools de ``any_of``.
    - ``clarify``: le falta un dato; tiene que preguntar sin llamar ninguna tool.
    - ``call_or_clarify``: el prompt del modo no define cuál; valen las dos.
    - ``none``: no corresponde ninguna tool (charla, o un pedido que no se puede).
    """

    kind: Literal["call", "clarify", "call_or_clarify", "none"]
    any_of: list[ToolExpectation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> Expect:
        needs_tools = self.kind in ("call", "call_or_clarify")
        if needs_tools != bool(self.any_of):
            raise ValueError(f"kind={self.kind} no coincide con any_of")
        return self


class ToolCallingCase(_Strict):
    """Un pedido en rioplatense y la tool call que le corresponde."""

    id: str
    text: str
    mode: Mode = Mode.PRODUCTIVIDAD
    now: _dt.datetime = DEFAULT_NOW
    tz: str = APP_TIMEZONE
    tags: list[str] = Field(default_factory=list)
    expect: Expect


class FalseConfirmationCase(_Strict):
    """Un pedido de acción («agendame…») en un modo que no tiene tools."""

    id: str
    text: str
    mode: Mode
    now: _dt.datetime = DEFAULT_NOW
    tz: str = APP_TIMEZONE
    tags: list[str] = Field(default_factory=list)


def load_cases[CaseT: BaseModel](path: Path, model: type[CaseT]) -> list[CaseT]:
    """Lee un JSONL de casos; falla si una línea no valida o si se repite un ``id``."""
    cases: list[CaseT] = []
    seen: set[str] = set()
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            case = model.model_validate(json.loads(line))
        except ValueError as exc:
            raise ValueError(f"{path.name}:{number}: caso inválido: {exc}") from exc
        case_id = getattr(case, "id", "")
        if case_id in seen:
            raise ValueError(f"{path.name}:{number}: id repetido: {case_id}")
        seen.add(case_id)
        cases.append(case)
    return cases
