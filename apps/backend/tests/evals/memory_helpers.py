"""Helpers de los tests de la suite de extracción de memoria.

Arman respuestas del modelo a mano y las pasan por el camino real: ``extract`` con
``FakeLlmClient`` y después el corrector. Nunca se llama a un proveedor.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.constants import MODEL_ALIAS_AGENT
from app.llm.clients.fakes import FakeLlmClient
from app.llm.schemas import CompletionResult
from evals.cases import MemoryExtractionCase
from evals.harness import Verdict, grade_record
from evals.suites.memory_extraction import (
    MemoryExtractionSuite,
    extract,
)

SUITE = MemoryExtractionSuite()
CASES = SUITE.load()
NOOP_OP = {"op": "NOOP", "layer": "semantic"}


def make_result(text: str, *, finish_reason: str = "stop") -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason=finish_reason,
        prompt_tokens=400,
        completion_tokens=30,
        model_name="org/agent",
        latency_ms=180.0,
    )


def ops_json(*ops: dict[str, Any]) -> str:
    return json.dumps(list(ops), ensure_ascii=False)


def add_op(content: str) -> dict[str, Any]:
    return {"op": "ADD", "layer": "semantic", "content": content}


def pref_op(key: str, **value: Any) -> dict[str, Any]:
    return {"op": "ADD", "layer": "procedural", "key": key, "value": value}


def as_op(write: str | dict[str, Any]) -> dict[str, Any]:
    """Un ejemplo de ``ideal`` / ``wrong`` como op: texto = hecho, objeto = preferencia."""
    if isinstance(write, str):
        return add_op(write)
    return {"op": "ADD", "layer": "procedural", **write}


def make_case(**expect: Any) -> MemoryExtractionCase:
    return MemoryExtractionCase.model_validate(
        {
            "id": "mx-test",
            "user": "Me llamo Renata y vivo en Rosario.",
            "assistant": "Dale, Renata.",
            "expect": expect,
        }
    )


async def verdict_for(case: MemoryExtractionCase, text: str, **kwargs: Any) -> Verdict:
    fake = FakeLlmClient(models=frozenset({MODEL_ALIAS_AGENT}))
    fake.queue_result(make_result(text, **kwargs))
    return grade_record(SUITE, case, await extract(fake, case))


async def bucket_for(case: MemoryExtractionCase, *ops: dict[str, Any]) -> str:
    return (await verdict_for(case, ops_json(*ops))).bucket
