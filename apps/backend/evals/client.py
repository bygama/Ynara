"""Cliente LLM de una evaluación: un solo proveedor y sin respuestas degradadas.

Se construye con la factory del backend (regla #4b: el runner no habla HTTP). Con la
lista completa de ``LLM_PROVIDERS`` el respaldo podría contestar por el principal sin
que el reporte lo note; por eso cada corrida deja un solo proveedor en la lista.
"""

from __future__ import annotations

from app.core.config import Settings
from app.llm.clients.base import LLMClient
from app.llm.clients.factory import build_llm_client
from app.llm.config import LlmRuntimeConfig


class EvalConfigError(RuntimeError):
    """La configuración no alcanza para correr una evaluación contra un proveedor."""


def build_eval_client(
    settings: Settings, config: LlmRuntimeConfig, provider_name: str
) -> LLMClient:
    """Cliente contra ``provider_name`` solo, con ``degrade=False``.

    ``degrade=False`` hace que una falla llegue como error (y el reporte la cuente
    aparte), no como el texto de «IA pausada» que el chat le muestra al usuario.
    """
    if settings.llm_backend != "api":
        raise EvalConfigError(
            "LLM_BACKEND tiene que ser 'api': el cliente fake no contesta nada evaluable"
        )
    selected = [p for p in settings.llm_providers if p.name == provider_name]
    if not selected:
        known = ", ".join(p.name for p in settings.llm_providers) or "ninguno"
        raise EvalConfigError(
            f"el proveedor '{provider_name}' no está en LLM_PROVIDERS (hay: {known})"
        )
    single = settings.model_copy(update={"llm_providers": selected})
    return build_llm_client(single, config, degrade=False)
