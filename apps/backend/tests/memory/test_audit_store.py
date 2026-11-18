"""Tests UNIT (sin DB) de la validación de ``origin_model_id`` en ``AuditStore``.

``audit_log.origin_model_id`` guarda el id de texto del modelo que originó la op
(ADR-028). El store valida que entre en la columna ANTES del insert, igual que con
``record_hash``: un valor que no cumple levanta ``ValueError`` sin repetir el valor.

Sin mocks de DB (AGENTS.md §5): la persistencia real se prueba en
``tests/integration/test_audit_writes.py``. Acá el store recibe un centinela que no es
una sesión: si la validación no cortara antes, el ``add`` fallaría con
``AttributeError`` y no con ``ValueError``.
"""

from __future__ import annotations

from typing import cast
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AuditOperation, MemoryLayer
from app.memory.audit import AuditStore, _is_valid_origin_model_id
from app.models.audit import ORIGIN_MODEL_ID_MAX_LENGTH

_VALID_HASH = "a" * 64


def _store_without_session() -> AuditStore:
    """Store con un centinela en vez de sesión: cualquier uso de la sesión revienta."""
    return AuditStore(cast(AsyncSession, object()), uuid4())


def test_max_length_matches_the_column() -> None:
    assert ORIGIN_MODEL_ID_MAX_LENGTH == 200


@pytest.mark.parametrize(
    "value",
    [
        "org/agent-model-2026-06",
        "agente",
        "a",
        "modelo con espacios en el medio",
        "x" * ORIGIN_MODEL_ID_MAX_LENGTH,
    ],
)
def test_valid_origin_model_ids(value: str) -> None:
    assert _is_valid_origin_model_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "",
        " ",
        " org/agent",
        "org/agent ",
        "org/\nagent",
        "org/agent\x00",
        "org/agent\t",
        "x" * (ORIGIN_MODEL_ID_MAX_LENGTH + 1),
    ],
)
async def test_record_rejects_an_origin_model_id_that_does_not_fit(value: str) -> None:
    """El valor inválido corta antes del insert y el mensaje no lo repite."""
    assert not _is_valid_origin_model_id(value)
    with pytest.raises(ValueError, match="origin_model_id") as exc_info:
        await _store_without_session().record(
            operation=AuditOperation.WRITE,
            target_layer=MemoryLayer.SEMANTIC,
            target_id=None,
            record_hash=_VALID_HASH,
            origin_model_id=value,
        )
    if value.strip():
        assert value.strip() not in str(exc_info.value)


async def test_record_checks_the_hash_before_the_model_id() -> None:
    """Un ``record_hash`` mal formado se reporta primero, con su propio mensaje."""
    with pytest.raises(ValueError, match="record_hash"):
        await _store_without_session().record(
            operation=AuditOperation.WRITE,
            target_layer=MemoryLayer.SEMANTIC,
            target_id=None,
            record_hash="no-es-un-sha256",
            origin_model_id="",
        )
