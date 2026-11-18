"""Reintentos de las tasks de consolidacion ante fallas del proveedor (UNIT).

Corren la task como la corre el worker, pero en el acto: ``.apply()`` ejecuta los
reintentos de ``autoretry_for`` sin broker ni countdown. Sin DB: ``worker_session``
entrega una sesion mock y la marca del turno y los stores estan parcheados. Los casos
con proveedor pasan por la factory real (``LLM_BACKEND=api``) con un transporte HTTP
programado, sin red.

- Un 429 sostenido agota la cadena del cliente del worker: la task se reintenta y el
  recuerdo se guarda UNA vez, en el intento que anda.
- Un 401 es permanente: un solo intento, sin reintento y sin escribir.
- Una respuesta degradada es transitoria: se reintenta.
- Un intento que vence el soft limit (un proveedor lento) se reintenta igual, también
  si vence adentro del motor de memoria (extracción, op o resumen): su catch-all no
  lo traga.
- Agotados los reintentos, la task termina sin propagar y loguea sin contenido.
- Cada intento cierra el cliente HTTP que armo.
- El chat no cambia: el mismo 429 le llega como respuesta degradada.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import httpx
import pytest
from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.factory import build_llm_clients
from app.llm.clients.fakes import FakeLlmClient
from app.llm.config import load_llm_config
from app.llm.errors import LlmRateLimitError, LlmTransientError, degraded_response
from app.llm.schemas import ChatMessage, CompletionResult
from app.workflows import consolidation
from app.workflows._turn_marker import TurnClaim
from app.workflows.consolidation import consolidate_session, consolidate_turn

USER_ID = str(uuid4())
SESSION_ID = str(uuid4())
_SECRET = "mi diagnostico es privado"
_OPS = json.dumps([{"op": "ADD", "layer": "semantic", "content": "El usuario vive en Rosario."}])
_TURN = {
    "user_id": USER_ID,
    "session_id": SESSION_ID,
    "user_msg": _SECRET,
    "model_response": "anotado",
    "mode": "vida",
    "turn_seq": 0,
}

# Un solo proveedor, sin respaldo: cada request del cliente va al mismo lugar.
_PROVIDER = {
    "name": "principal",
    "base_url": "https://api.principal.test/v1",
    "models": {"conversacional": "org/chat", "agente": "org/agent"},
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


class _ScriptedProvider:
    """Transporte HTTP del proveedor: contesta, en orden, las respuestas programadas."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self._responses:
            return httpx.Response(500, json={"error": {"message": "request de mas"}})
        return self._responses.pop(0)


def _rate_limited() -> httpx.Response:
    return httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"message": "429"}})


def _completion(text: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "org/agent",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        },
    )


def _result(text: str) -> CompletionResult:
    return CompletionResult(
        text=text,
        finish_reason="stop",
        prompt_tokens=10,
        completion_tokens=20,
        model_name="agente",
        latency_ms=1.0,
    )


@asynccontextmanager
async def _mock_worker_session(_settings: Settings) -> AsyncIterator[AsyncSession]:
    yield MagicMock(spec=AsyncSession)


@pytest.fixture
def semantic_add() -> Iterator[AsyncMock]:
    """El worker sin DB: devuelve el ``add`` del store semantico para contar escrituras."""
    semantic = AsyncMock()
    semantic.add = AsyncMock(return_value=MagicMock(id=uuid4()))
    with (
        patch.object(consolidation, "worker_session", _mock_worker_session),
        patch.object(consolidation, "claim_turn", AsyncMock(return_value=TurnClaim.CLAIMED)),
        patch.object(consolidation, "SemanticMemoryStore", return_value=semantic),
        patch.object(consolidation, "ProceduralMemoryStore", return_value=AsyncMock()),
        patch.object(consolidation, "AuditStore", return_value=AsyncMock()),
        patch.object(consolidation, "_build_embedder", return_value=FakeEmbeddingClient()),
    ):
        yield semantic.add


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> Callable[..., _ScriptedProvider]:
    """Programa las respuestas del proveedor y pone al worker en ``LLM_BACKEND=api``."""

    def _program(*responses: httpx.Response) -> _ScriptedProvider:
        scripted = _ScriptedProvider(*responses)
        real_async_client = httpx.AsyncClient

        def _client() -> httpx.AsyncClient:
            client = real_async_client(transport=httpx.MockTransport(scripted))
            scripted.clients.append(client)
            return client

        monkeypatch.setattr("app.llm.clients.factory.httpx.AsyncClient", _client)
        monkeypatch.setattr(consolidation, "get_settings", _api_settings)
        return scripted

    return _program


# ---------------------------------------------------------------------------
# consolidate_turn
# ---------------------------------------------------------------------------


