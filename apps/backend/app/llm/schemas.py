"""Schemas de la capa LLM (M1).

Tipos de dominio del cliente de inferencia, independientes de FastAPI y de
cada proveedor. ``ChatRequest`` / ``ChatResponse`` son el contrato del router (se
movieron desde ``router.py``); el resto modela lo que el cliente manda al
servidor y lo que devuelve, en formato neutro (no OpenAI ni custom de
modelo).

``CompletionResult`` / ``CompletionChunk`` / ``ModelHealth`` son
``frozen`` + ``strict``: son resultados inmutables que viajan del cliente
al router sin mutarse.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.enums import Mode

Role = Literal["system", "user", "assistant", "tool"]

# ``tool_choice`` que el caller le pide al cliente: ``"auto"`` deja que el modelo decida
# si llama una tool; ``"none"`` le manda las mismas tools pero no le deja llamarlas (la
# confirmación forzada del tool loop, ADR-031 D5: sacar las tools cambiaría el prefijo).
ToolChoice = Literal["auto", "none"]


# ---------- Contrato del router ----------


class ChatRequest(BaseModel):
    """Entrada del router: texto del usuario + modo activo."""

    text: str
    mode: Mode
    session_id: str | None = None


class ChatResponse(BaseModel):
    """Salida del router hacia el caller HTTP."""

    text: str
    actions: list[dict[str, Any]] = []
    session_id: str
    finish_reason: str | None = None
    # Razonamiento del modelo acumulado por el tool loop (canal ``reasoning`` separado).
    # El endpoint /chat/stream lo re-trocea como evento SSE ``reasoning`` (display-only:
    # el toggle de mostrar/ocultar es 100% del front). ``None`` cuando no hubo
    # razonamiento o el turno degrado. Aditivo: los callers que lo ignoran no cambian.
    reasoning: str | None = None


# ---------- Mensajes y tools ----------


class ProviderBlocks(BaseModel):
    """Contenido crudo de la respuesta de un proveedor, para reenviarlo sin tocar.

    ADR-031 D5: dentro de una vuelta con tools, la Messages API pide que los bloques de
    razonamiento vuelvan exactamente como llegaron. ``provider`` es el ``name`` de la
    entrada de ``LLM_PROVIDERS`` que los produjo: un cliente solo los reenvía si es esa
    misma entrada. ``blocks`` son los dicts del wire tal cual (incluidos los vacíos).

    Lleva el razonamiento del modelo: ``blocks`` queda fuera del ``repr`` y nunca se
    loguea ni sale en una respuesta de la API (regla #4a).
    """

    model_config = ConfigDict(strict=True, frozen=True)

    provider: str
    blocks: list[dict[str, Any]] = Field(repr=False)


class ChatMessage(BaseModel):
    """Un turno del historial. ``content`` puede ser ``None`` cuando el
    assistant solo emite tool_calls; ``tool_call_id`` / ``name`` aplican al
    rol ``tool`` (resultado de una tool). ``tool_calls`` preserva las calls
    del assistant para el multi-turno correcto con la API de Chat Completions.

    ``provider_blocks`` (solo ``assistant``) es la respuesta cruda del proveedor que la
    produjo, para reenviarla sin tocar dentro de la vuelta (ADR-031 D5); el cliente de
    otro proveedor la ignora. ``is_error`` (solo ``tool``) marca un resultado con la
    forma de ``tool_error``."""

    model_config = ConfigDict(strict=True)

    role: Role
    content: str | None = None
    tool_call_id: str | None = None
    name: str | None = None
    tool_calls: list[ToolCall] | None = None
    provider_blocks: ProviderBlocks | None = Field(default=None, repr=False)
    is_error: bool = False


class ToolSpec(BaseModel):
    """Especificacion de una tool en formato OpenAI (``parameters`` es un
    JSON schema)."""

    model_config = ConfigDict(strict=True)

    name: str
    description: str
    parameters: dict[str, Any]


class ToolCall(BaseModel):
    """Una tool call ya normalizada: ``arguments`` viene parseado de JSON a
    dict (a diferencia del wire OpenAI, donde es un string)."""

    model_config = ConfigDict(strict=True, frozen=True)

    id: str
    name: str
    arguments: dict[str, Any]


# ---------- Resultados de inferencia ----------


class CompletionResult(BaseModel):
    """Resultado de una completion no-streaming (inmutable)."""

    model_config = ConfigDict(strict=True, frozen=True)

    text: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    finish_reason: str
    prompt_tokens: int
    completion_tokens: int
    model_name: str
    latency_ms: float
    # Razonamiento del modelo en su canal SEPARADO (``reasoning_content`` o
    # ``reasoning`` del message OpenAI). Los proveedores que lo exponen lo mandan
    # APARTE del ``content`` para los thinking models: el ``text`` queda limpio y el
    # pensamiento viaja acá. ``None`` cuando el modelo no expone canal de
    # razonamiento o thinking estuvo apagado. Aditivo: los consumidores que lo
    # ignoran no cambian.
    reasoning: str | None = None
    # Respuesta cruda del proveedor (ADR-031 D5): el tool loop la copia al mensaje
    # ``assistant`` que agrega al historial. ``None`` en los clientes que no la usan.
    provider_blocks: ProviderBlocks | None = Field(default=None, repr=False)
    # Desglose de la caché de prompts (ADR-031 D8). ``prompt_tokens`` ya los incluye:
    # estos dos dicen cuánto de ese contexto se leyó de la caché y cuánto se escribió.
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class CompletionChunk(BaseModel):
    """Un fragmento de un stream (inmutable). ``tool_call_delta`` es el
    fragmento crudo OpenAI de la tool call en curso, si lo hay."""

    model_config = ConfigDict(strict=True, frozen=True)

    delta_text: str
    tool_call_delta: dict[str, Any] | None = None
    finish_reason: str | None = None
    # Canal de razonamiento separado (``reasoning_content`` o ``reasoning`` del delta
    # OpenAI). Los proveedores que lo exponen lo mandan APARTE del ``content`` para los
    # modelos thinking: el texto de respuesta queda limpio y el razonamiento viaja
    # acá. ``None``/ausente cuando el modelo no expone un canal de razonamiento.
    reasoning_delta: str | None = None


class ModelHealth(BaseModel):
    """Estado de salud reportado por una instancia de modelo."""

    model_config = ConfigDict(strict=True, frozen=True)

    model_name: str
    healthy: bool
