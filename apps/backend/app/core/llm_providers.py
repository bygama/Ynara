"""Configuración de proveedores de IA y credenciales ``LLM_API_KEY_<NAME>`` (ADR-028 D2).

Módulo hermano de ``app/core/config.py`` para mantener ``config.py`` acotado (<500 líneas):
define ``LlmProvider`` (cada entrada de ``LLM_PROVIDERS``) y la fuente Pydantic Settings
``_LlmApiKeysSource`` que recolecta las credenciales ``LLM_API_KEY_<NAME>`` por prefijo.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from app.core.constants import MODEL_ALIASES
from app.core.urls import validated_base_url

# Prefijo de la credencial de cada proveedor de IA: ``LLM_API_KEY_<NAME>`` (ADR-028 D2).
LLM_API_KEY_PREFIX = "LLM_API_KEY_"

# ``name`` de un proveedor: nombra su variable de credencial, así que va en minúsculas,
# empieza con una letra y sigue con letras, dígitos o ``_``.
_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")

# Protocolo de la API del proveedor (ADR-031 D2): Chat Completions compatible con OpenAI
# o la Messages API de Anthropic.
ProviderProtocol = Literal["openai", "anthropic"]

# Modos de razonamiento de la Messages API (ADR-031 D3) y sus esfuerzos.
ThinkingType = Literal["adaptive", "disabled", "between_tools"]
ThinkingEffort = Literal["low", "medium", "high", "xhigh", "max"]

# Esfuerzos que la API rechaza con el razonamiento apagado o entre tools.
_EFFORTS_ONLY_ADAPTIVE = frozenset({"xhigh", "max"})

# Entradas ``anthropic`` admitidas en la lista (ADR-031 D10): una sola hasta que el
# equipo confirme que los términos comerciales permiten una segunda cuenta.
_MAX_ANTHROPIC_PROVIDERS = 1


class ThinkingMode(BaseModel):
    """Qué razonamiento manda el cliente de Anthropic para una intención (ADR-031 D3).

    - ``adaptive``: razonamiento adaptativo con resumen; pide un ``effort``.
    - ``disabled``: razonamiento apagado.
    - ``between_tools``: razona solo entre llamadas a tools, para los modelos que no
      se pueden apagar del todo.

    ``effort`` va en ``output_config.effort``. La API rechaza ``xhigh`` y ``max`` si el
    modo no es ``adaptive``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: ThinkingType
    effort: ThinkingEffort | None = None

    @model_validator(mode="after")
    def _check_effort(self) -> ThinkingMode:
        if self.type == "adaptive" and self.effort is None:
            raise ValueError("el modo adaptive de razonamiento necesita un effort")
        if self.type != "adaptive" and self.effort in _EFFORTS_ONLY_ADAPTIVE:
            raise ValueError(f"el effort {self.effort} solo vale con el modo adaptive")
        return self


