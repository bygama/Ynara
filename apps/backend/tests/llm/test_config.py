"""Tests de carga y fail-fast de ``load_llm_config`` (ADR-028 D3).

Cubren la carga OK contra el ``ynara.config.json`` real del repo, cada caso
incoherente que tiene que disparar ``LlmConfigError`` (modo que apunta a un alias
inexistente, alias desconocido o faltante, rol cambiado, ``conversacional`` que
escribe memoria o llama tools, campos que no son del contrato) y que el schema del
JSON no se desfase del loader.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core.constants import MODEL_ALIASES
from app.llm.config import LlmConfigError, LlmRuntimeConfig, ModelConfig, load_llm_config

# tests/llm/test_config.py -> raíz del repo, donde viven el JSON y su schema.
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_PATH = _REPO_ROOT / "docs" / "architecture" / "ynara-config.schema.json"


def _base_config() -> dict[str, Any]:
    """Config mínima coherente, base para mutar en los tests de fail-fast."""
    return {
        "modes": {
            "productividad": {
                "model": "agente",
                "memory_layers": ["semantic", "episodic"],
                "tools_enabled": ["calendar", "memory"],
                "tone": "neutro-eficaz",
            },
            "vida": {
                "model": "conversacional",
                "memory_layers": ["procedural"],
                "tools_enabled": [],
                "tone": "casual-rioplatense",
            },
        },
        "models": {
            "conversacional": {
                "role": "conversational",
                "writes_memory": False,
                "context_tokens": 8192,
            },
            "agente": {
                "role": "agent",
                "writes_memory": True,
                "context_tokens": 32768,
            },
        },
    }


def _load(tmp_path: Path, data: dict[str, Any]) -> LlmRuntimeConfig:
    path = tmp_path / "ynara.config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return load_llm_config(config_path=path)


def _schema() -> dict[str, Any]:
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


# ---------- Carga OK ----------


def test_load_real_repo_config() -> None:
    """El ``ynara.config.json`` real carga y cada modo apunta a su alias."""
    cfg = load_llm_config()
    assert isinstance(cfg, LlmRuntimeConfig)
    assert set(cfg.models) == set(MODEL_ALIASES)
    agent = cfg.models["agente"]
    assert (agent.role, agent.writes_memory, agent.context_tokens) == ("agent", True, 32768)
    chat = cfg.models["conversacional"]
    assert (chat.role, chat.writes_memory, chat.context_tokens) == ("conversational", False, 8192)
    assert {name: mode.model for name, mode in cfg.modes.items()} == {
        "productividad": "agente",
        "estudio": "conversacional",
        "bienestar": "conversacional",
        "vida": "conversacional",
        "memoria": "agente",
    }


def test_load_minimal_config(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _base_config())
    assert cfg.models["agente"].alias == "agente"
    assert cfg.model_for_mode("productividad").alias == "agente"
    vida = cfg.model_for_mode("vida")
    assert (vida.alias, vida.context_tokens) == ("conversacional", 8192)


# ---------- Fail-fast ----------


def test_mode_points_to_missing_model(tmp_path: Path) -> None:
    data = _base_config()
    data["modes"]["productividad"]["model"] = "no-existe"
    with pytest.raises(LlmConfigError, match="inexistente"):
        _load(tmp_path, data)


def test_unknown_alias_rejected(tmp_path: Path) -> None:
    """Una clave que no es alias (p. ej. el id de un modelo) no entra en ``models``."""
    data = _base_config()
    data["models"]["qwen-3.5-9b"] = dict(data["models"]["agente"])
    with pytest.raises(LlmConfigError, match="alias de modelo desconocido"):
        _load(tmp_path, data)


@pytest.mark.parametrize("alias", MODEL_ALIASES)
def test_missing_alias_rejected(tmp_path: Path, alias: str) -> None:
    data = _base_config()
    del data["models"][alias]
    with pytest.raises(LlmConfigError, match="falta el alias de modelo"):
        _load(tmp_path, data)


@pytest.mark.parametrize(
    ("alias", "role"), [("conversacional", "agent"), ("agente", "conversational")]
)
def test_alias_with_wrong_role_rejected(tmp_path: Path, alias: str, role: str) -> None:
    data = _base_config()
    data["models"][alias]["role"] = role
    with pytest.raises(LlmConfigError, match="tiene que tener role"):
        _load(tmp_path, data)


def test_conversational_alias_cannot_write_memory(tmp_path: Path) -> None:
    data = _base_config()
    data["models"]["conversacional"]["writes_memory"] = True
    with pytest.raises(LlmConfigError, match="no escribe memoria"):
        _load(tmp_path, data)


def test_conversational_mode_cannot_enable_tools(tmp_path: Path) -> None:
    data = _base_config()
    data["modes"]["vida"]["tools_enabled"] = ["calendar"]
    with pytest.raises(LlmConfigError, match="no llama tools"):
        _load(tmp_path, data)


def test_memory_tool_requires_semantic_layer(tmp_path: Path) -> None:
    data = _base_config()
    data["modes"]["productividad"]["memory_layers"] = ["episodic"]
    with pytest.raises(LlmConfigError, match="'semantic' en memory_layers"):
        _load(tmp_path, data)


@pytest.mark.parametrize("context_tokens", [0, -1])
def test_context_tokens_must_be_positive(tmp_path: Path, context_tokens: int) -> None:
    data = _base_config()
    data["models"]["agente"]["context_tokens"] = context_tokens
    with pytest.raises(LlmConfigError, match="invalido"):
        _load(tmp_path, data)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("served_name", "qwen"),
        ("context_window", 262144),
        ("endpoint", "http://localhost:8001/v1"),
        ("model_id", "org/agent"),
    ],
)
def test_unknown_field_in_model_rejected(tmp_path: Path, field: str, value: object) -> None:
    """``extra='forbid'``: ni campos del serving local ni ids de proveedor van en el JSON."""
    data = _base_config()
    data["models"]["agente"][field] = value
    with pytest.raises(LlmConfigError, match="invalido"):
        _load(tmp_path, data)


@pytest.mark.parametrize("block", ["models", "modes"])
def test_missing_block(tmp_path: Path, block: str) -> None:
    data = _base_config()
    del data[block]
    with pytest.raises(LlmConfigError, match=f"falta el bloque '{block}'"):
        _load(tmp_path, data)


def test_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "ynara.config.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(LlmConfigError, match="JSON"):
        load_llm_config(config_path=path)


def test_missing_file(tmp_path: Path) -> None:
    path = tmp_path / "no-existe.json"
    with pytest.raises(LlmConfigError, match="no se pudo leer"):
        load_llm_config(config_path=path)


def test_mode_unknown_raises(tmp_path: Path) -> None:
    cfg = _load(tmp_path, _base_config())
    with pytest.raises(LlmConfigError, match="modo desconocido"):
        cfg.model_for_mode("inexistente")


# ---------- Schema del JSON ----------


def test_schema_matches_loader() -> None:
    """El schema pide los mismos alias y campos que el loader."""
    props = _schema()["properties"]
    assert "llm" not in props
    models = props["models"]
    assert models["required"] == list(MODEL_ALIASES)
    assert models["propertyNames"]["enum"] == list(MODEL_ALIASES)
    fields = set(ModelConfig.model_fields) - {"alias"}
    assert set(models["additionalProperties"]["required"]) == fields
    assert set(models["additionalProperties"]["properties"]) == fields
    mode_model = props["modes"]["additionalProperties"]["properties"]["model"]
    assert mode_model["enum"] == list(MODEL_ALIASES)


def test_real_config_uses_schema_fields() -> None:
    """El JSON real usa solo los alias y los campos que declara el schema."""
    schema_models = _schema()["properties"]["models"]
    data = json.loads((_REPO_ROOT / "ynara.config.json").read_text(encoding="utf-8"))
    assert "llm" not in data
    assert set(data["models"]) == set(schema_models["required"])
    required = set(schema_models["additionalProperties"]["required"])
    for alias, model in data["models"].items():
        assert set(model) == required, alias
