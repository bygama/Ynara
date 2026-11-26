"""Protocols de la capa de clientes LLM (M1).

``LLMClient`` es el contrato que el router consume; cualquier
implementacion concreta (``OpenAICompatClient``, ``FakeLlmClient``) lo satisface
por duck typing. ``ToolCallParser`` abstrae como se normalizan las tool
calls de la respuesta del servidor.

Ambos son ``runtime_checkable`` para poder afirmar conformidad en tests sin
heredar explicitamente.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from typing import Protocol, runtime_checkable

from app.llm.schemas import (
    ChatMessage,
    CompletionChunk,
    CompletionResult,
    ModelHealth,
    ToolCall,
    ToolChoice,
    ToolSpec,
)


@runtime_checkable
class ToolCallParser(Protocol):
    """Normaliza tool calls de la respuesta del servidor a ``ToolCall``.

    Los proveedores devuelven las tool calls en formato OpenAI estandar (sin
    importar el modelo); el parser solo las re-empaqueta y parsea el JSON de
    ``arguments``.
    """

    def parse(self, raw_message: dict[str, object]) -> list[ToolCall]:
        """Extrae las tool calls de un ``message`` OpenAI no-streaming."""
        ...

    def accumulate(self, deltas: Iterable[dict[str, object]]) -> list[ToolCall]:
        """Junta fragmentos de tool calls de un stream en ``ToolCall``s."""
        ...


@runtime_checkable
class LLMClient(Protocol):
    """Contrato del cliente de inferencia que consume el router."""

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        thinking: bool | None = None,
        tool_choice: ToolChoice = "auto",
        timeout_s: float | None = None,
    ) -> CompletionResult:
        """Genera una completion no-streaming.

        ``timeout_s=None`` deja que la implementacion use su default (los clientes de
        proveedores usan el ``timeout_s`` de su entrada de ``LLM_PROVIDERS`` o, sin
        valor, el ``PROVIDER_REQUEST_TIMEOUT_S`` de la factory).

        ``thinking`` es la intencion de razonamiento del caller (ADR-028 D3):
        ``True`` = razonar, ``False`` = no razonar, ``None`` = sin preferencia. La
        implementacion la traduce a lo que entienda su proveedor; el cliente por API
        manda ``reasoning_effort`` solo si tiene un valor configurado para ella y, si
        no, no manda nada y el modelo razona segun su default.

        ``tool_choice`` (ADR-031 D5): ``"auto"`` deja que el modelo llame una de
        ``tools``; ``"none"`` se las manda igual pero no le deja llamarlas. Sin
        ``tools`` no tiene efecto.
        """
        ...

    def stream(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        thinking: bool | None = None,
        tool_choice: ToolChoice = "auto",
        timeout_s: float | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        """Genera una completion en streaming (yield de chunks).

        ``timeout_s=None`` -> default de la implementacion (ver ``complete``).

        ``thinking`` y ``tool_choice``: ver ``complete``.
        """
        ...

    async def health(self) -> ModelHealth:
        """Reporta si la instancia responde."""
        ...

    def serves_model(self, model: str) -> bool:
        """Indica si esta instancia sirve el modelo pedido."""
        ...
