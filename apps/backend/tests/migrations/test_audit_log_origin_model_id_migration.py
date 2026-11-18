"""Tests de la migración ``audit_log_origin_model_id``.

Tabla SAGRADA (regla #3): ``audit_log``. Valida que:

- **Unit (sin DB)**: la revisión encadena al head previo (``e5f6a7b8c9d0``); el
  ``upgrade`` SOLO agrega ``origin_model_id`` (``VARCHAR(200)``, nullable, sin
  ``server_default``) y no corre SQL suelto ni toca filas; el ``downgrade`` dropea
  exactamente esa columna; y el modelo ORM coincide con la migración, sin perder
  ``origin_model`` ni los valores de su enum.
- **Integración** (``integration``): en una DB EFÍMERA DEDICADA, una fila vieja de
  ``audit_log`` (``origin_model='qwen'``) atraviesa el upgrade con ``origin_model_id``
  en NULL y su ``origin_model`` intacto, el trigger de inmutabilidad sigue rechazando
  el UPDATE, y el downgrade quita la columna sin borrar la fila. Sin
  ``TEST_DATABASE_URL`` la fixture ``db_url`` del ``conftest`` lo saltea, como al
  resto de los tests de DB.
"""

from __future__ import annotations

import ast
import asyncio
import hashlib
import importlib.util
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID

import asyncpg
import pytest
from alembic.config import Config
from sqlalchemy import Enum, String

from alembic import command
from app.models.audit import ORIGIN_MODEL_ID_MAX_LENGTH, AuditLog

_BACKEND = Path(__file__).resolve().parents[2]
_MIGRATION = _BACKEND / "alembic" / "versions" / "20261117_1200_audit_log_origin_model_id.py"

_TABLE = "audit_log"
_COLUMN = "origin_model_id"
_NEW_REVISION = "6a5be12eb0fd"
_PREV_REVISION = "e5f6a7b8c9d0"  # users_preferences (head previo)


# ---------------------------------------------------------------------------
# Unit (sin DB)
# ---------------------------------------------------------------------------


def _op_calls(name: str) -> list[tuple[str, ast.Call]]:
    """``(operación, llamada)`` de cada ``op.<operación>(...)`` en la función ``name``."""
    tree = ast.parse(_MIGRATION.read_text(encoding="utf-8"))
    funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name]
    assert len(funcs) == 1, f"la migración debería definir {name}() una sola vez"
    return [
        (node.func.attr, node)
        for node in ast.walk(funcs[0])
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "op"
    ]


def _keywords(call: ast.Call) -> dict[str, ast.expr]:
    return {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}


def test_migration_chains_to_previous_head() -> None:
    """La revisión encadena al head previo y trae upgrade/downgrade."""
    spec = importlib.util.spec_from_file_location("_audit_log_origin_model_id", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == _NEW_REVISION
    assert module.down_revision == _PREV_REVISION
    assert callable(module.upgrade)
    assert callable(module.downgrade)


def test_upgrade_only_adds_a_nullable_varchar_column() -> None:
    """El ``upgrade`` hace un solo ``op.add_column``: ni ``execute`` ni backfill."""
    calls = _op_calls("upgrade")
    assert [operation for operation, _ in calls] == ["add_column"]

    table, column = calls[0][1].args
    assert isinstance(table, ast.Constant) and table.value == _TABLE
    assert isinstance(column, ast.Call)
    assert isinstance(column.args[0], ast.Constant) and column.args[0].value == _COLUMN

    # sa.String(length=200)
    col_type = column.args[1]
    assert isinstance(col_type, ast.Call)
    assert isinstance(col_type.func, ast.Attribute) and col_type.func.attr == "String"
    length = _keywords(col_type).get("length")
    assert isinstance(length, ast.Constant) and length.value == ORIGIN_MODEL_ID_MAX_LENGTH

    # nullable=True y sin server_default: las filas existentes no se reescriben.
    column_kwargs = _keywords(column)
    nullable = column_kwargs.get("nullable")
    assert isinstance(nullable, ast.Constant) and nullable.value is True
    assert "server_default" not in column_kwargs
    assert "default" not in column_kwargs


def test_downgrade_drops_only_that_column() -> None:
    """El ``downgrade`` dropea ``audit_log.origin_model_id`` y nada más."""
    calls = _op_calls("downgrade")
    assert [operation for operation, _ in calls] == ["drop_column"]
    args = calls[0][1].args
    assert all(isinstance(arg, ast.Constant) for arg in args)
    assert [arg.value for arg in args if isinstance(arg, ast.Constant)] == [_TABLE, _COLUMN]


def test_model_matches_the_migration() -> None:
    """``AuditLog.origin_model_id`` es el ``VARCHAR(200)`` nullable de la migración."""
    column = AuditLog.__table__.c[_COLUMN]
    assert isinstance(column.type, String)
    assert column.type.length == ORIGIN_MODEL_ID_MAX_LENGTH == 200
    assert column.nullable is True
    assert column.server_default is None
    assert column.default is None
    assert not column.index
    assert all(_COLUMN not in index.columns for index in AuditLog.__table__.indexes)


def test_legacy_origin_model_column_is_kept() -> None:
    """``origin_model`` sigue en el modelo con los dos valores de ``llm_model_enum``."""
    column = AuditLog.__table__.c["origin_model"]
    assert isinstance(column.type, Enum)
    assert column.type.name == "llm_model_enum"
    assert list(column.type.enums) == ["gemma", "qwen"]
    assert column.nullable is True


# ---------------------------------------------------------------------------
# Integración (DB efímera dedicada)
# ---------------------------------------------------------------------------


def _alembic_cfg() -> Config:
    cfg = Config(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "alembic"))
    return cfg


