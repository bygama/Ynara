"""Filtro y campo ``origin_model_id`` de ``GET /v1/admin/audit`` (UNIT, sin DB).

El panel filtra el audit por el id de texto del modelo que originó la op (ADR-028), en
lugar del enum viejo ``origin_model``. Acá se cubre la capa HTTP: la validación del query
param, que llegue tal cual al service y que la fila lo serialice. La validación es la
misma con la que ``AuditStore.record`` guarda la columna, así que los casos se contrastan
con su regla (``_is_valid_origin_model_id``). El filtrado contra la DB real está en
``tests/api/test_admin_audit.py`` (integración). El gate de admin y la sesión van por
override; el service, mockeado.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from app.core.deps import get_current_admin, get_db
from app.enums import AuditOperation, MemoryLayer, Mode
from app.main import app
from app.memory.audit import _is_valid_origin_model_id
from app.models.audit import ORIGIN_MODEL_ID_MAX_LENGTH
from app.schemas.admin_api import AdminAuditPage, AdminAuditRow

_MODEL_ID = "org/agent-2026-06-01"


async def _override_db() -> AsyncIterator[object]:
    yield object()


@pytest.fixture
def audit_service() -> Iterator[AsyncMock]:
    """Admin y sesión por override; devuelve el ``audit`` mockeado del service."""
    audit = AsyncMock(return_value=AdminAuditPage(items=[], total=0, sensitive_pct=0.0))
    service = MagicMock()
    service.return_value.audit = audit
    app.dependency_overrides[get_current_admin] = lambda: uuid.uuid4()
    app.dependency_overrides[get_db] = _override_db
    try:
        with patch("app.api.v1.admin.metrics.AdminMetricsService", service):
            yield audit
    finally:
        app.dependency_overrides.clear()


async def _get_audit(params: dict[str, str]) -> httpx.Response:
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get("/v1/admin/audit", params=params)


@pytest.mark.parametrize(
    "value",
    [
        _MODEL_ID,
        "modelo con espacios en el medio",
        "org/modelo-ñ",
        "x" * ORIGIN_MODEL_ID_MAX_LENGTH,
    ],
    ids=["provider-id", "inner-spaces", "non-ascii", "max-length"],
)
async def test_origin_model_id_reaches_the_service(audit_service: AsyncMock, value: str) -> None:
    """Un id que el store guarda llega tal cual: el filtro no es más estricto que el store."""
    assert _is_valid_origin_model_id(value)

    resp = await _get_audit({"origin_model_id": value})

    assert resp.status_code == 200
    audit_service.assert_awaited_once()
    assert audit_service.await_args.kwargs["origin_model_id"] == value


async def test_without_the_filter_the_service_gets_none(audit_service: AsyncMock) -> None:
    resp = await _get_audit({})

    assert resp.status_code == 200
    kwargs = audit_service.await_args.kwargs
    assert kwargs["origin_model_id"] is None
    # El filtro por el enum viejo ya no existe: el service no recibe ``origin_model``.
    assert "origin_model" not in kwargs


@pytest.mark.parametrize(
    "value",
    [
        "",
        "x" * (ORIGIN_MODEL_ID_MAX_LENGTH + 1),
        "\x00",
        "org/agent\x1f",
        "org/agent\x7f",
        "org/agent\x85",
        "org/\u200bagent",
        "org/\u00a0agent",
        " org/agent",
        "org/agent ",
        "org/agent\t",
    ],
    ids=[
        "empty",
        "too-long",
        "nul",
        "unit-separator",
        "del",
        "next-line",
        "zero-width-space",
        "inner-nbsp",
        "leading-space",
        "trailing-space",
        "trailing-tab",
    ],
)
async def test_origin_model_id_outside_the_column_is_rejected(
    audit_service: AsyncMock, value: str
) -> None:
    """Un id que el store no guardaría da 422 sin llegar al service ni a la DB.

    Con un NUL, sin este chequeo, el valor llegaría al WHERE y Postgres rechaza el byte
    0x00: el endpoint respondería 500 en vez de 422.
    """
    assert not _is_valid_origin_model_id(value)

    resp = await _get_audit({"origin_model_id": value})

    assert resp.status_code == 422
    assert resp.json()["detail"][0]["loc"] == ["query", "origin_model_id"]
    audit_service.assert_not_awaited()


async def test_the_row_serializes_both_model_fields(audit_service: AsyncMock) -> None:
    audit_service.return_value = AdminAuditPage(
        items=[
            AdminAuditRow(
                id=uuid.uuid4(),
                created_at=datetime.now(UTC),
                operation=AuditOperation.WRITE,
                target_layer=MemoryLayer.SEMANTIC,
                origin_mode=Mode.PRODUCTIVIDAD,
                origin_model=None,
                origin_model_id=_MODEL_ID,
                origin_tool=None,
                sensitive=False,
            )
        ],
        total=1,
        sensitive_pct=0.0,
    )

    resp = await _get_audit({})

    assert resp.status_code == 200
    item = resp.json()["items"][0]
    assert item["origin_model_id"] == _MODEL_ID
    assert item["origin_model"] is None
