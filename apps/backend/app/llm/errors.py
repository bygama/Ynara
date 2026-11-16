"""Taxonomia de errores de la capa LLM (M1).

Tres familias:

- **Transitorios** (reintentables): timeout, instancia caida, sobrecarga o
  limite de tasa del proveedor (429, con la espera de ``Retry-After``).
- **Permanentes** (no reintentar): request mal formado, contexto excedido,
  modelo no servido y credencial rechazada por el proveedor (401/403). La
  credencial es del proveedor, no del request: no se reintenta contra el
  mismo, pero el respaldo de la lista tiene la suya (ver ``ResilientClient``).
- **Semanticos**: fallas de parseo / ejecucion de tools y de recuperacion
  de memoria.

Regla #4 (datos del usuario nunca fuera del perimetro): **ninguna
excepcion expone contenido del usuario ni la respuesta del modelo en su
``__str__``**. Los mensajes son etiquetas tecnicas fijas; cualquier detalle
sensible viaja en atributos privados que el logging con scrubbing puede
decidir omitir, nunca en el texto de la excepcion.

``LlmDegradedResponse`` NO es una excepcion: es un helper para construir un
``CompletionResult`` de respuesta degradada (``finish_reason="degraded"``)
cuando toda la cadena de fallback se agoto.
"""

from __future__ import annotations

from app.llm.schemas import CompletionResult


class LlmError(Exception):
    """Raiz de la jerarquia de errores del LLM.

    ``__str__`` devuelve solo una etiqueta tecnica fija; nunca el prompt ni
    la respuesta. Detalles tecnicos no sensibles (status HTTP, nombre del
    modelo) van como argumento opcional ``detail``.
    """

    message: str = "error de inferencia LLM"

    def __init__(self, detail: str | None = None) -> None:
        self._detail = detail
        super().__init__(self.message)

    def __str__(self) -> str:
        if self._detail:
            return f"{self.message}: {self._detail}"
        return self.message


# ---------- Transitorios (reintentables) ----------


class LlmTimeoutError(LlmError):
    """La instancia no respondio dentro del timeout."""

    message = "timeout de inferencia LLM"


class LlmUnavailableError(LlmError):
    """No se pudo conectar a la instancia o devolvio 503."""

    message = "instancia LLM no disponible"


class LlmOverloadedError(LlmError):
    """La instancia rechazo por sobrecarga (HTTP 429)."""

    message = "instancia LLM sobrecargada"


class LlmRateLimitError(LlmOverloadedError):
    """El proveedor limito la tasa de requests (HTTP 429).

    ``retry_after_s`` es la espera que pidio con ``Retry-After``, en segundos (ya
    convertida si vino como fecha HTTP), o ``None`` si no la mando. El
    ``ResilientClient`` la respeta si entra en su presupuesto; si no, pasa al
    siguiente proveedor. Subclase de ``LlmOverloadedError``: quien ya trataba la
    sobrecarga (p.ej. el 503 del playground) la trata igual.
    """

    message = "limite de tasa del proveedor LLM"

    def __init__(self, detail: str | None = None, *, retry_after_s: float | None = None) -> None:
        super().__init__(detail)
        self.retry_after_s = retry_after_s


# ---------- Permanentes (no reintentar) ----------


class LlmBadRequestError(LlmError):
    """Request mal formado (HTTP 400/422). No tiene sentido reintentar."""

    message = "request invalido al LLM"


class LlmContextOverflowError(LlmBadRequestError):
    """El prompt + la generacion exceden la ventana de contexto."""

    message = "contexto excedido"


class ModelNotServedError(LlmError):
    """Ninguna instancia del pool sirve el modelo pedido."""

    message = "modelo no servido"


class LlmAuthError(LlmError):
    """El proveedor rechazo la credencial (HTTP 401/403).

    Permanente para ESE proveedor: reintentar con la misma credencial da lo mismo.
    No es un error del request (no hereda de ``LlmBadRequestError``): el
    ``ResilientClient`` pasa al siguiente proveedor de la lista, que tiene su propia
    credencial. Regla #4a: el detail es el status, nunca el body (un 401 puede
    repetir un pedazo de la clave).
    """

    message = "credencial rechazada por el proveedor LLM"


# ---------- Semanticos ----------


class ToolParsingError(LlmError):
    """No se pudo parsear una tool call de la respuesta del modelo.

    No tumba el turno por si misma: el caller decide si degrada o reintenta.
    """

    message = "error parseando tool call"


class ToolExecutionError(LlmError):
    """La ejecucion de una tool fallo.

    Entrada RESERVADA de la taxonomia, todavia NO cableada. El path previsto
    es que ``tool_loop.py`` envuelva los fallos de ejecucion de tool en esta
    excepcion. No es dead-code sino taxonomia forward-looking: ya queda
    cubierta por el ``except LlmError`` de familia que envuelve ``route()``,
    asi que cuando se cablee no requiere tocar el manejo de errores.
    """

    message = "error ejecutando tool"


class MemoryRetrievalError(LlmError):
    """Fallo la recuperacion de contexto de memoria.

    Entrada RESERVADA de la taxonomia, todavia NO cableada. El path previsto
    es que ``memory_engine.py`` envuelva los fallos de retrieval en esta
    excepcion. No es dead-code sino taxonomia forward-looking: ya queda
    cubierta por el ``except LlmError`` de familia que envuelve ``route()``,
    asi que cuando se cablee no requiere tocar el manejo de errores.
    """

    message = "error recuperando memoria"


# ---------- Degradacion (no es excepcion) ----------


def degraded_response(
    *,
    text: str,
    model_name: str,
    latency_ms: float = 0.0,
) -> CompletionResult:
    """Construye un ``CompletionResult`` de respuesta degradada.

    Se usa cuando la cadena de fallback on-prem se agoto y hay que
    devolverle algo coherente al usuario en vez de una excepcion. El
    ``finish_reason`` queda en ``"degraded"`` para que las capas superiores
    lo distingan de una respuesta normal.
    """
    return CompletionResult(
        text=text,
        tool_calls=[],
        finish_reason="degraded",
        prompt_tokens=0,
        completion_tokens=0,
        model_name=model_name,
        latency_ms=latency_ms,
    )
