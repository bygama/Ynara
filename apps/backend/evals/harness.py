"""Armado del turno, llamada al modelo y corrida de una suite.

El turno se arma con las MISMAS piezas que ``app.llm.router.route``: el prompt del
modo, el preámbulo de fecha, las specs de las tools del modo, el alias y la intención
de razonamiento. La diferencia es deliberada: no hay base de datos (el bloque de
memoria va vacío y ninguna tool se ejecuta) y el «ahora» lo fija el caso.
``tests/evals/test_route_drift.py`` compara este armado con lo que manda ``route``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Any, Literal, Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.enums import Mode
from app.llm.clients.base import LLMClient
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.reranker import FakeReranker
from app.llm.config import LlmRuntimeConfig, load_llm_config
from app.llm.context import build_memory_context
from app.llm.errors import LlmError, ModelNotServedError
from app.llm.prompts.datetime_context import build_now_preamble
from app.llm.prompts.loader import load_prompt
from app.llm.router import _thinking_for_role
from app.llm.schemas import ChatMessage, ToolSpec

# Usuario ficticio: solo liga los stores, que acá nunca tocan la base.
_EVAL_USER_ID = UUID("00000000-0000-4000-8000-000000000e7a")

Bucket = Literal["pass", "fail", "review", "error", "truncated"]


class TurnRequest(BaseModel):
    """Lo que se le manda al modelo en un turno: igual que arma ``route``."""

    alias: str
    messages: list[ChatMessage]
    tools: list[ToolSpec] | None
    thinking: bool | None


class CallRecord(BaseModel):
    """Lo que devolvió una llamada, o el tipo de error si no devolvió."""

    status: Literal["ok", "error"]
    error_type: str | None = None
    text: str = ""
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    finish_reason: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_name: str | None = None
    latency_ms: float = 0.0
    has_reasoning: bool = False


class Verdict(BaseModel):
    """Resultado de corregir un caso.

    ``error`` y ``truncated`` no son respuestas malas: son llamadas que no llegaron a
    contestar (falla del proveedor, o el tope de tokens se fue sin texto ni tool call).
    Se cuentan aparte para no confundir una caída con un modelo que responde mal.
    """

    bucket: Bucket
    details: list[str] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class CaseResult(BaseModel):
    case_id: str
    tags: list[str]
    attempt: int
    record: CallRecord
    verdict: Verdict


class Suite(Protocol):
    """Una suite: de dónde salen sus casos, cómo arma el turno y cómo corrige."""

    name: str
    description: str

    def load(self) -> list[Any]: ...
    def build(self, case: Any, config: LlmRuntimeConfig) -> TurnRequest: ...
    def grade(self, case: Any, record: CallRecord) -> Verdict: ...


def build_turn(
    mode: Mode,
    text: str,
    *,
    now: datetime,
    tz: str,
    config: LlmRuntimeConfig | None = None,
) -> TurnRequest:
    """Arma el turno de ``mode`` para ``text`` como ``route``, sin memoria y con ``now`` fijo."""
    cfg = config if config is not None else load_llm_config()
    mode_cfg = cfg.modes[mode.value]
    model_cfg = cfg.model_for_mode(mode.value)
    # Como ``current_now(tz)`` en el chat: el «ahora» se expresa en el huso del usuario.
    local_now = now.astimezone(ZoneInfo(tz))
    system = f"{load_prompt(mode)}\n\n{build_now_preamble(local_now, tz_label=tz)}"
    # Los stores solo guardan la sesión al construirse: sin sesión no hay I/O, y las
    # specs salen del mismo registro que usa el chat.
    memory_context = build_memory_context(
        session=None,  # type: ignore[arg-type]
        user_id=_EVAL_USER_ID,
        embedder=FakeEmbeddingClient(),
        reranker=FakeReranker(),
        mode_cfg=mode_cfg,
    )
    specs = memory_context.tool_specs(mode_cfg.tools_enabled)
    return TurnRequest(
        alias=model_cfg.alias,
        messages=[
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=text),
        ],
        tools=specs or None,
        thinking=_thinking_for_role(model_cfg.role),
    )


def is_truncated(record: CallRecord) -> bool:
    """El tope de tokens cortó la respuesta antes de que sirviera.

    Sin texto (p. ej. se fue razonando), o con tool calls: el tool loop del chat no
    ejecuta las tool calls de una respuesta cortada, así que tampoco se corrigen.
    """
    if record.finish_reason != "length":
        return False
    return bool(record.tool_calls) or not record.text.strip()


async def call_model(client: LLMClient, turn: TurnRequest) -> CallRecord:
    """Una llamada con los defaults de producción, los del tool loop.

    No fija ``max_tokens`` ni ``temperature``: mide lo mismo que ve el chat. Un
    ``LlmError`` vuelve como registro de error con el nombre de la clase;
    ``ModelNotServedError`` es de configuración y se propaga.
    """
    try:
        result = await client.complete(
            model=turn.alias,
            messages=turn.messages,
            tools=turn.tools,
            thinking=turn.thinking,
        )
    except ModelNotServedError:
        raise
    except LlmError as exc:
        return CallRecord(status="error", error_type=type(exc).__name__)
    if result.finish_reason == "degraded":
        return CallRecord(status="error", error_type="degraded", model_name=result.model_name)
    return CallRecord(
        status="ok",
        text=result.text,
        tool_calls=[{"name": c.name, "arguments": c.arguments} for c in result.tool_calls],
        finish_reason=result.finish_reason,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        model_name=result.model_name,
        latency_ms=result.latency_ms,
        has_reasoning=bool(result.reasoning),
    )


def grade_record(suite: Suite, case: Any, record: CallRecord) -> Verdict:
    """Separa lo que no llegó a contestar antes de pasarle el caso al corrector."""
    if record.status == "error":
        return Verdict(bucket="error", details=[record.error_type or "error"])
    if is_truncated(record):
        return Verdict(bucket="truncated", details=["tope de tokens: respuesta cortada"])
    return suite.grade(case, record)


async def run_suite(
    client: LLMClient,
    suite: Suite,
    cases: Sequence[Any],
    *,
    repeat: int = 1,
    concurrency: int = 4,
    config: LlmRuntimeConfig | None = None,
    on_result: Callable[[CaseResult], None] | None = None,
) -> list[CaseResult]:
    """Corre cada caso ``repeat`` veces, con hasta ``concurrency`` llamadas a la vez."""
    cfg = config if config is not None else load_llm_config()
    gate = asyncio.Semaphore(concurrency)

    async def _one(case: Any, attempt: int) -> CaseResult:
        turn = suite.build(case, cfg)
        async with gate:
            record = await call_model(client, turn)
        result = CaseResult(
            case_id=case.id,
            tags=list(case.tags),
            attempt=attempt,
            record=record,
            verdict=grade_record(suite, case, record),
        )
        if on_result is not None:
            on_result(result)
        return result

    jobs = [_one(case, attempt) for case in cases for attempt in range(1, repeat + 1)]
    return list(await asyncio.gather(*jobs))
