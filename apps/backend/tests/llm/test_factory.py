"""Tests de la factory de clientes LLM / embedder / reranker.

Verifican el gate entre el fake (default de dev y test) y los clientes reales
(``ResilientClient`` sobre un ``OpenAICompatClient`` por proveedor con
``LLM_BACKEND=api``) SIN abrir red: ``httpx.AsyncClient`` no disca hasta el primer
request. Tambien cubren el presupuesto de resiliencia y los gates del embedder
(``embedding_backend``) y del reranker.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator

import pytest

from app.core.config import Settings
from app.llm.clients.embedding import FakeEmbeddingClient, VllmEmbeddingClient
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
from app.llm.clients.providers.openai_compat import OpenAICompatClient
from app.llm.clients.reranker import FakeReranker, VllmReranker
from app.llm.clients.resilient import ResilientClient
from app.llm.config import (
    LlmRuntimeConfig,
    ModeConfig,
    ModelConfig,
    ServingConfig,
)

_QWEN = "qwen-3.5-9b"
_GEMMA = "gemma-4-12b"

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

# JWT secret >= 32 chars: production rechaza un secret debil (ver Settings).
_PROD_JWT_SECRET = "x" * 48
_PROD_MASTER_KEY = "dGVzdC1tYXN0ZXIta2V5LTMyLWJ5dGVzLWxvbmctYWE="  # base64 placeholder


def _settings(
    *,
    environment: str = "development",
    embedding_backend: str = "fake",
    reranker_backend: str = "fake",
    llm_backend: str = "fake",
) -> Settings:
    """Settings aislado de cualquier .env. ``production`` exige config no-dev.

    Con ``llm_backend='api'`` se cargan los dos proveedores de prueba y sus
    credenciales (por nombre de campo: ``llm_api_keys``).
    """
    kwargs: dict[str, object] = {
        "_env_file": None,
        "DATABASE_URL": "postgresql://test:test@localhost/test",
        "REDIS_URL": "redis://localhost:6379/0",
        # ``environment`` no tiene alias: se setea por nombre de campo, no por env var.
        "environment": environment,
        "EMBEDDING_BACKEND": embedding_backend,
        "RERANKER_BACKEND": reranker_backend,
        "LLM_BACKEND": llm_backend,
    }
    if llm_backend == "api":
        kwargs["LLM_PROVIDERS"] = json.dumps(_PROVIDERS)
        kwargs["llm_api_keys"] = dict(_API_KEYS)
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
    """``LlmRuntimeConfig`` minimo coherente (gemma + qwen)."""
    serving = ServingConfig(
        tool_parsers={_QWEN: "hermes", _GEMMA: "gemma4"},
        quantization="awq_marlin",
        kv_cache_dtype="fp8",
        max_model_len={_QWEN: 32768, _GEMMA: 8192},
        request_timeout_s=120,
    )
    models = {
        _QWEN: ModelConfig(
            key=_QWEN,
            role="agent",
            writes_memory=True,
            served_name="qwen",
            context_window=262144,
        ),
        _GEMMA: ModelConfig(
            key=_GEMMA,
            role="conversational",
            writes_memory=False,
            served_name="gemma4",
            context_window=128000,
        ),
    }
    modes = {
        "productividad": ModeConfig(
            name="productividad",
            model=_QWEN,
            memory_layers=["semantic"],
            tools_enabled=["memory"],
            tone="neutro",
        )
    }
    return LlmRuntimeConfig(serving_endpoints=[], serving=serving, models=models, modes=modes)


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
    # El fake sirve todos los modelos de la config.
    assert client.serves_model("qwen")
    assert client.serves_model("gemma4")


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


def test_llm_client_is_resilient_in_production() -> None:
    """En production (``LLM_BACKEND=api`` obligatorio) el cliente es el real."""
    client = build_llm_client(_settings(environment="production", llm_backend="api"), _config())
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
    embedder = build_embedder(_settings(embedding_backend="fake"))
    assert isinstance(embedder, FakeEmbeddingClient)


def test_embedder_is_vllm_when_backend_vllm() -> None:
    """``embedding_backend='vllm'`` construye el cliente real (sin abrir red)."""
    embedder = build_embedder(_settings(embedding_backend="vllm"))
    assert isinstance(embedder, VllmEmbeddingClient)


def test_embedder_vllm_takes_timeout_from_settings() -> None:
    """El timeout del embedder sale de Settings (review PR #198, MEDIUM #1)."""
    settings = _settings(embedding_backend="vllm").model_copy(update={"embedding_timeout_s": 12.5})
    embedder = build_embedder(settings)
    assert isinstance(embedder, VllmEmbeddingClient)
    assert embedder._default_timeout_s == 12.5


# ---------- build_reranker ----------


def test_reranker_is_fake_by_default() -> None:
    reranker = build_reranker(_settings())
    assert isinstance(reranker, FakeReranker)


def test_reranker_is_vllm_when_backend_vllm() -> None:
    """``reranker_backend='vllm'`` construye el VllmReranker real (sin abrir red)."""
    reranker = build_reranker(_settings(reranker_backend="vllm"))
    assert isinstance(reranker, VllmReranker)


def test_reranker_vllm_takes_timeout_from_settings() -> None:
    """El timeout del reranker sale de Settings (review PR #198, MEDIUM #1)."""
    settings = _settings(reranker_backend="vllm").model_copy(update={"reranker_timeout_s": 12.5})
    reranker = build_reranker(settings)
    assert isinstance(reranker, VllmReranker)
    assert reranker._default_timeout_s == 12.5


# ---------- build_llm_clients (trio) ----------


def test_build_llm_clients_returns_trio_of_fakes_in_dev() -> None:
    llm, embedder, reranker = build_llm_clients(_settings(), _config())
    assert isinstance(llm, FakeLlmClient)
    assert isinstance(embedder, FakeEmbeddingClient)
    assert isinstance(reranker, FakeReranker)


def test_build_llm_clients_returns_real_llm_in_production() -> None:
    settings = _settings(environment="production", llm_backend="api")
    llm, embedder, reranker = build_llm_clients(settings, _config())
    assert isinstance(llm, ResilientClient)
    # embedder/reranker reales aun no existen: siguen siendo Fakes detras del gate.
    assert isinstance(embedder, FakeEmbeddingClient)
    assert isinstance(reranker, FakeReranker)
