"""Tests de la factory de clientes LLM / embedder / reranker.

Verifican el gate entre el fake (default de dev y test) y los clientes reales
(``ResilientClient`` sobre un ``OpenAICompatClient`` por proveedor con
``LLM_BACKEND=api``) SIN abrir red: ``httpx.AsyncClient`` no disca hasta el primer
request. Tambien cubren el presupuesto de resiliencia, el gate del embedder
(``embedding_backend``) y el reranker passthrough.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import httpx
import pytest

from app.core.config import Settings
from app.core.constants import DEFAULT_EMBEDDING_BATCH_SIZE
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.factory import (
    PROVIDER_MAX_ATTEMPTS,
    PROVIDER_REQUEST_TIMEOUT_S,
    PROVIDER_TOTAL_BUDGET_S,
    build_embedder,
    build_llm_client,
    build_llm_clients,
    build_reranker,
    check_resilience_budget,
)
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.providers.embeddings import ApiEmbeddingClient
from app.llm.clients.providers.openai_compat import OpenAICompatClient
from app.llm.clients.reranker import RERANKER_PASSTHROUGH, FakeReranker
from app.llm.clients.resilient import ResilientClient
from app.llm.config import LlmRuntimeConfig, ModeConfig, ModelConfig
from app.llm.errors import LlmAuthError
from app.llm.schemas import ChatMessage

# Proveedores de prueba (ADR-028): el principal acepta reasoning_effort, el respaldo no.
_PROVIDERS = [
    {
        "name": "principal",
        "base_url": "https://api.principal.test/v1",
        "models": {"conversacional": "org/chat", "agente": "org/agent"},
        "supports_reasoning_effort": True,
    },
    {
        "name": "respaldo",
        "base_url": "https://api.respaldo.test/v1",
        "models": {"conversacional": "otro/chat", "agente": "otro/agent"},
    },
]
_API_KEYS = {"principal": "sk-test-principal-0123", "respaldo": "sk-test-respaldo-4567"}
# Proveedor de embeddings de prueba (ADR-028 D4).
_EMBEDDING_BASE_URL = "https://api.embeddings.test/v1"
_EMBEDDING_KEY = "sk-test-embeddings-8899"

# JWT secret >= 32 chars: production rechaza un secret debil (ver Settings).
_PROD_JWT_SECRET = "x" * 48
_PROD_MASTER_KEY = "dGVzdC1tYXN0ZXIta2V5LTMyLWJ5dGVzLWxvbmctYWE="  # base64 placeholder


def _settings(
    *,
    environment: str = "development",
    embedding_backend: str = "fake",
    llm_backend: str = "fake",
) -> Settings:
    """Settings aislado de cualquier .env. ``production`` exige config no-dev.

    Con ``llm_backend='api'`` se cargan los dos proveedores de prueba y sus
    credenciales (por nombre de campo: ``llm_api_keys``); con ``embedding_backend='api'``,
    el proveedor de embeddings de prueba y su credencial.
    """
    kwargs: dict[str, object] = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test:test@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        # ``environment`` no tiene alias: se setea por nombre de campo, no por env var.
        "environment": environment,
        "EMBEDDING_BACKEND": embedding_backend,
        "LLM_BACKEND": llm_backend,
    }
    if llm_backend == "api":
        kwargs["LLM_PROVIDERS"] = json.dumps(_PROVIDERS)
        kwargs["llm_api_keys"] = dict(_API_KEYS)
    if embedding_backend == "api":
        kwargs["EMBEDDING_BASE_URL"] = _EMBEDDING_BASE_URL
        kwargs["EMBEDDING_API_KEY"] = _EMBEDDING_KEY
    if environment == "production":
        # production fail-fast: secret fuerte, CORS no-localhost, master key.
        # ``cors_origins`` no tiene alias: se setea por nombre de campo.
        kwargs["JWT_SECRET"] = _PROD_JWT_SECRET
        kwargs["MEMORY_ENCRYPTION_MASTER_KEY"] = _PROD_MASTER_KEY
        kwargs["cors_origins"] = ["https://app.ynara.test"]
    else:
        kwargs["JWT_SECRET"] = "test-secret"
    return Settings(**kwargs)  # type: ignore[arg-type]


def _config() -> LlmRuntimeConfig:
    """``LlmRuntimeConfig`` minimo coherente (los dos alias de modelo)."""
    models = {
        "conversacional": ModelConfig(
            alias="conversacional",
            role="conversational",
            writes_memory=False,
            context_tokens=8192,
        ),
        "agente": ModelConfig(
            alias="agente",
            role="agent",
            writes_memory=True,
            context_tokens=32768,
        ),
    }
    modes = {
        "productividad": ModeConfig(
            name="productividad",
            model="agente",
            memory_layers=["semantic"],
            tools_enabled=["memory"],
            tone="neutro",
        )
    }
    return LlmRuntimeConfig(models=models, modes=modes)


def _provider_clients(client: object) -> list[OpenAICompatClient]:
    assert isinstance(client, ResilientClient)
    clients = client._pool.clients
    assert all(isinstance(c, OpenAICompatClient) for c in clients)
    return clients  # type: ignore[return-value]


# ---------- build_llm_client ----------


@pytest.fixture(autouse=True)
def _restore_httpx_log_level() -> Iterator[None]:
    """``build_llm_client`` con ``api`` sube el logger de httpx: se restaura al salir."""
    logger = logging.getLogger("httpx")
    level = logger.level
    yield
    logger.setLevel(level)


@pytest.mark.parametrize("environment", ["development", "staging"])
def test_llm_client_is_fake_by_default(environment: str) -> None:
    """Sin ``LLM_BACKEND`` la factory devuelve el ``FakeLlmClient`` (sin red)."""
    client = build_llm_client(_settings(environment=environment), _config())
    assert isinstance(client, FakeLlmClient)
    # El fake sirve los alias de la config, no ids de modelo.
    assert client.serves_model("conversacional")
    assert client.serves_model("agente")
    assert not client.serves_model("qwen")


def test_llm_client_api_builds_one_client_per_provider_in_priority_order() -> None:
    """``LLM_BACKEND=api``: un cliente por proveedor, el principal primero."""
    clients = _provider_clients(build_llm_client(_settings(llm_backend="api"), _config()))
    assert [c.name for c in clients] == ["principal", "respaldo"]
    for provider_client in clients:
        assert provider_client.serves_model("conversacional")
        assert provider_client.serves_model("agente")
        assert provider_client._default_timeout_s == PROVIDER_REQUEST_TIMEOUT_S


def test_llm_client_api_maps_reasoning_effort_per_provider() -> None:
    """``supports_reasoning_effort`` pide ``medium`` al pensar; apagado no se manda."""
    principal, respaldo = _provider_clients(
        build_llm_client(_settings(llm_backend="api"), _config())
    )
    assert principal._reasoning_effort == {True: "medium", False: None}
    assert respaldo._reasoning_effort == {True: None, False: None}


def test_llm_client_api_takes_each_credential_by_provider_name() -> None:
    principal, respaldo = _provider_clients(
        build_llm_client(_settings(llm_backend="api"), _config())
    )
    assert principal._auth._api_key.get_secret_value() == _API_KEYS["principal"]
    assert respaldo._auth._api_key.get_secret_value() == _API_KEYS["respaldo"]


def test_llm_client_api_quiets_the_httpx_request_logs() -> None:
    """httpx loguea cada request en INFO con la URL del proveedor: queda en WARNING."""
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    build_llm_client(_settings(llm_backend="api"), _config())
    assert logging.getLogger("httpx").level == logging.WARNING


def test_llm_client_fake_leaves_the_httpx_logs_alone() -> None:
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    build_llm_client(_settings(), _config())
    assert logging.getLogger("httpx").level == logging.NOTSET


def test_llm_client_api_uses_the_resilience_policy() -> None:
    client = build_llm_client(_settings(llm_backend="api"), _config())
    assert isinstance(client, ResilientClient)
    assert client._policy.max_attempts == PROVIDER_MAX_ATTEMPTS
    assert client._total_budget_s == PROVIDER_TOTAL_BUDGET_S


def _mock_provider_http(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    """Los ``httpx.AsyncClient`` que arma la factory responden ``status`` sin abrir red."""
    real_async_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": {"message": "rechazado"}})

    monkeypatch.setattr(
        "app.llm.clients.factory.httpx.AsyncClient",
        lambda: real_async_client(transport=httpx.MockTransport(handler)),
    )


async def test_llm_client_api_degrades_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """El chat usa el default: si todos los proveedores rechazan, respuesta degradada."""
    _mock_provider_http(monkeypatch, 401)
    client = build_llm_client(_settings(llm_backend="api"), _config())
    try:
        result = await client.complete(
            model="agente", messages=[ChatMessage(role="user", content="hola")]
        )
    finally:
        await client.aclose()
    assert result.finish_reason == "degraded"


async def test_llm_client_api_without_degrade_raises_the_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Los workers piden ``degrade=False``: el rechazo de la credencial se levanta."""
    _mock_provider_http(monkeypatch, 401)
    client = build_llm_client(_settings(llm_backend="api"), _config(), degrade=False)
    try:
        with pytest.raises(LlmAuthError):
            await client.complete(
                model="agente", messages=[ChatMessage(role="user", content="hola")]
            )
    finally:
        await client.aclose()


