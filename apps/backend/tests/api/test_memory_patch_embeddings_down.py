"""``PATCH /v1/memory/semantic/{ref}`` con el proveedor de embeddings caído (UNIT, sin DB).

Editar un hecho semántico re-embeddea el nuevo contenido. Si el proveedor de embeddings
falla (429, timeout, 5xx, etc.), el endpoint responde **503** con un ``detail`` neutro
(sin ecoar el texto del usuario, regla #4) y reenvía ``Retry-After`` en segundos enteros
si el proveedor mandó un 429 con ese header — mismo contrato que ``GET /v1/memory/search``.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_db, get_embedder, get_reranker
from app.core.security import create_access_token
from app.llm.clients.reranker import FakeReranker
from app.llm.errors import LlmRateLimitError, LlmUnavailableError
from app.main import app

_FACT_CONTENT = "mi dirección personal es Calle Falsa 123"


async def _patch_semantic(embedder_error: Exception) -> httpx.Response:
    embedder = MagicMock()
    embedder.embed = AsyncMock(side_effect=embedder_error)
    db = MagicMock()
    db.commit = AsyncMock()

    async def _override_db() -> AsyncIterator[MagicMock]:
        yield db

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[get_reranker] = FakeReranker
    try:
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        headers = {"Authorization": f"Bearer {create_access_token(str(uuid.uuid4()))}"}
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.patch(
                f"/v1/memory/semantic/{uuid.uuid4()}",
                json={"content": _FACT_CONTENT},
                headers=headers,
            )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_patch_semantic_returns_503_with_retry_after_on_rate_limit() -> None:
    response = await _patch_semantic(LlmRateLimitError("HTTP 429", retry_after_s=4.2))
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert _FACT_CONTENT not in response.text


@pytest.mark.asyncio
async def test_patch_semantic_returns_503_without_retry_after_on_unavailable() -> None:
    response = await _patch_semantic(LlmUnavailableError("HTTP 503"))
    assert response.status_code == 503
    assert "Retry-After" not in response.headers
    assert _FACT_CONTENT not in response.text