class AliasThinking(BaseModel):
    """Razonamiento de un alias en una entrada ``anthropic`` (ADR-031 D3 y D4).

    - ``on``: el modo para ``thinking=True`` (y para ``None``).
    - ``off``: el modo para ``thinking=False``. Un modelo que no se puede apagar lleva
      acá el adaptativo con el esfuerzo más bajo.
    - ``max_tokens_floor``: el mínimo de ``max_tokens`` cuando el modo que sale es el
      adaptativo, porque el razonamiento cuenta dentro de ese tope. Obligatorio si
      alguno de los dos modos es ``adaptive``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    on: ThinkingMode
    off: ThinkingMode
    max_tokens_floor: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_alias(self) -> AliasThinking:
        adaptive = "adaptive" in (self.on.type, self.off.type)
        if adaptive and self.max_tokens_floor is None:
            raise ValueError("un alias con razonamiento adaptive necesita max_tokens_floor")
        # La confirmación forzada del tool loop pide ``False`` en la misma vuelta que se
        # hizo con ``True``. Con ``between_tools`` la API aceptó ahí otro esfuerzo
        # explícito (ADR-031 D3); sin esfuerzo, o con ``disabled``, no se probó.
        if self.off.type == "disabled" and self.off.effort != self.on.effort:
            raise ValueError("con off disabled, el effort de off tiene que ser el de on")
        if self.off.type == "between_tools" and self.on.effort and not self.off.effort:
            raise ValueError("con off between_tools, off necesita un effort si on lo tiene")
        return self

    def mode_for(self, thinking: bool | None) -> ThinkingMode:
        """El modo para la intención del llamador: ``None`` cuenta como ``True``."""
        return self.off if thinking is False else self.on


class LlmProvider(BaseModel):
    """Un proveedor de IA de ``LLM_PROVIDERS`` (ADR-028 D2, ADR-031 D2).

    - ``name``: identifica al proveedor en los logs y nombra su credencial,
      ``LLM_API_KEY_<NAME>`` (el ``name`` en mayúsculas).
    - ``protocol``: ``openai`` (Chat Completions, el default) o ``anthropic``
      (Messages API).
    - ``base_url``: URL base de su API, con el ``/v1`` (``https``; ver
      ``validated_base_url``).
    - ``models``: ``{alias: id del modelo en el proveedor}``. Tiene que cubrir los dos
      alias de Ynara (``conversacional`` y ``agente``).
    - ``supports_reasoning_effort``: si el proveedor acepta ``reasoning_effort``. Solo
      ``openai``.
    - ``thinking``: ``{alias: AliasThinking}``. Obligatorio con ``anthropic``, para
      cada alias de ``models``; no va con ``openai``.
    - ``timeout_s``: timeout de cada request a esta entrada; sin valor, el default del
      cliente.

    La credencial no va acá ni en el JSON: se lee aparte, como secreto
    (``Settings.llm_api_keys``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    name: str
    protocol: ProviderProtocol = "openai"
    base_url: str
    models: dict[str, str]
    supports_reasoning_effort: bool = False
    thinking: dict[str, AliasThinking] | None = None
    timeout_s: float | None = Field(default=None, gt=0)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if not _PROVIDER_NAME.fullmatch(value):
            raise ValueError(
                "el name del proveedor va en minúsculas, con letras, dígitos o '_' "
                "(nombra su variable LLM_API_KEY_<NAME>)"
            )
        return value

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str) -> str:
        return validated_base_url(value)

    @field_validator("models")
    @classmethod
    def _require_both_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        if not all(alias.strip() and model_id.strip() for alias, model_id in value.items()):
            raise ValueError("models del proveedor tiene un alias o un id de modelo vacío")
        missing = [alias for alias in MODEL_ALIASES if alias not in value]
        if missing:
            raise ValueError(f"models del proveedor no cubre el alias: {', '.join(missing)}")
        return value

    @model_validator(mode="after")
    def _check_protocol_fields(self) -> LlmProvider:
        if self.protocol == "openai":
            if self.thinking is not None:
                raise ValueError("thinking es solo para las entradas anthropic")
            return self
        # Anthropic: el esfuerzo va por modo en ``thinking``, no por esta bandera.
        if self.supports_reasoning_effort:
            raise ValueError("supports_reasoning_effort no aplica a una entrada anthropic")
        if self.thinking is None:
            raise ValueError("una entrada anthropic necesita thinking para cada alias")
        missing = sorted(set(self.models) - set(self.thinking))
        if missing:
            raise ValueError(f"thinking no cubre el alias: {', '.join(missing)}")
        extra = sorted(set(self.thinking) - set(self.models))
        if extra:
            raise ValueError(f"thinking nombra un alias que no está en models: {', '.join(extra)}")
        return self

    @property
    def api_key_env(self) -> str:
        """Variable de entorno con la credencial del proveedor."""
        return f"{LLM_API_KEY_PREFIX}{self.name.upper()}"


def validate_provider_list(providers: list[LlmProvider]) -> list[LlmProvider]:
    """Fail-fast sobre la lista entera de ``LLM_PROVIDERS``.

    - Dos proveedores con el mismo ``name`` compartirían credencial.
    - Más de una entrada ``anthropic`` usaría una segunda cuenta para pasar por encima
      de los límites de la primera, y eso espera a que se confirmen los términos
      comerciales (ADR-031 D10).
    """
    names = [provider.name for provider in providers]
    repeated = sorted({name for name in names if names.count(name) > 1})
    if repeated:
        raise ValueError(f"LLM_PROVIDERS repite el proveedor: {', '.join(repeated)}")
    anthropic = sum(provider.protocol == "anthropic" for provider in providers)
    if anthropic > _MAX_ANTHROPIC_PROVIDERS:
        raise ValueError(
            "LLM_PROVIDERS admite una sola entrada anthropic hasta confirmar los "
            "términos para una segunda cuenta (ADR-031 D10)"
        )
    return providers


