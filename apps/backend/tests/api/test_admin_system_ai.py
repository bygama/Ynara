"""Tests unitarios del bloque ``ai`` de ``GET /v1/admin/system`` (ADR-028).

Sin DB ni red. ``_probe_ai`` se ejercita con ``Settings`` armados acá (sin ``.env``) y
con los clientes reales de los proveedores sobre ``httpx.MockTransport``: el health
recorre el mismo camino que en producción sin salir de la máquina. El endpoint se
ejercita con la app real y overrides de sus dependencias (admin, sesión y clientes de
IA). Cubre:

1. Backend ``fake``: no se sondea a nadie y todo queda ``unconfigured``.
2. Backend ``api``: cada proveedor queda ``ok`` o ``down`` según su propio health, en
   el orden de ``LLM_PROVIDERS``, con su host y sus alias -> id de modelo.
3. Embeddings: modo, host, modelo, dimensión y estado.
4. Privacidad: ninguna credencial, URL completa ni path sale en la respuesta.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from app.api.v1.admin.metrics import _probe_ai
from app.core.config import Settings
from app.core.constants import EMBEDDING_DIM
from app.core.deps import get_current_admin, get_db, get_embedder, get_llm_client
from app.llm.clients.embedding import FakeEmbeddingClient
from app.llm.clients.fakes import FakeLlmClient
from app.llm.clients.pool import build_provider_pool
from app.llm.clients.providers.embeddings import ApiEmbeddingClient
from app.llm.clients.providers.openai_compat import OpenAICompatClient
from app.llm.clients.resilient import ResilientClient
from app.llm.schemas import ModelHealth
from app.main import app
from app.schemas.admin import AdminSystemOut

_PRINCIPAL_KEY = "sk-test-principal-0123"
_RESPALDO_KEY = "sk-test-respaldo-4567"
_EMBEDDING_KEY = "sk-test-embeddings-89ab"
_SECRETS = (_PRINCIPAL_KEY, _RESPALDO_KEY, _EMBEDDING_KEY)

_PROVIDERS = [
    {
        "name": "principal",
        "base_url": "https://api.principal.example.com/v1",
        "models": {"conversacional": "proveedor/modelo-chat", "agente": "proveedor/modelo-agente"},
    },
    {
        "name": "respaldo",
        "base_url": "https://api.respaldo.example.com:8443/v1",
        "models": {"agente": "proveedor/modelo-agente-b", "conversacional": "proveedor/modelo-b"},
    },
]

Handler = Callable[[httpx.Request], httpx.Response]


def _settings(*, llm_backend: str, embedding_backend: str, **extra: object) -> Settings:
    """``Settings`` de test sin ``.env``: DB/Redis/JWT dummy + los backends de IA."""
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        DATABASE_URL="postgresql://test:test@localhost/test",
        REDIS_URL="redis://localhost:6379/0",
        JWT_SECRET="test-secret-no-usar-en-prod-min-32b",
        LLM_BACKEND=llm_backend,
        EMBEDDING_BACKEND=embedding_backend,
        **extra,
    )


def _api_settings() -> Settings:
    """Los dos backends en ``api``: dos proveedores LLM y un proveedor de embeddings."""
    return _settings(
        llm_backend="api",
        embedding_backend="api",
        LLM_PROVIDERS=json.dumps(_PROVIDERS),
        llm_api_keys={"principal": _PRINCIPAL_KEY, "respaldo": _RESPALDO_KEY},
        EMBEDDING_BASE_URL="https://embeddings.example.com/v1",
        EMBEDDING_API_KEY=_EMBEDDING_KEY,
    )


def _fake_settings() -> Settings:
    """Los dos backends en ``fake``, con los proveedores igual cargados en la config."""
    return _settings(
        llm_backend="fake",
        embedding_backend="fake",
        LLM_PROVIDERS=json.dumps(_PROVIDERS),
        llm_api_keys={},
        EMBEDDING_BASE_URL="",
    )


def _answer(status_by_host: dict[str, int], seen: list[str]) -> Handler:
    """Responde el ``GET /models`` del health con el status de cada host."""

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.host}{request.url.path}")
        return httpx.Response(status_by_host[request.url.host], json={"data": []})

    return handler


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("sin red", request=request)


def _llm_client(settings: Settings, handler: Handler) -> ResilientClient:
    """El ``ResilientClient`` de los proveedores de ``settings``, como lo arma el factory."""
    clients = [
        OpenAICompatClient(
            name=provider.name,
            base_url=provider.base_url,
            api_key=settings.llm_api_keys[provider.name],
            model_map=provider.models,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        for provider in settings.llm_providers
    ]
    return ResilientClient(build_provider_pool(clients))


def _embedder(settings: Settings, handler: Handler) -> ApiEmbeddingClient:
    return ApiEmbeddingClient(
        base_url=settings.embedding_base_url,
        api_key=settings.embedding_api_key,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        model=settings.embedding_model,
    )


class _CountingLlm(FakeLlmClient):
    """Fake con salud por proveedor (como el ``ResilientClient``) que cuenta los sondeos."""

    def __init__(self) -> None:
        super().__init__(models=frozenset({"conversacional", "agente"}))
        self.probes = 0

    async def health(self) -> ModelHealth:
        self.probes += 1
        return await super().health()

    async def provider_health(self) -> list[ModelHealth]:
        self.probes += 1
        return [ModelHealth(model_name=str(p["name"]), healthy=True) for p in _PROVIDERS]


class _CountingEmbedder(FakeEmbeddingClient):
    """Fake de embeddings que cuenta los sondeos."""

    def __init__(self) -> None:
        super().__init__()
        self.probes = 0

    async def health(self) -> ModelHealth:
        self.probes += 1
        return await super().health()


def _assert_no_secrets(serialized: str) -> None:
    """Ni credenciales ni URLs completas: solo hosts."""
    for secret in _SECRETS:
        assert secret not in serialized
    assert "://" not in serialized
    assert "/v1" not in serialized


# ---------- backend fake ----------


async def test_fake_backends_are_unconfigured_and_never_probed() -> None:
    settings = _fake_settings()
    llm, embedder = _CountingLlm(), _CountingEmbedder()

    ai = await _probe_ai(settings, llm, embedder)

    assert (llm.probes, embedder.probes) == (0, 0)
    assert ai.llm.backend == "fake"
    assert [(p.name, p.status) for p in ai.llm.providers] == [
        ("principal", "unconfigured"),
        ("respaldo", "unconfigured"),
    ]
    assert ai.embeddings.backend == "fake"
    assert ai.embeddings.status == "unconfigured"
    assert ai.embeddings.host is None
    assert ai.embeddings.model == settings.embedding_model
    assert ai.embeddings.dim == EMBEDDING_DIM


async def test_fake_backend_without_providers_lists_none() -> None:
    settings = _settings(llm_backend="fake", embedding_backend="fake", LLM_PROVIDERS="")

    ai = await _probe_ai(settings, FakeLlmClient(models=frozenset()), FakeEmbeddingClient())

    assert ai.llm.providers == []


# ---------- backend api ----------


async def test_api_backend_reports_each_provider_by_its_own_health() -> None:
    settings = _api_settings()
    seen: list[str] = []
    llm = _llm_client(
        settings,
        _answer({"api.principal.example.com": 200, "api.respaldo.example.com": 503}, seen),
    )
    embedder = _embedder(settings, _answer({"embeddings.example.com": 200}, seen))
    try:
        ai = await _probe_ai(settings, llm, embedder)
    finally:
        await llm.aclose()
        await embedder.aclose()

    assert ai.llm.backend == "api"
    assert [(p.name, p.host, p.status) for p in ai.llm.providers] == [
        ("principal", "api.principal.example.com", "ok"),
        ("respaldo", "api.respaldo.example.com", "down"),
    ]
    assert sorted(seen) == [
        "GET api.principal.example.com/v1/models",
        "GET api.respaldo.example.com/v1/models",
        "GET embeddings.example.com/v1/models",
    ]
    assert ai.embeddings.backend == "api"
    assert ai.embeddings.host == "embeddings.example.com"
    assert ai.embeddings.status == "ok"


async def test_api_backend_lists_alias_to_model_id_sorted_by_alias() -> None:
    settings = _api_settings()
    llm = _llm_client(settings, _unreachable)
    embedder = _embedder(settings, _unreachable)
    try:
        ai = await _probe_ai(settings, llm, embedder)
    finally:
        await llm.aclose()
        await embedder.aclose()

    routes = {p.name: [(m.alias, m.model_id) for m in p.models] for p in ai.llm.providers}
    assert routes == {
        "principal": [
            ("agente", "proveedor/modelo-agente"),
            ("conversacional", "proveedor/modelo-chat"),
        ],
        "respaldo": [
            ("agente", "proveedor/modelo-agente-b"),
            ("conversacional", "proveedor/modelo-b"),
        ],
    }


async def test_api_backend_marks_unreachable_providers_as_down() -> None:
    settings = _api_settings()
    llm = _llm_client(settings, _unreachable)
    embedder = _embedder(settings, _unreachable)
    try:
        ai = await _probe_ai(settings, llm, embedder)
    finally:
        await llm.aclose()
        await embedder.aclose()

    assert [p.status for p in ai.llm.providers] == ["down", "down"]
    assert ai.embeddings.status == "down"


async def test_api_backend_uses_the_per_provider_health() -> None:
    settings = _api_settings()
    llm, embedder = _CountingLlm(), _CountingEmbedder()

    ai = await _probe_ai(settings, llm, embedder)

    assert (llm.probes, embedder.probes) == (1, 1)
    assert [p.status for p in ai.llm.providers] == ["ok", "ok"]
    assert ai.embeddings.status == "ok"


async def test_api_backend_without_per_provider_health_never_reports_ok() -> None:
    """Un cliente que no sabe la salud por proveedor no se toma como sano."""
    settings = _api_settings()
    embedder = FakeEmbeddingClient()
    embedder.set_health(False)

    ai = await _probe_ai(settings, FakeLlmClient(models=frozenset()), embedder)

    assert [p.status for p in ai.llm.providers] == ["down", "down"]
    assert ai.embeddings.status == "down"


async def test_api_backend_per_provider_health_that_raises_is_down() -> None:
    settings = _api_settings()

    class _Exploding(_CountingLlm):
        async def provider_health(self) -> list[ModelHealth]:
            raise RuntimeError("health roto")

    ai = await _probe_ai(settings, _Exploding(), FakeEmbeddingClient())

    assert [p.status for p in ai.llm.providers] == ["down", "down"]


async def test_api_backend_embedder_that_raises_is_down() -> None:
    settings = _api_settings()

    class _Exploding(FakeEmbeddingClient):
        async def health(self) -> ModelHealth:
            raise RuntimeError("health roto")

    ai = await _probe_ai(settings, FakeLlmClient(models=frozenset()), _Exploding())

    assert ai.embeddings.status == "down"


async def test_ai_block_never_exposes_credentials_or_urls() -> None:
    settings = _api_settings()
    llm = _llm_client(
        settings,
        _answer({"api.principal.example.com": 200, "api.respaldo.example.com": 200}, []),
    )
    embedder = _embedder(settings, _answer({"embeddings.example.com": 200}, []))
    try:
        ai = await _probe_ai(settings, llm, embedder)
    finally:
        await llm.aclose()
        await embedder.aclose()

    _assert_no_secrets(ai.model_dump_json())


# ---------- endpoint ----------


async def _override_db() -> AsyncIterator[object]:
    """Sesión de mentira: el probe de Postgres la marca caída y sigue."""
    yield object()


@pytest.fixture
def _system_overrides() -> Iterator[None]:
    app.dependency_overrides[get_current_admin] = lambda: uuid.uuid4()
    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_llm_client] = _CountingLlm
    app.dependency_overrides[get_embedder] = _CountingEmbedder
    try:
        yield None
    finally:
        app.dependency_overrides.clear()


@pytest.mark.usefixtures("_system_overrides")
async def test_system_endpoint_returns_ai_block_from_the_app_clients() -> None:
    """El endpoint sondea los clientes de ``app.state`` (acá, los overrides)."""
    transport = ASGITransport(app=app)
    with (
        patch("app.api.v1.admin.metrics.get_settings", return_value=_api_settings()),
        patch("app.api.v1.admin.metrics._schema_head", return_value="20260101_0000"),
    ):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/admin/system")

    assert resp.status_code == 200
    ai = AdminSystemOut.model_validate_json(resp.content).ai
    assert ai.llm.backend == "api"
    assert [(p.name, p.host, p.status) for p in ai.llm.providers] == [
        ("principal", "api.principal.example.com", "ok"),
        ("respaldo", "api.respaldo.example.com", "ok"),
    ]
    assert (ai.embeddings.host, ai.embeddings.dim, ai.embeddings.status) == (
        "embeddings.example.com",
        EMBEDDING_DIM,
        "ok",
    )
    _assert_no_secrets(resp.text)
