"""Carga y validacion de la config de runtime del LLM.

Lee el contrato de producto de ``ynara.config.json``: ``models`` (un alias lógico por
rol, ADR-028 D3) y ``modes`` (cada modo apunta a un alias). Qué proveedor y qué id de
modelo atienden cada alias no va acá: son valores por entorno (``LLM_PROVIDERS`` en
``Settings``), igual que las credenciales.

``load_llm_config()`` es fail-fast: levanta ``LlmConfigError`` con un mensaje claro
si la config es incoherente (modo que apunta a un alias inexistente, alias
desconocido o faltante, rol cambiado, o el alias conversacional escribiendo memoria
o con tools). Esta cacheado a nivel de modulo para no releer el JSON en cada llamada.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.constants import MODEL_ALIAS_AGENT, MODEL_ALIAS_CONVERSATIONAL, MODEL_ALIASES
from app.core.paths import resolve_config_path

# Rol de cada alias (ADR-028 D3): ``conversacional`` solo lee memoria y no llama tools;
# ``agente`` lee, escribe y llama tools.
_ALIAS_ROLES: dict[str, str] = {
    MODEL_ALIAS_CONVERSATIONAL: "conversational",
    MODEL_ALIAS_AGENT: "agent",
}


class LlmConfigError(RuntimeError):
    """Config de LLM incoherente o ilegible. Fail-fast en el arranque."""


class ModelConfig(BaseModel):
    """Un alias de modelo de ``ynara.config.json[models][<alias>]`` (ADR-028 D3).

    ``alias`` es la clave del dict (``conversacional`` | ``agente``) y la identidad del
    modelo en el código: es lo que se le pide al cliente LLM, y cada proveedor lo
    traduce a su id de modelo. ``context_tokens`` es el presupuesto de contexto que el
    router reparte entre el system prompt, la memoria, el historial y la respuesta.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    alias: str
    role: Literal["conversational", "agent"]
    writes_memory: bool
    context_tokens: int = Field(gt=0)


class ModeConfig(BaseModel):
    """Un modo de ``ynara.config.json[modes][<key>]``. ``model`` es un alias."""

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    name: str
    model: str
    memory_layers: list[str]
    tools_enabled: list[str]
    tone: str


class LlmRuntimeConfig(BaseModel):
    """Config de runtime ya validada.

    Inmutable: se construye una vez en el arranque via ``load_llm_config()``.
    """

    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")

    models: dict[str, ModelConfig]
    modes: dict[str, ModeConfig]

    def model_for_mode(self, mode: str) -> ModelConfig:
        """Devuelve la config del alias de modelo que sirve un modo dado."""
        mode_cfg = self.modes.get(mode)
        if mode_cfg is None:
            raise LlmConfigError(f"modo desconocido: {mode!r}")
        return self.models[mode_cfg.model]


def _read_config_file(path: Path) -> dict[str, object]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise LlmConfigError(f"no se pudo leer {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LlmConfigError(f"{path} no es JSON valido: {exc}") from exc
    if not isinstance(data, dict):
        raise LlmConfigError(f"{path} debe ser un objeto JSON en el top-level")
    return data


def _build_runtime_config(data: dict[str, object]) -> LlmRuntimeConfig:
    raw_models = data.get("models")
    raw_modes = data.get("modes")
    if not isinstance(raw_models, dict):
        raise LlmConfigError("falta el bloque 'models' en ynara.config.json")
    if not isinstance(raw_modes, dict):
        raise LlmConfigError("falta el bloque 'modes' en ynara.config.json")

    try:
        models = {
            alias: ModelConfig.model_validate({"alias": alias, **value})
            for alias, value in raw_models.items()
        }
        modes = {
            name: ModeConfig.model_validate({"name": name, **value})
            for name, value in raw_modes.items()
        }
    except (TypeError, ValueError) as exc:
        # Pydantic ValidationError es subclase de ValueError; envolvemos
        # para que el caller solo tenga que atrapar LlmConfigError.
        raise LlmConfigError(f"ynara.config.json invalido: {exc}") from exc

    _validate_coherence(models, modes)

    return LlmRuntimeConfig(models=models, modes=modes)


def _validate_coherence(
    models: dict[str, ModelConfig],
    modes: dict[str, ModeConfig],
) -> None:
    """Chequeos cross-field que Pydantic no puede hacer aislado."""
    for alias, model in models.items():
        expected_role = _ALIAS_ROLES.get(alias)
        if expected_role is None:
            raise LlmConfigError(
                f"alias de modelo desconocido: {alias!r} (van {', '.join(MODEL_ALIASES)})"
            )
        if model.role != expected_role:
            raise LlmConfigError(f"el alias {alias!r} tiene que tener role {expected_role!r}")
    missing = [alias for alias in MODEL_ALIASES if alias not in models]
    if missing:
        raise LlmConfigError(f"falta el alias de modelo: {', '.join(missing)}")
    # ``conversacional`` solo lee memoria (AGENTS.md, routing LLM y memoria).
    if models[MODEL_ALIAS_CONVERSATIONAL].writes_memory:
        raise LlmConfigError(f"el alias {MODEL_ALIAS_CONVERSATIONAL!r} no escribe memoria")

    for name, mode in modes.items():
        if mode.model not in models:
            raise LlmConfigError(f"modo {name!r} apunta a un modelo inexistente: {mode.model!r}")
        if mode.model == MODEL_ALIAS_CONVERSATIONAL and mode.tools_enabled:
            raise LlmConfigError(
                f"modo {name!r} usa el alias {MODEL_ALIAS_CONVERSATIONAL!r}, que no llama tools"
            )
        # Si el modo tiene 'memory' en tools_enabled, debe tener 'semantic' en
        # memory_layers: sin semantic_store no se puede construir memory_registry
        # y memory.search quedaria silenciosamente sin backend (decision #4 M8).
        if "memory" in mode.tools_enabled and "semantic" not in mode.memory_layers:
            raise LlmConfigError(
                f"modo {name!r} tiene 'memory' en tools_enabled pero le falta "
                f"'semantic' en memory_layers (sin semantic store, memory.search "
                f"no tiene backend)"
            )


def load_llm_config(*, config_path: Path | None = None) -> LlmRuntimeConfig:
    """Carga y valida la config de runtime del LLM.

    Inyectable para tests (``config_path``); sin argumentos usa el
    ``ynara.config.json`` de la raiz del repo y cachea el resultado.
    """
    if config_path is None:
        return _load_cached()
    return _build_runtime_config(_read_config_file(config_path))


@lru_cache(maxsize=1)
def _load_cached() -> LlmRuntimeConfig:
    return _build_runtime_config(_read_config_file(resolve_config_path()))