class _LlmApiKeysSource(PydanticBaseSettingsSource):
    """Junta las credenciales ``LLM_API_KEY_<NAME>`` en ``Settings.llm_api_keys``.

    El nombre de cada variable depende de ``LLM_PROVIDERS``, así que ningún campo fijo
    la declara: esta fuente las busca por prefijo entre las variables que ya leyeron las
    fuentes del ``.env`` y del entorno, en ese orden (el entorno pisa al ``.env``, como
    en el resto de los campos). Cada valor se envuelve en ``SecretStr`` acá mismo, y uno
    vacío en una fuente posterior anula el anterior y cuenta como faltante.
    """

    def __init__(
        self, settings_cls: type[BaseSettings], *sources: PydanticBaseSettingsSource
    ) -> None:
        super().__init__(settings_cls)
        self._sources = sources

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        # Sin uso: ``__call__`` arma el valor del campo entero.
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        prefix = LLM_API_KEY_PREFIX.lower()
        keys: dict[str, SecretStr] = {}
        for source in self._sources:
            env_vars: Mapping[str, str | None] = getattr(source, "env_vars", {})
            for env_name, value in env_vars.items():
                name = env_name.lower()
                if name.startswith(prefix):
                    provider_name = name.removeprefix(prefix)
                    if value and value.strip():
                        keys[provider_name] = SecretStr(value.strip())
                    else:
                        keys.pop(provider_name, None)
        return {"llm_api_keys": keys} if keys else {}


def validate_llm_api_config(
    *,
    environment: str,
    llm_backend: str,
    llm_providers: list[LlmProvider],
    llm_api_keys: Mapping[str, SecretStr],
) -> None:
    """Fail-fast de la inferencia por API (ADR-028 D7).

    En production el LLM tiene que ser ``api``: con ``fake`` el chat le contestaría
    a un usuario real con el cliente de prueba. Con ``api``, en cualquier entorno,
    tiene que haber al menos un proveedor y cada uno con su credencial
    ``LLM_API_KEY_<NAME>``. El error nombra la variable que falta, nunca un valor.
    """
    if environment == "production" and llm_backend != "api":
        raise ValueError(
            "LLM_BACKEND tiene que ser 'api' en production: el cliente fake no le "
            "contesta a usuarios reales (ADR-028)"
        )
    if llm_backend != "api":
        return
    if not llm_providers:
        raise ValueError(
            "LLM_PROVIDERS está vacío: con LLM_BACKEND=api hace falta al menos un proveedor"
        )
    missing = [
        p.api_key_env
        for p in llm_providers
        if not (key := llm_api_keys.get(p.name)) or not key.get_secret_value().strip()
    ]
    if missing:
        raise ValueError(f"falta la credencial del proveedor: {', '.join(missing)}")


def validate_embedding_api_config(
    *,
    environment: str,
    embedding_backend: str,
    embedding_base_url: str,
    embedding_api_key: SecretStr,
    embedding_model: str,
) -> None:
    """Fail-fast de los embeddings por API (ADR-028 D7).

    En production el embedder tiene que ser ``api``: con ``fake`` la memoria de un
    usuario real se guardaría con vectores que no representan al texto, y la búsqueda
    por similitud no encontraría nada. Con ``api``, en cualquier entorno, hacen falta
    la URL base, la credencial y el modelo. El error nombra las variables que faltan,
    nunca un valor.
    """
    if environment == "production" and embedding_backend != "api":
        raise ValueError(
            "EMBEDDING_BACKEND tiene que ser 'api' en production: el embedder fake no "
            "sirve para la memoria de usuarios reales (ADR-028)"
        )
    if embedding_backend != "api":
        return
    missing = [
        name
        for name, present in (
            ("EMBEDDING_BASE_URL", bool(embedding_base_url)),
            ("EMBEDDING_API_KEY", bool(embedding_api_key.get_secret_value())),
            ("EMBEDDING_MODEL", bool(embedding_model)),
        )
        if not present
    ]
    if missing:
        raise ValueError(
            f"falta la configuración del proveedor de embeddings: {', '.join(missing)}"
        )