def _swap_database(dsn: str, dbname: str) -> str:
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{dbname}"))


async def _create_db(maintenance_dsn: str, name: str) -> None:
    conn = await asyncpg.connect(dsn=maintenance_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await conn.execute(f'CREATE DATABASE "{name}"')
    finally:
        await conn.close()


async def _drop_db(maintenance_dsn: str, name: str) -> None:
    conn = await asyncpg.connect(dsn=maintenance_dsn)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await conn.close()


async def _column_present(dsn: str, table: str, column: str) -> bool:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        count = await conn.fetchval(
            "select count(*) from information_schema.columns "
            "where table_schema = 'public' and table_name = $1 and column_name = $2",
            table,
            column,
        )
        return bool(count)
    finally:
        await conn.close()


async def _seed_legacy_audit_row(dsn: str) -> UUID:
    """Usuario efímero + una fila de ``audit_log`` como las de antes de ADR-028."""
    conn = await asyncpg.connect(dsn=dsn)
    try:
        user_id = await conn.fetchval(
            "insert into users (is_ephemeral, onboarding_completed, retention_sensitive_days) "
            "values (true, false, 180) returning id"
        )
        return await conn.fetchval(
            "insert into audit_log "
            "(user_id, operation, target_layer, origin_model, record_hash, sensitive) "
            "values ($1, 'write', 'semantic', 'qwen', $2, false) returning id",
            user_id,
            hashlib.sha256(b"fila-de-antes").hexdigest(),
        )
    finally:
        await conn.close()


async def _fetch_audit_row(dsn: str, audit_id: UUID) -> asyncpg.Record | None:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        return await conn.fetchrow("select * from audit_log where id = $1", audit_id)
    finally:
        await conn.close()


async def _update_origin_model_id(dsn: str, audit_id: UUID) -> None:
    conn = await asyncpg.connect(dsn=dsn)
    try:
        await conn.execute(
            "update audit_log set origin_model_id = 'org/modelo' where id = $1",
            audit_id,
        )
    finally:
        await conn.close()


_ROUNDTRIP_DB = "ynara_audit_log_origin_model_id_roundtrip"


@pytest.mark.integration
def test_upgrade_keeps_legacy_rows_and_downgrade_drops_the_column(
    db_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """La fila vieja queda con ``origin_model_id`` NULL; el downgrade no la borra."""
    base_dsn = db_url.replace("+asyncpg", "")
    maintenance_dsn = _swap_database(base_dsn, "postgres")
    ephemeral_dsn = _swap_database(base_dsn, _ROUNDTRIP_DB)

    asyncio.run(_create_db(maintenance_dsn, _ROUNDTRIP_DB))
    monkeypatch.setenv("TEST_DATABASE_URL", ephemeral_dsn)
    cfg = _alembic_cfg()
    try:
        command.upgrade(cfg, _PREV_REVISION)
        assert not asyncio.run(_column_present(ephemeral_dsn, _TABLE, _COLUMN))
        audit_id = asyncio.run(_seed_legacy_audit_row(ephemeral_dsn))

        command.upgrade(cfg, _NEW_REVISION)
        assert asyncio.run(_column_present(ephemeral_dsn, _TABLE, _COLUMN)), (
            "audit_log.origin_model_id debería existir tras el upgrade"
        )
        row = asyncio.run(_fetch_audit_row(ephemeral_dsn, audit_id))
        assert row is not None
        assert row["origin_model_id"] is None, "el upgrade no debe hacer backfill"
        assert row["origin_model"] == "qwen", "el upgrade no debe tocar origin_model"

        # El trigger de inmutabilidad sigue vivo: la columna nueva tampoco se completa
        # a posteriori sobre filas existentes.
        with pytest.raises(asyncpg.CheckViolationError):
            asyncio.run(_update_origin_model_id(ephemeral_dsn, audit_id))

        command.downgrade(cfg, _PREV_REVISION)
        assert not asyncio.run(_column_present(ephemeral_dsn, _TABLE, _COLUMN)), (
            "el downgrade no borró audit_log.origin_model_id"
        )
        row = asyncio.run(_fetch_audit_row(ephemeral_dsn, audit_id))
        assert row is not None, "el downgrade no debe borrar filas de audit_log"
        assert row["origin_model"] == "qwen"
    finally:
        asyncio.run(_drop_db(maintenance_dsn, _ROUNDTRIP_DB))