def test_llm_client_fake_ignores_degrade() -> None:
    """El fake no tiene cadena que agotar: ``degrade`` no cambia el cliente."""
    client = build_llm_client(_settings(), _config(), degrade=False)
    assert isinstance(client, FakeLlmClient)


def test_llm_client_is_resilient_in_production() -> None:
    """En production (``LLM_BACKEND=api`` obligatorio) el cliente es el real."""
    settings = _settings(environment="production", llm_backend="api", embedding_backend="api")
    client = build_llm_client(settings, _config())
    assert [c.name for c in _provider_clients(client)] == ["principal", "respaldo"]


# ---------- presupuesto de resiliencia ----------


def test_resilience_budget_leaves_room_for_the_backup() -> None:
    """Con la politica por default, el respaldo entra despues del peor caso."""
    check_resilience_budget()


@pytest.mark.parametrize(
    ("timeout_s", "max_attempts"),
    [(30.0, 3), (22.0, 3), (45.0, 1)],
)
def test_resilience_budget_rejects_a_timeout_that_starves_the_backup(
    timeout_s: float, max_attempts: int
) -> None:
    with pytest.raises(ValueError, match="no deja lugar al respaldo"):
        check_resilience_budget(timeout_s=timeout_s, max_attempts=max_attempts)


