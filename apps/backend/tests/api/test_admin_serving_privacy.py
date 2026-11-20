"""Tests unitarios de privacidad y alias en ``/v1/admin/serving`` y ``/v1/admin/playground``.

Cubren las decisiones #46 y #106/#28:
- ``GET /v1/admin/serving`` informa el backend neutro de embeddings (``"fake"`` o
  ``"api"``), nunca el id de modelo del proveedor (``EMBEDDING_MODEL``).
- ``POST /v1/admin/playground`` devuelve el alias (``"conversacional"`` / ``"agente"``)
  en ``model_name``, consistente con ``/stream`` y ``/agent``, sin exponer el id del
  proveedor.
"""

from __future__ import annotations

import uuid
from unittest.mock import patch

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_current_admin, get_llm_client
from app.llm.clients.fakes import FakeLlmClient
from app.llm.schemas import CompletionResult
from app.main import app


@pytest.mark.asyncio
async def test_admin_serving_exposes_neutral_embedder_backend() -> None:
    fake = FakeLlmClient(models=frozenset({"conversacional", "agente"}))
    app.dependency_overrides[get_current_admin] = lambda: uuid.uuid4()
    app.dependency_overrides[get_llm_client] = lambda: fake
    try:
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/v1/admin/serving")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["embedder"] in {"fake", "api"}
    assert "bge-m3" not in data["embedder"]


@pytest.mark.asyncio
async def test_admin_playground_sync_returns_alias_not_provider_model_id() -> None:
    fake = FakeLlmClient(models=frozenset({"conversacional", "agente"}))
    fake.queue_result(
        CompletionResult(
            text="hola",
            finish_reason="stop",
            prompt_tokens=5,
            completion_tokens=3,
            model_name="provider-secret/internal-model-2026-06",
            latency_ms=12.0,
        )
    )
    app.dependency_overrides[get_current_admin] = lambda: uuid.uuid4()
    app.dependency_overrides[get_llm_client] = lambda: fake
    try:
        transport = ASGITransport(app=app)
        with patch("app.api.v1.admin.playground._wants_real_llm", return_value=True):
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/v1/admin/playground",
                    json={"model": "conversacional", "message": "hola"},
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    data = response.json()
    assert data["model_name"] == "conversacional"
    assert "provider-secret" not in response.text