def test_a_429_is_retried_and_the_memory_is_saved_once(
    semantic_add: AsyncMock,
    provider: Callable[..., _ScriptedProvider],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """429 en todo el primer intento: la task se reintenta y guarda el hecho UNA vez."""
    scripted = provider(_rate_limited(), _rate_limited(), _rate_limited(), _completion(_OPS))

    with caplog.at_level(logging.INFO):
        result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    # Primer intento: los 3 requests del cliente, todos 429. Segundo intento: el que anda.
    assert len(scripted.requests) == 4
    semantic_add.assert_awaited_once()
    assert any("intento=1: LlmTransientError, se reintenta" in m for m in caplog.messages)
    assert any("applied=1" in m for m in caplog.messages)
    assert _SECRET not in caplog.text


def test_a_401_is_not_retried(
    semantic_add: AsyncMock,
    provider: Callable[..., _ScriptedProvider],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Credencial rechazada: permanente. Un solo request, sin reintento y sin escribir."""
    scripted = provider(httpx.Response(401, json={"error": {"message": "rechazado"}}))

    with caplog.at_level(logging.INFO):
        result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    assert len(scripted.requests) == 1
    semantic_add.assert_not_awaited()
    assert "memory_engine: extraccion descartada (LlmAuthError)" in caplog.messages
    assert not any("se reintenta" in m for m in caplog.messages)
    assert _SECRET not in caplog.text


def test_each_attempt_closes_its_http_client(
    semantic_add: AsyncMock, provider: Callable[..., _ScriptedProvider]
) -> None:
    """El intento que falla y el que anda cierran el cliente HTTP que armaron."""
    scripted = provider(_rate_limited(), _rate_limited(), _rate_limited(), _completion(_OPS))

    consolidate_turn.apply(kwargs=_TURN)

    assert len(scripted.clients) == 2  # uno por intento (un solo proveedor)
    assert all(client.is_closed for client in scripted.clients)


def test_a_degraded_response_is_retried(semantic_add: AsyncMock) -> None:
    """Una respuesta degradada no es "nada que guardar": se reintenta y guarda una vez."""
    fake = FakeLlmClient(models=frozenset({"agente"}))
    fake.queue_result(degraded_response(text="probá en un ratito", model_name="agente"))
    fake.queue_result(_result(_OPS))

    with patch.object(consolidation, "_build_consolidation_llm", return_value=fake):
        result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    assert len(fake.complete_calls) == 2
    semantic_add.assert_awaited_once()


def test_an_attempt_past_the_soft_time_limit_is_retried(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Un proveedor lento que hace vencer el soft limit no pierde el turno: se reintenta."""
    body = AsyncMock(side_effect=[SoftTimeLimitExceeded(), 1])

    with (
        patch.object(consolidation, "_async_consolidate", body),
        caplog.at_level(logging.INFO),
    ):
        result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    assert body.await_count == 2
    assert any("intento=1: SoftTimeLimitExceeded, se reintenta" in m for m in caplog.messages)
    assert any("applied=1" in m for m in caplog.messages)


def test_the_soft_limit_during_the_extraction_is_retried(
    semantic_add: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """El soft limit vence mientras el modelo extrae: el motor no lo traga como un error
    permanente (un turno sin ops), la task se reintenta y guarda el hecho una vez."""
    fake = FakeLlmClient(models=frozenset({"agente"}))
    fake.queue_error(SoftTimeLimitExceeded())
    fake.queue_result(_result(_OPS))

    with (
        patch.object(consolidation, "_build_consolidation_llm", return_value=fake),
        caplog.at_level(logging.INFO),
    ):
        result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    assert len(fake.complete_calls) == 2
    semantic_add.assert_awaited_once()
    assert any("intento=1: SoftTimeLimitExceeded, se reintenta" in m for m in caplog.messages)
    assert not any("extraccion descartada" in m for m in caplog.messages)


def test_the_soft_limit_during_an_op_is_retried(
    semantic_add: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """El soft limit vence mientras se aplica una op (embed + INSERT): la op no se saltea,
    la task se reintenta y el hecho se guarda en el segundo intento."""
    semantic_add.side_effect = [SoftTimeLimitExceeded(), MagicMock(id=uuid4())]
    fake = FakeLlmClient(models=frozenset({"agente"}))
    fake.queue_result(_result(_OPS))
    fake.queue_result(_result(_OPS))

    with (
        patch.object(consolidation, "_build_consolidation_llm", return_value=fake),
        caplog.at_level(logging.INFO),
    ):
        result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    assert semantic_add.await_count == 2
    assert any("intento=1: SoftTimeLimitExceeded, se reintenta" in m for m in caplog.messages)
    assert any("applied=1" in m for m in caplog.messages)
    assert not any("revertida (savepoint), skip" in m for m in caplog.messages)


def test_gives_up_after_the_last_retry_without_propagating(
    semantic_add: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """Agotados los reintentos, la task termina (sin FAILURE ni kwargs en el log)."""
    attempts = consolidate_turn.max_retries + 1
    fake = FakeLlmClient(models=frozenset({"agente"}))
    for _ in range(attempts):
        fake.queue_error(LlmRateLimitError("HTTP 429"))

    with (
        patch.object(consolidation, "_build_consolidation_llm", return_value=fake),
        caplog.at_level(logging.INFO),
    ):
        result = consolidate_turn.apply(kwargs=_TURN)

    assert result.state == "SUCCESS"
    assert len(fake.complete_calls) == attempts
    semantic_add.assert_not_awaited()
    assert any(
        f"reintentos agotados user={USER_ID} session={SESSION_ID} intentos={attempts}" in m
        for m in caplog.messages
    )
    assert _SECRET not in caplog.text


def test_the_worker_builds_the_llm_client_without_degrading() -> None:
    with patch.object(consolidation, "build_llm_client") as build:
        consolidation._build_consolidation_llm(_api_settings())

    assert build.call_args.kwargs == {"degrade": False}


# ---------------------------------------------------------------------------
# consolidate_session
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [LlmTransientError("HTTP 503"), SoftTimeLimitExceeded()],
    ids=["transient-error", "soft-time-limit"],
)
def test_consolidate_session_is_retried(failure: Exception) -> None:
    """El cierre de sesion se reintenta como el turno: falla transitoria o soft limit."""
    body = AsyncMock(side_effect=[failure, 1])

    with patch.object(consolidation, "_async_consolidate_session", body):
        result = consolidate_session.apply(
            kwargs={"user_id": USER_ID, "session_id": SESSION_ID, "mode": "vida"}
        )

    assert result.state == "SUCCESS"
    assert body.await_count == 2


def test_the_soft_limit_during_the_summary_is_retried(caplog: pytest.LogCaptureFixture) -> None:
    """El soft limit vence mientras el modelo resume la sesion: no queda como un resumen
    vacio (el episodio perdido), la task se reintenta y crea el episodio una vez."""
    fake = FakeLlmClient(models=frozenset({"agente"}))
    fake.queue_error(SoftTimeLimitExceeded())
    fake.queue_result(_result(json.dumps({"summary": "Charlamos de la mudanza.", "topics": {}})))
    turns = MagicMock()
    turns.list_for_session = AsyncMock(return_value=[SimpleNamespace(role="user", content=_SECRET)])
    turns.purge_session = AsyncMock(return_value=1)
    episodic = MagicMock()
    episodic.add = AsyncMock(return_value=MagicMock(id=uuid4()))

    with (
        patch.object(consolidation, "worker_session", _mock_worker_session),
        patch.object(consolidation, "_episode_exists", AsyncMock(return_value=False)),
        patch.object(consolidation, "ConversationTurnStore", return_value=turns),
        patch.object(consolidation, "EpisodicMemoryStore", return_value=episodic),
        patch.object(consolidation, "AuditStore", return_value=AsyncMock()),
        patch.object(consolidation, "_build_embedder", return_value=FakeEmbeddingClient()),
        patch.object(consolidation, "_build_consolidation_llm", return_value=fake),
        caplog.at_level(logging.INFO),
    ):
        result = consolidate_session.apply(
            kwargs={"user_id": USER_ID, "session_id": SESSION_ID, "mode": "vida"}
        )

    assert result.state == "SUCCESS"
    assert len(fake.complete_calls) == 2
    episodic.add.assert_awaited_once()
    assert any("intento=1: SoftTimeLimitExceeded, se reintenta" in m for m in caplog.messages)
    assert not any("resumen descartado" in m for m in caplog.messages)
    assert _SECRET not in caplog.text


# ---------------------------------------------------------------------------
# El chat interactivo no cambia
# ---------------------------------------------------------------------------


async def test_the_chat_still_gets_a_degraded_response_on_a_429(
    provider: Callable[..., _ScriptedProvider],
) -> None:
    """El cliente del lifespan (el del chat) sigue degradando ante el mismo 429."""
    scripted = provider(_rate_limited(), _rate_limited(), _rate_limited())
    llm, _embedder, _reranker = build_llm_clients(_api_settings(), load_llm_config())

    try:
        result = await llm.complete(
            model="agente", messages=[ChatMessage(role="user", content="hola")]
        )
    finally:
        await llm.aclose()

    assert result.finish_reason == "degraded"
    assert len(scripted.requests) == 3