# ---------- build_embedder ----------


def test_embedder_is_fake_by_default() -> None:
    embedder = build_embedder(_settings())
    assert isinstance(embedder, FakeEmbeddingClient)


async def test_embedder_fake_does_not_keep_the_texts() -> None:
    """Es un singleton de la app: no acumula los textos que embebe (texto de usuario)."""
    embedder = build_embedder(_settings())
    assert isinstance(embedder, FakeEmbeddingClient)
    await embedder.embed(["el martes tengo turno con la psicóloga"])
    assert embedder.embed_calls == []


def test_embedder_api_builds_the_client_from_settings() -> None:
    """``EMBEDDING_BACKEND=api``: ``ApiEmbeddingClient`` con la config de Settings, sin red."""
    settings = _settings(embedding_backend="api").model_copy(
        update={
            "embedding_model": "org/bge-m3",
            "embedding_batch_size": 8,
            "embedding_timeout_s": 12.5,
        }
    )
    embedder = build_embedder(settings)
    assert isinstance(embedder, ApiEmbeddingClient)
    assert embedder.model == "org/bge-m3"
    assert embedder._base_url == _EMBEDDING_BASE_URL
    assert embedder._auth._api_key.get_secret_value() == _EMBEDDING_KEY
    assert embedder._batch_size == 8
    assert embedder._default_timeout_s == 12.5


def test_embedder_api_defaults_to_bge_m3() -> None:
    embedder = build_embedder(_settings(embedding_backend="api"))
    assert isinstance(embedder, ApiEmbeddingClient)
    assert embedder.model == "BAAI/bge-m3"
    assert embedder._batch_size == DEFAULT_EMBEDDING_BATCH_SIZE


def test_embedder_api_quiets_the_httpx_request_logs() -> None:
    """Con el LLM fake, el embedder por API igual deja httpx en WARNING (la URL)."""
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    build_embedder(_settings(embedding_backend="api"))
    assert logging.getLogger("httpx").level == logging.WARNING


def test_embedder_fake_leaves_the_httpx_logs_alone() -> None:
    logging.getLogger("httpx").setLevel(logging.NOTSET)
    build_embedder(_settings())
    assert logging.getLogger("httpx").level == logging.NOTSET


# ---------- build_reranker ----------


async def test_reranker_is_the_passthrough() -> None:
    """El reranker es el ``FakeReranker`` passthrough y se reporta como tal (ADR-028 D5)."""
    reranker = build_reranker()
    assert isinstance(reranker, FakeReranker)
    health = await reranker.health()
    assert health.model_name == RERANKER_PASSTHROUGH == "passthrough"
    assert health.healthy is True


async def test_reranker_passthrough_keeps_the_ann_order() -> None:
    results = await build_reranker().rerank("consulta", ["a", "b", "c"], top_n=2)
    assert [result.index for result in results] == [0, 1]


async def test_reranker_passthrough_does_not_keep_the_calls() -> None:
    """Es un singleton de la app: no acumula la consulta ni los documentos."""
    reranker = build_reranker()
    assert isinstance(reranker, FakeReranker)
    await reranker.rerank("consulta", ["un documento"])
    assert reranker.rerank_calls == []


@pytest.mark.parametrize("environment", ["development", "production"])
def test_reranker_is_the_passthrough_in_every_environment(environment: str) -> None:
    """Production tambien usa el passthrough: no hay reranker real que prender."""
    backend = "api" if environment == "production" else "fake"
    settings = _settings(environment=environment, llm_backend=backend, embedding_backend=backend)
    _, _, reranker = build_llm_clients(settings, _config())
    assert isinstance(reranker, FakeReranker)


# ---------- build_llm_clients (trio) ----------


def test_build_llm_clients_returns_trio_of_fakes_in_dev() -> None:
    llm, embedder, reranker = build_llm_clients(_settings(), _config())
    assert isinstance(llm, FakeLlmClient)
    assert isinstance(embedder, FakeEmbeddingClient)
    assert isinstance(reranker, FakeReranker)


def test_build_llm_clients_returns_real_clients_in_production() -> None:
    """En production el LLM y el embedder son los de los proveedores por API."""
    settings = _settings(environment="production", llm_backend="api", embedding_backend="api")
    llm, embedder, reranker = build_llm_clients(settings, _config())
    assert isinstance(llm, ResilientClient)
    assert isinstance(embedder, ApiEmbeddingClient)
    # El reranker es el passthrough en todos los entornos (ADR-028 D5).
    assert isinstance(reranker, FakeReranker)
