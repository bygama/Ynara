"""Cliente LLM falso y programable para tests (M2).

``FakeLlmClient`` implementa ``LLMClient`` sin red: se le programan las
respuestas (o errores) que debe devolver. Vive en ``app/`` (no en
``tests/``) para reusarlo en los tests del router y del pool de milestones
futuros (M3+).

Uso tipico::

    fake = FakeLlmClient(models=frozenset({"agente"}))
    fake.queue_result(CompletionResult(...))
    result = await fake.complete(model="agente", messages=[...])

Si se programa una excepcion, ``complete`` la levanta. Por default
``health`` reporta sano; se puede forzar con ``set_health``.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator

from app.llm.errors import degraded_response
from app.llm.schemas import (
    ChatMessage,
    CompletionChunk,
    CompletionResult,
    ModelHealth,
    ToolSpec,
)

# Texto neutro cuando el fake de la app (sin proveedor configurado) atiende un turno
# sin respuestas programadas (degrade_when_empty=True).
_FAKE_DEGRADED_TEXT = "La IA está pausada en este entorno (sin proveedor configurado)."


class FakeLlmClient:
    """Implementa ``LLMClient`` con respuestas programables."""

    def __init__(
        self,
        *,
        models: frozenset[str],
        record_calls: bool = True,
        degrade_when_empty: bool = False,
    ) -> None:
        """``models``: los modelos que el fake dice servir (``serves_model``).

        - ``record_calls``: ``True`` (default para tests) guarda cada llamada en
          ``complete_calls`` y ``stream_calls``. ``False`` (singleton de la app) no
          acumula mensajes ni memoria descifrada en RAM.
        - ``degrade_when_empty``: ``False`` (default para tests) levanta
          ``AssertionError`` si no hay respuestas programadas. ``True`` (servidor de dev
          con ``LLM_BACKEND=fake``) devuelve una respuesta degradada (``finish_reason=
          "degraded"``) en vez de 500.
        """
        self._models = models
        self._record_calls = record_calls
        self._degrade_when_empty = degrade_when_empty
        self._results: deque[CompletionResult | BaseException] = deque()
        self._chunks: deque[list[CompletionChunk] | BaseException] = deque()
        self._healthy = True
        self.complete_calls: list[dict[str, object]] = []
        self.stream_calls: list[dict[str, object]] = []

    # ---------- programacion ----------

    def queue_result(self, result: CompletionResult) -> None:
        """Encola un resultado para la proxima llamada a ``complete``.

        ``complete`` devuelve el ``CompletionResult`` encolado TAL CUAL, incluido su
        canal ``reasoning`` (separado del ``content``): encolar un resultado con
        ``reasoning`` alcanza para ejercitar el evento SSE ``reasoning`` del chat con
        ``LLM_BACKEND=fake`` (sin proveedor). No hace falta una API aparte.
        """
        self._results.append(result)

    def queue_error(self, error: BaseException) -> None:
        """Encola un error para la proxima llamada a ``complete``."""
        self._results.append(error)

    def queue_chunks(self, chunks: list[CompletionChunk]) -> None:
        """Encola una secuencia de chunks para la proxima llamada a
        ``stream``."""
        self._chunks.append(chunks)

    def queue_stream_error(self, error: BaseException) -> None:
        """Encola un error para la proxima llamada a ``stream``."""
        self._chunks.append(error)

    def set_health(self, healthy: bool) -> None:
        self._healthy = healthy

    # ---------- contrato LLMClient ----------

    def serves_model(self, model: str) -> bool:
        return model in self._models

    async def complete(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        thinking: bool | None = None,
        timeout_s: float | None = None,
    ) -> CompletionResult:
        # ``thinking`` se registra para que los tests de routing por rol (ADR-012
        # D4) puedan afirmar que el router lo deriva correcto (False conversacional,
        # True agente, None desconocido).
        if self._record_calls:
            self.complete_calls.append(
                {"model": model, "messages": messages, "tools": tools, "thinking": thinking}
            )
        if not self._results:
            if self._degrade_when_empty:
                return degraded_response(text=_FAKE_DEGRADED_TEXT, model_name=model)
            raise AssertionError("FakeLlmClient: no hay resultados programados")
        item = self._results.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    async def stream(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
        thinking: bool | None = None,
        timeout_s: float | None = None,
    ) -> AsyncIterator[CompletionChunk]:
        if self._record_calls:
            self.stream_calls.append(
                {"model": model, "messages": messages, "tools": tools, "thinking": thinking}
            )
        if not self._chunks:
            raise AssertionError("FakeLlmClient: no hay chunks programados")
        item = self._chunks.popleft()
        if isinstance(item, BaseException):
            raise item
        for chunk in item:
            yield chunk

    async def health(self) -> ModelHealth:
        model_name = next(iter(self._models), "")
        return ModelHealth(model_name=model_name, healthy=self._healthy)

    async def aclose(self) -> None:
        """No-op: el Fake no tiene recursos de red que liberar.

        Existe para que el teardown del lifespan/pool cierre el cliente LLM de
        forma uniforme sea Fake o real (``ResilientClient``/``OpenAICompatClient``).
        """
        return None
