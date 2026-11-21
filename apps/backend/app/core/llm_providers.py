"""Configuración de proveedores de IA y credenciales ``LLM_API_KEY_<NAME>`` (ADR-028 D2).

Módulo hermano de ``app/core/config.py`` para mantener ``config.py`` acotado (<500 líneas):
define ``LlmProvider`` (cada entrada de ``LLM_PROVIDERS``) y la fuente Pydantic Settings
``_LlmApiKeysSource`` que recolecta las credenciales ``LLM_API_KEY_<NAME>`` por prefijo.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, SecretStr, field_validator
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource

from app.core.constants import MODEL_ALIASES
from app.core.urls import validated_base_url

# Prefijo de la credencial de cada proveedor de IA: ``LLM_API_KEY_<NAME>`` (ADR-028 D2).
LLM_API_KEY_PREFIX = "LLM_API_KEY_"

# ``name`` de un proveedor: nombra su variable de credencial, así que va en minúsculas,
# empieza con una letra y sigue con letras, dígitos o ``_``.
_PROVIDER_NAME = re.compile(r"[a-z][a-z0-9_]{0,39}")


class LlmProvider(BaseModel):
    """Un proveedor de IA de ``LLM_PROVIDERS`` (ADR-028 D2).

    - ``name``: identifica al proveedor en los logs y nombra su credencial,
      ``LLM_API_KEY_<NAME>`` (el ``name`` en mayúsculas).
    - ``base_url``: URL base de su API compatible con OpenAI, con el ``/v1``
      (``https``; ver ``validated_base_url``).
    - ``models``: ``{alias: id del modelo en el proveedor}``. Tiene que cubrir los dos
      alias de Ynara (``conversacional`` y ``agente``).
    - ``supports_reasoning_effort``: si el proveedor acepta ``reasoning_effort``.

    La credencial no va acá ni en el JSON: se lee aparte, como secreto
    (``Settings.llm_api_keys``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", hide_input_in_errors=True)

    name: str
    base_url: str
    models: dict[str, str]
    supports_reasoning_effort: bool = False

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

    @property
    def api_key_env(self) -> str:
        """Variable de entorno con la credencial del proveedor."""
        return f"{LLM_API_KEY_PREFIX}{self.name.upper()}"


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
