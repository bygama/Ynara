"""add origin_model_id (texto) to audit_log.

GATE (regla #3): toca ``audit_log``, tabla SAGRADA. Pide tests y 1 aprobación humana
explícita en el PR.

Por qué: con la inferencia por API (ADR-028) el modelo que origina una operación es un
id de texto del proveedor (p.ej. ``org/modelo-2026-06``), no uno de los dos valores
del enum ``llm_model_enum`` (``gemma``/``qwen``). La columna nueva guarda ese id;
``origin_model`` y su enum quedan como están (no se quitan valores ni la columna) y
desde ahora los escritores la dejan en NULL.

Solo agrega la columna, nullable y sin ``server_default``: las filas existentes quedan
con NULL y NO se hace backfill. ``audit_log`` es inmutable (trigger BEFORE UPDATE
``trg_audit_log_block_update``, migración ``a1f3c9d27e84``): ningún UPDATE sobre filas
existentes está permitido, y un ``ADD COLUMN`` nullable sin default no reescribe filas
ni dispara el trigger. Sin índice: el filtro del panel admin va acotado por
``created_at`` (que sí tiene índice).

DOWNGRADE CON PÉRDIDA: dropea la columna y con ella los ids de modelo de las filas
escritas después del upgrade (las filas quedan, sin ese dato). No toca otras columnas
ni otras tablas.

Revision ID: 6a5be12eb0fd
Revises: e5f6a7b8c9d0
Create Date: 2026-11-17 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a5be12eb0fd"
down_revision: str | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # audit_log.origin_model_id — tabla SAGRADA. Nullable, sin server_default ni
    # backfill: el trigger de inmutabilidad bloquea cualquier UPDATE de filas, y el
    # ADD COLUMN no las reescribe. 200 alcanza para los ids de modelo de los proveedores.
    op.add_column(
        "audit_log",
        sa.Column("origin_model_id", sa.String(length=200), nullable=True),
    )


def downgrade() -> None:
    # Dropea SOLO la columna que esta migración creó (pierde los ids de modelo
    # guardados en ella). No toca otras columnas ni otras tablas.
    op.drop_column("audit_log", "origin_model_id")
