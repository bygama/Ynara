"""Guarda de borrado total de memoria para reintentos de consolidacion (#56).

Si un usuario ejecuta ``POST /v1/memory/wipe`` mientras una task de consolidacion
(``consolidate_turn`` o ``consolidate_session``) esta reintentando (ventana de hasta
~15 minutos), el reintento NO debe volver a guardar recuerdos anteriores al borrado.

Como se detecta sin tocar tablas sagradas ni migraciones (regla #3):
``MemoryService.wipe_execute`` registra en ``audit_log`` una fila ``DELETE`` con
``target_id IS NULL`` y ``record_hash = sha256("wipe:<capa>")`` por cada capa
borrada. Esta guarda hace un ``SELECT 1`` read-only contra ``audit_log`` preguntando
si existe un wipe de ese usuario con ``created_at >= since``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import AuditOperation, MemoryLayer
from app.memory.hashing import compute_record_hash
from app.models.audit import AuditLog

__all__ = ["was_wiped_after"]

# Digests exactos que escribe ``MemoryService.wipe_execute`` (ver services/memory.py).
_WIPE_RECORD_HASHES: tuple[str, ...] = tuple(
    compute_record_hash(f"wipe:{layer.value}")
    for layer in (MemoryLayer.SEMANTIC, MemoryLayer.EPISODIC, MemoryLayer.PROCEDURAL)
)


async def was_wiped_after(
    session: AsyncSession,
    *,
    user_id: UUID,
    since: datetime,
) -> bool:
    """``True`` si el usuario borro toda su memoria en ``since`` o despues."""
    stmt = (
        select(AuditLog.id)
        .where(
            AuditLog.user_id == user_id,
            AuditLog.operation == AuditOperation.DELETE,
            AuditLog.target_id.is_(None),
            AuditLog.record_hash.in_(_WIPE_RECORD_HASHES),
            AuditLog.created_at >= since,
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None
