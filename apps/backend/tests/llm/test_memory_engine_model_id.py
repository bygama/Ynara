"""El id del modelo que llega a ``audit_log.origin_model_id`` (ADR-028), UNIT.

``consolidate`` y ``summarize`` devuelven, junto a lo extraído, el id del modelo que
respondió tal como lo informa el cliente (``CompletionResult.model_name``), limpio para
que entre en la columna; el alias si el cliente no informa uno. ``apply_ops`` lo pasa a
cada fila de audit. Sin DB ni red: ``FakeLlmClient`` programable y stores mockeados; el
``AuditStore`` va con ``create_autospec`` para que un kwarg mal escrito falle (``apply_ops``
traga las excepciones por op y lo dejaría pasar como ``applied == 0``).
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, create_autospec
from uuid import uuid4

import pytest

from app.enums import Mode
from app.llm.clients.fakes import FakeLlmClient
from app.llm.memory_engine import AgentMemoryEngine, MemoryOp, _audit_model_id, apply_ops
from app.llm.schemas import CompletionResult
from app.memory.audit import AuditStore, _is_valid_origin_model_id
from app.models.audit import ORIGIN_MODEL_ID_MAX_LENGTH

_ALIAS = "agente"
_PROVIDER_ID = "org/agent-model-2026-06"
_OPS_JSON = json.dumps([{"op": "ADD", "layer": "semantic", "content": "El usuario vive en Salta."}])
_SUMMARY_JSON = json.dumps({"summary": "El usuario habló de su semana.", "topics": {}})

# Ids "de afuera" que no entran tal cual en la columna (largo, bordes, control, NBSP...).
_UNRULY_IDS = [
    "x" * 500,
    "a" * (ORIGIN_MODEL_ID_MAX_LENGTH - 1) + " " + "b" * 20,
    " org/agent ",
    "org/\nagent",
    "org/agent\x00",
    "\torg/agent\r\n",
    "org/agent\u00a0",
    "\u00a0\u2028",
    "\x00\x01\x02",
    "   ",
    "",
]


def _result(text: str, model_name: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=5,
        model_name=model_name,
        latency_ms=1.0,
    )


def _engine_answering(text: str, model_name: str) -> AgentMemoryEngine:
    client = FakeLlmClient(models=frozenset({_ALIAS}))
    client.queue_result(_result(text, model_name))
    return AgentMemoryEngine(llm_client=client, model=_ALIAS)


# ---------------------------------------------------------------------------
# _audit_model_id
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reported", "expected"),
    [
        (_PROVIDER_ID, _PROVIDER_ID),
        (" org/agent ", "org/agent"),
        ("org/\nagent", "org/agent"),
        ("org/agent\u00a0", "org/agent"),
        ("x" * 500, "x" * ORIGIN_MODEL_ID_MAX_LENGTH),
        (
            "a" * (ORIGIN_MODEL_ID_MAX_LENGTH - 1) + " " + "b" * 20,
            "a" * (ORIGIN_MODEL_ID_MAX_LENGTH - 1),
        ),
        ("", _ALIAS),
        ("   ", _ALIAS),
        ("\x00\x01\x02", _ALIAS),
    ],
)
def test_audit_model_id_cleans_what_the_client_reported(reported: str, expected: str) -> None:
    assert _audit_model_id(reported, _ALIAS) == expected


@pytest.mark.parametrize("reported", _UNRULY_IDS)
def test_audit_model_id_always_fits_the_column(reported: str) -> None:
    """Lo que sale del normalizador lo acepta siempre ``AuditStore.record``."""
    model_id = _audit_model_id(reported, _ALIAS)
    assert model_id is not None
    assert _is_valid_origin_model_id(model_id)


def test_audit_model_id_is_none_without_a_usable_id_or_alias() -> None:
    assert _audit_model_id("", "") is None
    assert _audit_model_id("\n", " ") is None


# ---------------------------------------------------------------------------
# consolidate / summarize
# ---------------------------------------------------------------------------


async def test_consolidate_reports_the_provider_model_id() -> None:
    extraction = await _engine_answering(_OPS_JSON, _PROVIDER_ID).consolidate(
        user_msg="vivo en Salta", model_response="anotado", mode="productividad"
    )
    assert len(extraction.ops) == 1
    assert extraction.model_id == _PROVIDER_ID


async def test_consolidate_falls_back_to_the_alias_without_a_model_name() -> None:
    """Un cliente que no informa el id (el fake con ``model_name=""``) deja el alias."""
    extraction = await _engine_answering(_OPS_JSON, "").consolidate(
        user_msg="vivo en Salta", model_response="anotado", mode="productividad"
    )
    assert extraction.model_id == _ALIAS


async def test_consolidate_keeps_the_model_when_there_is_nothing_to_save() -> None:
    """El modelo respondió (aunque sin ops útiles): el id viaja igual."""
    extraction = await _engine_answering("no es json", _PROVIDER_ID).consolidate(
        user_msg="hola", model_response="hola!", mode="vida"
    )
    assert extraction.ops == []
    assert extraction.model_id == _PROVIDER_ID


@pytest.mark.parametrize("reported", _UNRULY_IDS)
async def test_consolidate_hands_a_valid_model_id_with_its_ops(reported: str) -> None:
    extraction = await _engine_answering(_OPS_JSON, reported).consolidate(
        user_msg="vivo en Salta", model_response="anotado", mode="productividad"
    )
    assert extraction.ops
    assert extraction.model_id is not None
    assert _is_valid_origin_model_id(extraction.model_id)


async def test_summarize_reports_the_provider_model_id() -> None:
    summary = await _engine_answering(_SUMMARY_JSON, _PROVIDER_ID).summarize(
        transcript="Usuario: hola", mode="vida"
    )
    assert summary.summary == "El usuario habló de su semana."
    assert summary.model_id == _PROVIDER_ID


async def test_summarize_falls_back_to_the_alias_without_a_model_name() -> None:
    summary = await _engine_answering(_SUMMARY_JSON, "").summarize(
        transcript="Usuario: hola", mode="vida"
    )
    assert summary.model_id == _ALIAS


@pytest.mark.parametrize("reported", _UNRULY_IDS)
async def test_summarize_hands_a_valid_model_id_with_its_summary(reported: str) -> None:
    summary = await _engine_answering(_SUMMARY_JSON, reported).summarize(
        transcript="Usuario: hola", mode="vida"
    )
    assert summary.summary
    assert summary.model_id is not None
    assert _is_valid_origin_model_id(summary.model_id)


# ---------------------------------------------------------------------------
# apply_ops
# ---------------------------------------------------------------------------


async def test_apply_ops_writes_the_model_id_in_every_audit_row() -> None:
    semantic = AsyncMock()
    semantic.add = AsyncMock(return_value=MagicMock(id=uuid4()))
    procedural = AsyncMock()
    procedural.upsert = AsyncMock(return_value=MagicMock(id=uuid4()))
    audit = create_autospec(AuditStore, instance=True)
    ops = [
        MemoryOp(op="ADD", layer="semantic", content="El usuario vive en Salta."),
        MemoryOp(op="ADD", layer="procedural", key="idioma", value={"lang": "es-AR"}),
        MemoryOp(op="NOOP", layer="semantic"),
    ]

    applied = await apply_ops(
        ops,
        semantic_store=semantic,
        procedural_store=procedural,
        audit_store=audit,
        origin_model_id=_PROVIDER_ID,
        origin_mode=Mode.PRODUCTIVIDAD,
    )

    assert applied == 2
    assert audit.record.await_count == 2
    for call in audit.record.await_args_list:
        assert call.kwargs["origin_model_id"] == _PROVIDER_ID
        assert call.kwargs["origin_mode"] == Mode.PRODUCTIVIDAD
        # El enum viejo ya no se escribe: queda en su default (NULL).
        assert "origin_model" not in call.kwargs
