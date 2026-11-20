"""Tests unitarios de la guarda de borrado total en consolidación (#56).

Verifican que si el usuario borra toda su memoria mientras una task de consolidación
está en cola o reintentando, el worker descarta el turno en vez de volver a guardar
recuerdos anteriores al borrado — tanto antes de llamar al LLM como si el wipe ocurre
durante la extracción.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.enums import Mode
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.reranker import FakeReranker
from app.llm.schemas import CompletionResult
from app.workflows._wipe_guard import was_wiped_after
from app.workflows.consolidation import _consolidate_turn_in_db


def _mock_session(*, scalar_result: object = None) -> MagicMock:
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.scalar_one_or_none.return_value = scalar_result
    session.execute = AsyncMock(return_value=exec_result)

    @asynccontextmanager
    async def _nested():
        yield

    session.begin_nested = _nested
    return session


def _llm_with_add_op() -> FakeLlmClient:
    llm = FakeLlmClient(models=frozenset({"agente"}))
    llm.queue_result(
        CompletionResult(
            text=json.dumps(
                {
                    "ops": [
                        {
                            "op": "ADD",
                            "layer": "semantic",
                            "content": "le gusta el mate amargo",
                        }
                    ]
                }
            ),
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=10,
            model_name="org/agent",
            latency_ms=1.0,
        )
    )
    return llm


@pytest.mark.asyncio
async def test_was_wiped_after_true_when_wipe_row_exists() -> None:
    session = _mock_session(scalar_result=uuid.uuid4())
    assert await was_wiped_after(session, user_id=uuid.uuid4(), since=datetime.now(UTC)) is True


@pytest.mark.asyncio
async def test_was_wiped_after_false_when_no_wipe_row() -> None:
    session = _mock_session(scalar_result=None)
    assert await was_wiped_after(session, user_id=uuid.uuid4(), since=datetime.now(UTC)) is False


@pytest.mark.asyncio
async def test_consolidate_turn_aborts_before_llm_when_wiped_after_enqueue() -> None:
    session = _mock_session()
    llm = _llm_with_add_op()
    with (
        patch("app.workflows.consolidation.was_wiped_after", AsyncMock(return_value=True)),
        patch("app.workflows.consolidation.apply_ops", AsyncMock(return_value=1)) as mock_apply,
    ):
        applied = await _consolidate_turn_in_db(
            session=session,
            user_id=uuid.uuid4(),
            source_session_id=uuid.uuid4(),
            turn_seq=1,
            user_msg="me gusta el mate amargo",
            model_response="anotado",
            mode=Mode.PRODUCTIVIDAD.value,
            origin_mode=Mode.PRODUCTIVIDAD,
            llm_client=llm,
            embedder=FakeEmbeddingClient(),
            reranker=FakeReranker(),
            enqueued_at=datetime.now(UTC),
        )
    assert applied == 0
    assert llm.complete_calls == []
    mock_apply.assert_not_awaited()


@pytest.mark.asyncio
async def test_consolidate_turn_aborts_before_apply_when_wiped_during_extraction() -> None:
    session = _mock_session()
    llm = _llm_with_add_op()
    # Primer check (antes del LLM) da False; segundo check (antes de apply_ops) da True.
    with (
        patch(
            "app.workflows.consolidation.was_wiped_after",
            AsyncMock(side_effect=[False, True]),
        ),
        patch("app.workflows.consolidation.apply_ops", AsyncMock(return_value=1)) as mock_apply,
    ):
        applied = await _consolidate_turn_in_db(
            session=session,
            user_id=uuid.uuid4(),
            source_session_id=uuid.uuid4(),
            turn_seq=1,
            user_msg="me gusta el mate amargo",
            model_response="anotado",
            mode=Mode.PRODUCTIVIDAD.value,
            origin_mode=Mode.PRODUCTIVIDAD,
            llm_client=llm,
            embedder=FakeEmbeddingClient(),
            reranker=FakeReranker(),
            enqueued_at=datetime.now(UTC),
        )
    assert applied == 0
    assert len(llm.complete_calls) == 1
    mock_apply.assert_not_awaited()
