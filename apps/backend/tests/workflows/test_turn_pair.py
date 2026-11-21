"""Tests unitarios de ``load_turn_pair`` y del enqueue por referencia."""

from __future__ import annotations

import base64
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.crypto import encrypt_for_user
from app.enums import Mode, TurnRole
from app.llm.schemas import ChatResponse
from app.models.session import ChatSession
from app.schemas.chat import ChatHttpRequest
from app.services.chat import ChatService
from app.workflows._turn_marker import load_turn_pair


@pytest.fixture(autouse=True)
def _master_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.b64encode(b"0" * 32).decode("ascii")
    monkeypatch.setenv("MEMORY_ENCRYPTION_MASTER_KEY", key)
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _session_returning(rows: list[tuple[int, TurnRole, bytes]]) -> MagicMock:
    session = MagicMock()
    exec_result = MagicMock()
    exec_result.all.return_value = rows
    session.execute = AsyncMock(return_value=exec_result)
    return session


async def test_load_turn_pair_decrypts_user_and_model_turns() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    rows = [
        (0, TurnRole.USER, encrypt_for_user(user_id, "vivo en Rosario")),
        (1, TurnRole.MODEL, encrypt_for_user(user_id, "anotado")),
    ]
    pair = await load_turn_pair(
        _session_returning(rows), user_id=user_id, session_id=session_id, seq=0
    )
    assert pair == ("vivo en Rosario", "anotado")


async def test_load_turn_pair_returns_none_when_incomplete() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    only_user = [(0, TurnRole.USER, encrypt_for_user(user_id, "hola"))]
    assert (
        await load_turn_pair(
            _session_returning(only_user), user_id=user_id, session_id=session_id, seq=0
        )
        is None
    )
    assert (
        await load_turn_pair(_session_returning([]), user_id=user_id, session_id=session_id, seq=0)
        is None
    )


async def test_load_turn_pair_returns_none_when_roles_do_not_match() -> None:
    user_id = uuid.uuid4()
    session_id = uuid.uuid4()
    swapped = [
        (0, TurnRole.MODEL, encrypt_for_user(user_id, "al reves")),
        (1, TurnRole.USER, encrypt_for_user(user_id, "al reves")),
    ]
    assert (
        await load_turn_pair(
            _session_returning(swapped), user_id=user_id, session_id=session_id, seq=0
        )
        is None
    )


async def test_enqueue_consolidation_sends_only_references() -> None:
    user_id = uuid.uuid4()
    chat_session = ChatSession(id=uuid.uuid4(), user_id=user_id, mode=Mode.PRODUCTIVIDAD)
    service = ChatService(
        session=MagicMock(),
        user_id=user_id,
        llm_client=MagicMock(),
        embedder=MagicMock(),
        reranker=MagicMock(),
    )
    body = ChatHttpRequest(text="mensaje privado", mode=Mode.PRODUCTIVIDAD)
    resp = ChatResponse(
        text="respuesta privada",
        session_id=str(chat_session.id),
        model_used="agente",
        finish_reason="stop",
    )

    with patch("app.services.chat.consolidate_turn") as mock_task:
        mock_task.delay = MagicMock()
        await service._enqueue_consolidation(chat_session, body=body, resp=resp, turn_seq=2)

    mock_task.delay.assert_called_once()
    kwargs = mock_task.delay.call_args.kwargs
    assert set(kwargs) == {"user_id", "session_id", "turn_seq", "mode", "enqueued_at"}
    assert kwargs["user_id"] == str(user_id)
    assert kwargs["session_id"] == str(chat_session.id)
    assert kwargs["turn_seq"] == 2
    assert kwargs["mode"] == "productividad"
    assert "mensaje privado" not in str(kwargs)
    assert "respuesta privada" not in str(kwargs)


async def test_enqueue_consolidation_skips_when_turn_not_persisted() -> None:
    user_id = uuid.uuid4()
    chat_session = ChatSession(id=uuid.uuid4(), user_id=user_id, mode=Mode.PRODUCTIVIDAD)
    service = ChatService(
        session=MagicMock(),
        user_id=user_id,
        llm_client=MagicMock(),
        embedder=MagicMock(),
        reranker=MagicMock(),
    )
    body = ChatHttpRequest(text="hola", mode=Mode.PRODUCTIVIDAD)
    resp = ChatResponse(
        text="chau",
        session_id=str(chat_session.id),
        model_used="agente",
        finish_reason="stop",
    )

    with patch("app.services.chat.consolidate_turn") as mock_task:
        mock_task.delay = MagicMock()
        await service._enqueue_consolidation(chat_session, body=body, resp=resp, turn_seq=None)

    mock_task.delay.assert_not_called()
