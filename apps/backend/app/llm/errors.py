"""Taxonomia de errores de la capa LLM (M1).

Tres familias:

- **Transitorios** (reintentables, raiz ``LlmTransientError``): timeout, instancia
  caida, sobrecarga o limite de tasa del proveedor (429, con la espera de
  ``Retry-After``), y el 408 o el 425 que piden repetir el request. Reintentar mas
  tarde puede andar: el ``ResilientClient`` los reintenta y la consolidacion de
  memoria reintenta la task entera.
- **Permanentes** (no reintentar): request mal formado, contexto excedido,
  modelo no servido, credencial rechazada por el proveedor (401/403) y request
  que ese proveedor no acepta (un 4xx sin mapeo propio, como un 404 por un id
  de modelo que no tiene). La credencial y esos rechazos son del proveedor, no
  del request: no se reintenta contra el mismo, pero el respaldo de la lista
  puede aceptarlo (ver ``ResilientClient``). Tambien un embedding con otra
  dimension que la de la memoria (``EmbeddingDimensionError``).
- **Semanticos**: fallas de parseo / ejecucion de tools y de recuperacion
  de memoria.

Regla #4a (datos del usuario nunca en logs ni errores): **ninguna
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


class LlmTransientError(LlmError):
    """Falla transitoria del proveedor: reintentar mas tarde puede andar.

    Raiz de la familia: timeout, caida, sobrecarga y 429 heredan de aca, asi quien
    quiere reintentar pregunta por UNA clase (``isinstance`` o ``autoretry_for``). Un
    408 o un 425 del proveedor la levantan pelada (``raise_for_provider_status``): no son
    una caida ni una sobrecarga, piden repetir el mismo request. El
    ``ResilientClient`` sin degradar (``degrade=False``) la levanta tal cual cuando se
    agota la cadena por algo que no es un rechazo del proveedor (respuesta caida, 429,
    breaker abierto, presupuesto vencido): la consolidacion de memoria la usa para
    reintentar el turno en vez de perderlo.
    """

    message = "falla transitoria del proveedor LLM"


class LlmTimeoutError(LlmTransientError):
    """La instancia no respondio dentro del timeout."""

    message = "timeout de inferencia LLM"


class LlmUnavailableError(LlmTransientError):
    """No se pudo conectar a la instancia o devolvio 503."""

    message = "instancia LLM no disponible"


class LlmOverloadedError(LlmTransientError):
    """La instancia rechazo por sobrecarga (HTTP 429)."""

    message = "instancia LLM sobrecargada"


class LlmRateLimitError(LlmOverloadedError):
    """El proveedor limito la tasa de requests (HTTP 429).

    ``retry_after_s`` es la espera que pidio con ``Retry-After``, en segundos (ya
    convertida si vino como fecha HTTP), o ``None`` si no la mando. El
    ``ResilientClient`` la espera si es corta o si no hay respaldo, y solo si
    despues le queda presupuesto para el request; si no, pasa al siguiente
    proveedor. Subclase de ``LlmOverloadedError``: quien ya trataba la
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


class LlmProviderRejectedError(LlmError):
    """El proveedor rechazo el request por algo propio de el (un 4xx sin mapeo propio).

    Por ejemplo un 404 (el id del modelo o la ruta no existen en ese proveedor), un
    402 (sin credito), un 405 o un 413 (el body supera su limite). Permanente para
    ESE proveedor, como ``LlmAuthError``: reintentar contra el mismo da lo mismo,
    pero otro proveedor de la lista puede aceptar el mismo request. No hereda de
    ``LlmBadRequestError``: si heredara, el ``ResilientClient`` lo re-lanzaria sin
    probar el respaldo. Regla #4a: el detail es el status, nunca el body.
    """

    message = "request rechazado por el proveedor LLM"


class EmbeddingDimensionError(LlmError):
    """El proveedor devolvio un embedding con otra dimension que ``EMBEDDING_DIM``.

    Las columnas de embeddings de la memoria son ``vector(1024)``: un vector de otra
    dimension no se puede guardar ni comparar. El cliente no lo recorta ni lo rellena,
    porque ya no representaria al texto. Suele ser de configuracion (un
    ``EMBEDDING_MODEL`` que no da esa dimension), asi que es permanente: reintentar con
    el mismo modelo da lo mismo. ``expected`` y ``received`` son las dos dimensiones.
    Regla #4a: el detail lleva solo esos numeros, nunca el vector ni el texto.
    """

    message = "dimension de embedding inesperada"

    def __init__(
        self,
        detail: str | None = None,
        *,
        expected: int | None = None,
        received: int | None = None,
    ) -> None:
        super().__init__(detail)
        self.expected = expected
        self.received = received


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

    Se usa cuando la cadena de proveedores y respaldo se agoto y hay que
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
