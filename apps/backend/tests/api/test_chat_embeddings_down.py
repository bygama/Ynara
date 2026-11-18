"""``/v1/chat`` y ``/v1/chat/stream`` con el proveedor de embeddings caído (UNIT, sin DB).

Los modos que leen la memoria semántica o episódica embeddean el mensaje antes de armar
el contexto. Si el proveedor de embeddings falla, el turno degrada igual que ante un error
del modelo: 200 con ``finish_reason="degraded"`` y el texto de fallback, sin llamar al
modelo, sin persistir los turnos y sin encolar la consolidación. El stream lo emite como
cualquier turno degradado (``token`` + ``done``), sin evento ``error``.

Corre el stack HTTP real (deps, ``ChatService.run_turn`` y ``route()``) con la DB afuera:
el camino que se prueba falla antes de la primera query y el turno degradado no escribe
nada. La sesión es un stub y la resolución de la ``ChatSession``, el historial y el
enqueue van parcheados. El turno degradado contra la DB real ya lo cubren
``test_chat.py`` y ``test_chat_stream.py`` (integración).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_db, get_embedder, get_llm_client, get_reranker
from app.core.security import create_access_token
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.reranker import FakeReranker
from app.llm.errors import LlmRateLimitError
from app.llm.router import _FALLBACK_TEXT
from app.main import app

# Texto con forma de dato de usuario: el modo memoria lo embeddea para buscar.
_USER_TEXT = "el martes tengo turno con la psicóloga"


@dataclass
class _Turn:
    """Lo que el test inspecciona de un turno: los clientes, la DB stub y el enqueue."""

    session_id: uuid.UUID
    llm: FakeLlmClient
    embedder: MagicMock
    db: MagicMock
    savepoint: AsyncMock
    turns_store: MagicMock
    consolidate_turn: MagicMock


@pytest.fixture
def turn() -> Iterator[_Turn]:
    """Deps por override y la DB afuera; el proveedor de embeddings responde un 429."""
    llm = FakeLlmClient(models=frozenset({"conversacional", "agente"}))
    embedder = MagicMock()
    embedder.embed = AsyncMock(side_effect=LlmRateLimitError("HTTP 429", retry_after_s=2.0))
    savepoint = AsyncMock()
    db = MagicMock()
    db.begin_nested = AsyncMock(return_value=savepoint)
    db.get = AsyncMock(return_value=None)  # sin fila del usuario: huso de la app
    db.commit = AsyncMock()

    async def _override_db() -> AsyncIterator[MagicMock]:
        yield db

    turns_store = MagicMock()
    turns_store.list_recent_for_session = AsyncMock(return_value=[])
    chat_session = SimpleNamespace(id=uuid.uuid4())

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_llm_client] = lambda: llm
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_reranker] = FakeReranker
    try:
        with (
            patch("app.api.v1.chat.resolve_chat_session", AsyncMock(return_value=chat_session)),
            patch("app.services.chat.ConversationTurnStore", return_value=turns_store),
            patch("app.services.chat.consolidate_turn") as consolidate_turn,
        ):
            yield _Turn(
                session_id=chat_session.id,
                llm=llm,
                embedder=embedder,
                db=db,
                savepoint=savepoint,
                turns_store=turns_store,
                consolidate_turn=consolidate_turn,
            )
    finally:
        app.dependency_overrides.clear()


async def _post(path: str) -> httpx.Response:
    """Un turno del modo memoria (alias agente: escribe memoria y se consolidaría)."""
    # raise_app_exceptions=False: un error que se escapa llega como el 500 que vería el
    # cliente, no como la excepción del server.
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    headers = {"Authorization": f"Bearer {create_access_token(str(uuid.uuid4()))}"}
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            path, json={"text": _USER_TEXT, "mode": "memoria"}, headers=headers
        )


def _events(raw: str) -> list[tuple[str, dict[str, Any]]]:
    """Los eventos del wire SSE de ``_sse_event``: ``event: <nombre>`` + ``data: <json>``."""
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.split("\n\n"):
        if not block.strip():
            continue
        fields = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((fields["event"], json.loads(fields["data"])))
    return events


def _assert_degraded_without_side_effects(turn: _Turn) -> None:
    """El turno degradado no llama al modelo, no persiste turnos y no se consolida."""
    turn.embedder.embed.assert_awaited_once()
    assert turn.llm.complete_calls == []
    # Se descarta el savepoint de las tools y se commitea solo la ChatSession ancla.
    turn.savepoint.rollback.assert_awaited_once()
    turn.savepoint.commit.assert_not_awaited()
    turn.turns_store.add.assert_not_called()
    turn.db.commit.assert_awaited_once()
    turn.consolidate_turn.delay.assert_not_called()


async def test_chat_degrades_when_the_embeddings_fail(turn: _Turn) -> None:
    """``POST /v1/chat``: 200 con el payload degradado, el mismo de un error del modelo."""
    resp = await _post("/v1/chat")

    assert resp.status_code == 200
    assert resp.json() == {
        "text": _FALLBACK_TEXT,
        "actions": [],
        "session_id": str(turn.session_id),
        "finish_reason": "degraded",
    }
    _assert_degraded_without_side_effects(turn)


async def test_chat_stream_degrades_when_the_embeddings_fail(turn: _Turn) -> None:
    """``POST /v1/chat/stream``: el turno degradado se streamea como cualquier otro."""
    resp = await _post("/v1/chat/stream")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _events(resp.text)
    names = [name for name, _ in events]
    assert "".join(data["delta"] for name, data in events if name == "token") == _FALLBACK_TEXT
    assert names[-1] == "done"
    assert names.count("done") == 1
    assert events[-1][1] == {
        "session_id": str(turn.session_id),
        "actions": [],
        "finish_reason": "degraded",
    }
    # Es una respuesta degradada válida, no un fallo: sin error ni razonamiento.
    assert "error" not in names
    assert "reasoning" not in names
    _assert_degraded_without_side_effects(turn)
