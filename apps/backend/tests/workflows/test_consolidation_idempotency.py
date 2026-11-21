"""Idempotencia de ``consolidate_turn``: un reintento no duplica memoria ni auditoria.

Dos mecanismos, los dos en la capa de workflows (sin tocar ``app/memory/``):

- Cada intento es todo-o-nada: la marca del turno, las ops y su auditoria van en un
  SAVEPOINT; una falla en el medio (p.ej. un 429 del embedder en la segunda op) revierte
  el intento entero y el reintento arranca de cero.
- Con ``turn_seq``, el turno del usuario queda marcado como consolidado en la misma
  transaccion (``claim_turn``): una re-entrega del broker encuentra la marca y no escribe.

UNIT: ``claim_turn`` parcheado y stores mockeados (sin DB). INTEGRATION
(``@pytest.mark.integration``): contra la DB de tests, con turnos reales.
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Mode, TurnRole
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.reranker import FakeReranker
from app.llm.errors import LlmRateLimitError, LlmTransientError
from app.llm.schemas import CompletionResult
from app.memory.conversation_turns import ConversationTurnStore
from app.models.audit import AuditLog
from app.models.conversation_turn import ConversationTurn
from app.models.memory import SemanticMemory
from app.models.session import ChatSession
from app.models.user import User
from app.schemas.conversation_turn import ConversationTurnCreate
from app.workflows._turn_marker import TurnClaim, claim_turn, load_turn_pair
from app.workflows.consolidation import _async_consolidate

USER_ID = str(uuid.uuid4())
SESSION_ID = str(uuid.uuid4())
MODE = "vida"


def _llm_with_ops(*contents: str) -> FakeLlmClient:
    """``FakeLlmClient`` que devuelve un ADD semantic por cada ``content``."""
    ops = [{"op": "ADD", "layer": "semantic", "content": content} for content in contents]
    client = FakeLlmClient(models=frozenset({"agente"}))
    client.queue_result(
        CompletionResult(
            text=json.dumps(ops),
            finish_reason="stop",
            prompt_tokens=10,
            completion_tokens=20,
            model_name="agente",
            latency_ms=1.0,
        )
    )
    return client


# ---------------------------------------------------------------------------
# UNIT — sin DB
# ---------------------------------------------------------------------------


class TestTurnClaimUnit:
    """Que hace la consolidacion con cada resultado de ``claim_turn``."""

    async def _consolidate(
        self,
        claim: TurnClaim | None,
        *,
        session_id: str = SESSION_ID,
        turn_seq: int = 0,
        pair: tuple[str, str] | None = ("vivo en Rosario", "anotado"),
    ) -> tuple[int, AsyncMock, AsyncMock]:
        semantic = AsyncMock()
        semantic.add = AsyncMock(return_value=MagicMock(id=uuid.uuid4()))
        claim_mock = AsyncMock(return_value=claim)
        with (
            patch("app.workflows.consolidation.load_turn_pair", AsyncMock(return_value=pair)),
            patch("app.workflows.consolidation.claim_turn", claim_mock),
            patch("app.workflows.consolidation.SemanticMemoryStore", return_value=semantic),
            patch("app.workflows.consolidation.ProceduralMemoryStore", return_value=AsyncMock()),
            patch("app.workflows.consolidation.AuditStore", return_value=AsyncMock()),
        ):
            applied = await _async_consolidate(
                user_id=USER_ID,
                session_id=session_id,
                mode=MODE,
                turn_seq=turn_seq,
                llm_client=_llm_with_ops("El usuario vive en Rosario."),
                embedder=FakeEmbeddingClient(),
                reranker=FakeReranker(),
                session=MagicMock(spec=AsyncSession),
            )
        return applied, semantic.add, claim_mock

    async def test_claimed_turn_applies_the_ops(self) -> None:
        applied, add, claim_mock = await self._consolidate(TurnClaim.CLAIMED)

        assert applied == 1
        add.assert_awaited_once()
        claim_mock.assert_awaited_once()
        assert claim_mock.await_args.kwargs == {
            "user_id": UUID(USER_ID),
            "session_id": UUID(SESSION_ID),
            "seq": 0,
        }

    async def test_already_consolidated_turn_writes_nothing(self) -> None:
        """Re-entrega de un turno ya commiteado: no se escribe ni se audita de nuevo."""
        applied, add, _ = await self._consolidate(TurnClaim.ALREADY_CONSOLIDATED)

        assert applied == 0
        add.assert_not_awaited()

    async def test_missing_turn_at_claim_is_consolidated_anyway(self) -> None:
        """Si la fila se purgo durante la extraccion, las ops ya extraidas se aplican igual."""
        applied, add, _ = await self._consolidate(TurnClaim.MISSING)

        assert applied == 1
        add.assert_awaited_once()

    async def test_missing_turn_pair_is_noop(self) -> None:
        """Si el par de turnos no existe en conversation_turns, no llama al LLM ni escribe."""
        applied, add, claim_mock = await self._consolidate(None, pair=None)

        assert applied == 0
        add.assert_not_awaited()
        claim_mock.assert_not_awaited()

    async def test_garbage_session_id_is_noop(self) -> None:
        applied, add, claim_mock = await self._consolidate(None, session_id="no-es-un-uuid")

        assert applied == 0
        add.assert_not_awaited()
        claim_mock.assert_not_awaited()


async def test_a_transient_failure_leaves_through_the_attempt_savepoint() -> None:
    """Un 429 del embedder en la segunda op sale por el SAVEPOINT del intento (se revierte)."""
    semantic = AsyncMock()
    semantic.add = AsyncMock(
        side_effect=[MagicMock(id=uuid.uuid4()), LlmRateLimitError("HTTP 429")]
    )
    session = MagicMock(spec=AsyncSession)
    with (
        patch(
            "app.workflows.consolidation.load_turn_pair",
            AsyncMock(return_value=("dos datos", "anotados")),
        ),
        patch("app.workflows.consolidation.claim_turn", AsyncMock(return_value=TurnClaim.CLAIMED)),
        patch("app.workflows.consolidation.SemanticMemoryStore", return_value=semantic),
        patch("app.workflows.consolidation.ProceduralMemoryStore", return_value=AsyncMock()),
        patch("app.workflows.consolidation.AuditStore", return_value=AsyncMock()),
        pytest.raises(LlmTransientError),
    ):
        await _async_consolidate(
            user_id=USER_ID,
            session_id=SESSION_ID,
            mode=MODE,
            turn_seq=0,
            llm_client=_llm_with_ops("dato uno", "dato dos"),
            embedder=FakeEmbeddingClient(),
            reranker=FakeReranker(),
            session=session,
        )

    # Tres SAVEPOINT: el del intento (el primero en abrirse) y uno por op. El ultimo en
    # cerrarse es el del intento, y lo cerro la excepcion: se revierte la op uno tambien.
    assert session.begin_nested.call_count == 3
    exit_calls = session.begin_nested.return_value.__aexit__.await_args_list
    assert [call.args[0] for call in exit_calls] == [
        None,
        LlmRateLimitError,
        LlmRateLimitError,
    ]


# ---------------------------------------------------------------------------
# INTEGRATION — contra la DB de tests
# ---------------------------------------------------------------------------


class _FlakyEmbedder(FakeEmbeddingClient):
    """Embedder fake que responde 429 en la llamada ``fail_on_call`` (1-based)."""

    def __init__(self, *, fail_on_call: int) -> None:
        super().__init__()
        self._calls = 0
        self._fail_on_call = fail_on_call

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self._calls += 1
        if self._calls == self._fail_on_call:
            raise LlmRateLimitError("HTTP 429")
        return await super().embed(texts)


async def _seed_turn(session: AsyncSession) -> tuple[UUID, UUID]:
    """Usuario + sesion + el par de turnos (user en seq 0, modelo en seq 1)."""
    user = User()
    session.add(user)
    await session.flush()
    chat_session = ChatSession(user_id=user.id, mode=Mode.VIDA)
    session.add(chat_session)
    await session.flush()
    store = ConversationTurnStore(session, user.id)
    await store.add(
        ConversationTurnCreate(
            session_id=chat_session.id, role=TurnRole.USER, content="vivo en Rosario", seq=0
        )
    )
    await store.add(
        ConversationTurnCreate(
            session_id=chat_session.id, role=TurnRole.MODEL, content="anotado", seq=1
        )
    )
    return user.id, chat_session.id


async def _count(session: AsyncSession, model: type, user_id: UUID) -> int:
    stmt = select(func.count()).select_from(model).where(model.user_id == user_id)
    return (await session.execute(stmt)).scalar_one()


async def _user_turn_is_marked(session: AsyncSession, session_id: UUID) -> bool:
    stmt = select(ConversationTurn.updated_at != ConversationTurn.created_at).where(
        ConversationTurn.session_id == session_id, ConversationTurn.seq == 0
    )
    return bool((await session.execute(stmt)).scalar_one())


async def _consolidate_in(
    session: AsyncSession,
    user_id: UUID,
    session_id: UUID,
    llm: FakeLlmClient,
    *,
    turn_seq: int = 0,
    embedder: FakeEmbeddingClient | None = None,
) -> int:
    return await _async_consolidate(
        user_id=str(user_id),
        session_id=str(session_id),
        mode=MODE,
        turn_seq=turn_seq,
        llm_client=llm,
        embedder=embedder or FakeEmbeddingClient(),
        reranker=FakeReranker(),
        session=session,
    )


@pytest.mark.integration
class TestConsolidationIdempotencyIntegration:
    async def test_redelivered_turn_does_not_duplicate_memory_or_audit(
        self, db_session: AsyncSession
    ) -> None:
        user_id, session_id = await _seed_turn(db_session)

        first = await _consolidate_in(
            db_session, user_id, session_id, _llm_with_ops("El usuario vive en Rosario.")
        )
        # Re-entrega del mismo mensaje: el LLM podria redactar distinto, igual no escribe.
        second = await _consolidate_in(
            db_session, user_id, session_id, _llm_with_ops("El usuario es de Rosario.")
        )

        assert (first, second) == (1, 0)
        assert await _count(db_session, SemanticMemory, user_id) == 1
        assert await _count(db_session, AuditLog, user_id) == 1
        assert await _user_turn_is_marked(db_session, session_id)

    async def test_transient_failure_rolls_back_the_whole_attempt(
        self, db_session: AsyncSession
    ) -> None:
        """429 del embedder en la segunda op: ni hechos, ni auditoria, ni marca."""
        user_id, session_id = await _seed_turn(db_session)

        with pytest.raises(LlmTransientError):
            await _consolidate_in(
                db_session,
                user_id,
                session_id,
                _llm_with_ops("dato uno", "dato dos"),
                embedder=_FlakyEmbedder(fail_on_call=2),
            )

        assert await _count(db_session, SemanticMemory, user_id) == 0
        assert await _count(db_session, AuditLog, user_id) == 0
        assert not await _user_turn_is_marked(db_session, session_id)

        # El reintento arranca de cero y guarda cada hecho una sola vez.
        retried = await _consolidate_in(
            db_session, user_id, session_id, _llm_with_ops("dato uno", "dato dos")
        )
        assert retried == 2
        assert await _count(db_session, SemanticMemory, user_id) == 2
        assert await _count(db_session, AuditLog, user_id) == 2
        assert await _user_turn_is_marked(db_session, session_id)

    async def test_missing_turn_is_noop(self, db_session: AsyncSession) -> None:
        user_id, session_id = await _seed_turn(db_session)

        applied = await _consolidate_in(
            db_session, user_id, session_id, _llm_with_ops("dato"), turn_seq=8
        )

        assert applied == 0
        assert not await _user_turn_is_marked(db_session, session_id)

    async def test_load_turn_pair_decrypts_and_enforces_owner(
        self, db_session: AsyncSession
    ) -> None:
        user_id, session_id = await _seed_turn(db_session)
        other_user, _ = await _seed_turn(db_session)

        pair = await load_turn_pair(db_session, user_id=user_id, session_id=session_id, seq=0)
        foreign = await load_turn_pair(db_session, user_id=other_user, session_id=session_id, seq=0)
        missing = await load_turn_pair(db_session, user_id=user_id, session_id=session_id, seq=8)

        assert pair == ("vivo en Rosario", "anotado")
        assert foreign is None
        assert missing is None

    async def test_claim_turn_claims_once(self, db_session: AsyncSession) -> None:
        user_id, session_id = await _seed_turn(db_session)

        first = await claim_turn(db_session, user_id=user_id, session_id=session_id, seq=0)
        second = await claim_turn(db_session, user_id=user_id, session_id=session_id, seq=0)

        assert (first, second) == (TurnClaim.CLAIMED, TurnClaim.ALREADY_CONSOLIDATED)

    async def test_claim_turn_only_takes_the_user_turn_of_the_owner(
        self, db_session: AsyncSession
    ) -> None:
        """El turno del modelo y el turno de otro usuario no se pueden marcar."""
        user_id, session_id = await _seed_turn(db_session)
        other_user, _ = await _seed_turn(db_session)

        model_turn = await claim_turn(db_session, user_id=user_id, session_id=session_id, seq=1)
        foreign = await claim_turn(db_session, user_id=other_user, session_id=session_id, seq=0)

        assert (model_turn, foreign) == (TurnClaim.MISSING, TurnClaim.MISSING)
        assert not await _user_turn_is_marked(db_session, session_id)
