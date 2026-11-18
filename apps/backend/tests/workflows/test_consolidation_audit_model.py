"""La consolidación audita con el modelo que informó el proveedor (ADR-028), UNIT.

Corren las tasks como las corre el worker (``.apply()``), con la factory real
(``LLM_BACKEND=api``) y un transporte HTTP programado, sin red ni DB: ``worker_session``
entrega una sesión mock y los stores están parcheados. El ``AuditStore`` va con
``create_autospec``, así un kwarg que no existe en ``record`` falla en vez de pasar.

- El turno y el episodio escriben en ``origin_model_id`` el ``model`` que devolvió el
  proveedor (un id fechado, distinto del configurado), y no tocan ``origin_model``.
- Si el proveedor no informa ``model``, queda el id configurado para el alias.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, create_autospec, patch
from uuid import uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.enums import AuditOperation, MemoryLayer, Mode
from app.llm.clients.embedding import FakeEmbeddingClient
from app.memory.audit import AuditStore
from app.workflows import consolidation
from app.workflows._turn_marker import TurnClaim
from app.workflows.consolidation import consolidate_session, consolidate_turn

USER_ID = str(uuid4())
SESSION_ID = str(uuid4())
_OPS = json.dumps([{"op": "ADD", "layer": "semantic", "content": "El usuario vive en Salta."}])
_SUMMARY = json.dumps({"summary": "El usuario organizó su semana.", "topics": {}})
_TURN = {
    "user_id": USER_ID,
    "session_id": SESSION_ID,
    "user_msg": "vivo en Salta",
    "model_response": "anotado",
    "mode": "productividad",
    "turn_seq": 0,
}

# El id que el proveedor dice haber usado: fechado, distinto del configurado.
_REPORTED_MODEL = "org/agent-2026-06-01"
_CONFIGURED_MODEL = "org/agent"
_PROVIDER = {
    "name": "principal",
    "base_url": "https://api.principal.test/v1",
    "models": {"conversacional": "org/chat", "agente": _CONFIGURED_MODEL},
}


def _api_settings() -> Settings:
    """Settings aislado de cualquier .env, con el LLM por API y embeddings fake."""
    kwargs: dict[str, object] = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test:test@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        "environment": "development",
        "JWT_SECRET": "test-secret",
        "EMBEDDING_BACKEND": "fake",
        "LLM_BACKEND": "api",
        "LLM_PROVIDERS": json.dumps([_PROVIDER]),
        "llm_api_keys": {"principal": "sk-test-principal-0123"},
    }
    return Settings(**kwargs)  # type: ignore[arg-type]


def _completion(text: str, model: str | None) -> httpx.Response:
    body: dict[str, object] = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }
    if model is not None:
        body["model"] = model
    return httpx.Response(200, json=body)


@asynccontextmanager
async def _mock_worker_session(_settings: Settings) -> AsyncIterator[AsyncSession]:
    yield MagicMock(spec=AsyncSession)


@pytest.fixture
def audit() -> Iterator[MagicMock]:
    """El worker sin DB: stores parcheados; devuelve el ``AuditStore`` espiable."""
    audit_store = create_autospec(AuditStore, instance=True)
    semantic = AsyncMock()
    semantic.add = AsyncMock(return_value=MagicMock(id=uuid4()))
    with (
        patch.object(consolidation, "worker_session", _mock_worker_session),
        patch.object(consolidation, "claim_turn", AsyncMock(return_value=TurnClaim.CLAIMED)),
        patch.object(consolidation, "SemanticMemoryStore", return_value=semantic),
        patch.object(consolidation, "ProceduralMemoryStore", return_value=AsyncMock()),
        patch.object(consolidation, "AuditStore", return_value=audit_store),
        patch.object(consolidation, "_build_embedder", return_value=FakeEmbeddingClient()),
    ):
        yield audit_store


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Programa las respuestas del proveedor y pone al worker en ``LLM_BACKEND=api``."""

    def _program(*responses: httpx.Response) -> None:
        pending = list(responses)
        real_async_client = httpx.AsyncClient

        def _answer(_request: httpx.Request) -> httpx.Response:
            if not pending:
                return httpx.Response(500, json={"error": {"message": "request de mas"}})
            return pending.pop(0)

        def _client() -> httpx.AsyncClient:
            return real_async_client(transport=httpx.MockTransport(_answer))

        monkeypatch.setattr("app.llm.clients.factory.httpx.AsyncClient", _client)
        monkeypatch.setattr(consolidation, "get_settings", _api_settings)

    return _program


# ---------------------------------------------------------------------------
# consolidate_turn
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reported", "expected"),
    [(_REPORTED_MODEL, _REPORTED_MODEL), (None, _CONFIGURED_MODEL)],
    ids=["provider-reports-model", "provider-omits-model"],
)
def test_the_turn_audits_the_model_that_answered(
    audit: MagicMock,
    provider: Callable[..., None],
    reported: str | None,
    expected: str,
) -> None:
    provider(_completion(_OPS, reported))

    result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    audit.record.assert_awaited_once()
    kwargs = audit.record.await_args.kwargs
    assert kwargs["origin_model_id"] == expected
    assert kwargs["origin_mode"] == Mode.PRODUCTIVIDAD
    assert kwargs["operation"] == AuditOperation.WRITE
    assert "origin_model" not in kwargs


# ---------------------------------------------------------------------------
# consolidate_session
# ---------------------------------------------------------------------------


def test_the_episode_audits_the_model_that_summarized(
    audit: MagicMock, provider: Callable[..., None]
) -> None:
    provider(_completion(_SUMMARY, _REPORTED_MODEL))
    turns = AsyncMock()
    turns.list_for_session = AsyncMock(
        return_value=[
            SimpleNamespace(role="user", content="organicé la semana"),
            SimpleNamespace(role="model", content="buenísimo"),
        ]
    )
    turns.purge_session = AsyncMock(return_value=2)
    episodic = AsyncMock()
    episodic.add = AsyncMock(return_value=MagicMock(id=uuid4()))

    with (
        patch.object(consolidation, "_episode_exists", AsyncMock(return_value=False)),
        patch.object(consolidation, "ConversationTurnStore", return_value=turns),
        patch.object(consolidation, "EpisodicMemoryStore", return_value=episodic),
    ):
        result = consolidate_session.apply(
            kwargs={"user_id": USER_ID, "session_id": SESSION_ID, "mode": "productividad"}
        )

    assert result.state == "SUCCESS"
    episodic.add.assert_awaited_once()
    audit.record.assert_awaited_once()
    kwargs = audit.record.await_args.kwargs
    assert kwargs["origin_model_id"] == _REPORTED_MODEL
    assert kwargs["target_layer"] == MemoryLayer.EPISODIC
    assert "origin_model" not in kwargs
