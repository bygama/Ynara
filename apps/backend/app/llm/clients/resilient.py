"""Cliente resiliente: retry + circuit breaker + respaldo (M3, ADR-028).

``ResilientClient`` implementa ``LLMClient`` (drop-in para el router) y
envuelve un ``ClientPool``. Politica de resiliencia, toda a mano con
stdlib (regla M3: nada de tenacity/circuitbreaker):

1. Pide al pool los candidatos para el modelo, en orden de preferencia
   (el principal primero, el respaldo despues).
2. Para cada candidato cuyo breaker ``allow()``:
   - Reintenta ante errores TRANSITORIOS (timeout, caida, sobrecarga,
     respuesta invalida) con backoff exponencial + jitter, hasta
     ``max_attempts``. Ante un 429 con ``Retry-After`` espera lo que pidio
     el proveedor si entra en el presupuesto global; si no entra, pasa al
     siguiente candidato.
   - Ante un error del REQUEST (request invalido, contexto excedido, modelo
     no servido) re-lanza de inmediato: es bug del request, no del
     proveedor, y el respaldo fallaria igual.
   - Ante una credencial rechazada (401/403) no reintenta contra ese
     proveedor y pasa al siguiente, que tiene la suya.
   - Si tiene exito, devuelve el resultado.
3. Si todos los candidatos se agotan (o ninguno tiene el breaker cerrado), o
   se vence el presupuesto global, devuelve ``degraded_response()``. NUNCA
   propaga una excepcion de infra al caller (UX: siempre algo coherente).

El breaker de cada candidato abre solo por caidas (timeout, 5xx, red:
``_BREAKER_ERRORS``) o porque el presupuesto global corto su request. Cualquier
otra respuesta (error del request, credencial, 429, respuesta invalida) quiere
decir que el proveedor contesto: cuenta como exito y cierra la prueba
HALF_OPEN si la habia. Un candidato que termina devolviendo un resultado
siempre cuenta como exito, aunque un intento anterior haya sido una caida: el
proveedor ya respondio, y una prueba HALF_OPEN que responde en el reintento
vuelve a ``CLOSED`` (contrato de ``CircuitBreaker``).

El respaldo es otro cliente del pool: otra instancia de serving o, con
ADR-028, otro proveedor por API (regla #4b). ``clock`` y ``sleep`` son
inyectables para tests sin tiempo real.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable

from app.llm.clients.base import LLMClient
from app.llm.clients.circuit import CircuitBreaker
from app.llm.clients.pool import ClientPool
from app.llm.errors import (
    LlmAuthError,
    LlmBadRequestError,
    LlmError,
    LlmRateLimitError,
    LlmTimeoutError,
    LlmUnavailableError,
    ModelNotServedError,
    degraded_response,
)
from app.llm.schemas import (
    ChatMessage,
    CompletionChunk,
    CompletionResult,
    ModelHealth,
    ToolSpec,
)

logger = logging.getLogger(__name__)

# Errores del REQUEST: se re-lanzan sin reintentar ni pasar al respaldo (bug del
# request, no del proveedor: el respaldo fallaria igual). Incluye el contexto
# excedido, subclase de ``LlmBadRequestError``.
_REQUEST_ERRORS: tuple[type[LlmError], ...] = (
    LlmBadRequestError,
    ModelNotServedError,
)

# Errores PERMANENTES: no se reintentan contra el mismo candidato. Los del request
# se re-lanzan; la credencial rechazada (401/403) es del proveedor, no del request,
# asi que se pasa al siguiente candidato, que tiene la suya. Cualquier OTRO
# ``LlmError`` (timeout, caida, sobrecarga, 4xx no mapeado, respuesta malformada,
# tool parse error) se reintenta y, al agotarse, pasa al respaldo o degrada.
_PERMANENT_ERRORS: tuple[type[LlmError], ...] = (*_REQUEST_ERRORS, LlmAuthError)

# Errores que abren el breaker: el proveedor no respondio (timeout, 5xx, red). El
# resto de los ``LlmError`` son respuestas del proveedor: no lo marcan como caido.
_BREAKER_ERRORS: tuple[type[LlmError], ...] = (LlmTimeoutError, LlmUnavailableError)

_DEFAULT_DEGRADED_TEXT = "Estoy con un problema tecnico, proba en un ratito."

# Presupuesto global (en segundos) para TODA la cadena de candidatos + respaldo
# de un ``complete``. Sin este tope, el peor caso es ~timeout_per_request x
# max_attempts x N_candidatos (p.ej. 120s x 3 x 2 -> ~12 min) antes de degradar.
# Al vencerse el budget se corta limpio y se devuelve la respuesta degradada
# (mismo contrato que agotar la cadena), dando ademas un punto de cancelacion
# async cooperativo. Es una cota defensiva, no el camino feliz.
#
# Relacion con ``request_timeout_s`` (ynara.config.json[llm.serving] = 120s, el
# timeout POR request individual): este tope (90s) es deliberadamente MENOR que
# un solo request timeout. Asi la cota global de la cadena corta ANTES de que
# siquiera el primer request llegue a su propio timeout de 120s, garantizando un
# techo de latencia agregada por debajo del peor caso de un request aislado.
# Para que un proveedor colgado deje lugar al respaldo, su timeout por request
# tiene que entrar varias veces en este tope (intentos x timeout + backoff + el
# request al respaldo); si no, el presupuesto corta antes y la respuesta degrada.
_DEFAULT_TOTAL_BUDGET_S: float = 90.0


class ResilientClient:
    """Envuelve un ``ClientPool`` con retry, breaker y respaldo."""

    def __init__(
        self,
        pool: ClientPool,
        *,
        max_attempts: int = 3,
        base_backoff_s: float = 0.5,
        max_backoff_s: float = 8.0,
        total_budget_s: float = _DEFAULT_TOTAL_BUDGET_S,
        degraded_text: str = _DEFAULT_DEGRADED_TEXT,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        breaker_factory: Callable[[], CircuitBreaker] = CircuitBreaker,
    ) -> None:
        """Configura la politica de resiliencia.

        ``total_budget_s`` es el deadline global de ``complete``: envuelve toda
        la cadena de candidatos + respaldo para que la respuesta degradada
        llegue rapido en vez de sumar el timeout de cada intento. El corte lo
        hace el reloj del event loop (``asyncio.timeout``); ``clock`` mide
        cuanto queda de ese presupuesto para decidir si se respeta el
        ``Retry-After`` de un 429. ``clock`` y ``sleep`` son inyectables para
        tests deterministas; el ``breaker_factory`` construye un
        ``CircuitBreaker`` por cada cliente del pool (uno por cliente, keyed
        por ``id(client)``).
        """
        if max_attempts < 1:
            raise ValueError("max_attempts tiene que ser al menos 1")
        self._pool = pool
        self._max_attempts = max_attempts
        self._base_backoff_s = base_backoff_s
        self._max_backoff_s = max_backoff_s
        self._total_budget_s = total_budget_s
        self._degraded_text = degraded_text
        self._clock = clock
        self._sleep = sleep
        self._breakers: dict[int, CircuitBreaker] = {
            id(client): breaker_factory() for client in pool.clients
        }

    def serves_model(self, model: str) -> bool:
        """``True`` si algun cliente del pool sirve el modelo."""
        return bool(self._pool.candidates(model))

    async def aclose(self) -> None:
        """Libera los recursos de los clientes del pool (``httpx.AsyncClient``).

        Delega en ``ClientPool.aclose``; lo llama el shutdown del lifespan para
        no filtrar conexiones de los clientes HTTP en produccion.
        """
        await self._pool.aclose()

    async def health(self) -> ModelHealth:
        """Salud agregada: sano si al menos un cliente del pool responde.

        Reporta el ``model_name`` del primer cliente sano; si ninguno
        responde, el del primero del pool (o vacio si el pool esta vacio).
        """
        first_name = ""
        for client in self._pool.clients:
            try:
                health = await client.health()
            except Exception:  # noqa: S112 -- health() roto = no-sano; el logging real es M4
                # Un cliente que rompe en health() se trata como no-sano: no
                # debe tumbar el chequeo agregado. Probamos el siguiente.
                continue
            if not first_name:
                first_name = health.model_name
            if health.healthy:
                return ModelHealth(model_name=health.model_name, healthy=True)
        return ModelHealth(model_name=first_name, healthy=False)

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
        """Completa con retry + respaldo; degrada si todo falla.

        Toda la cadena de candidatos vive bajo un deadline global
        (``total_budget_s``): si se vence, se corta y se degrada igual que al
        agotar la cadena, evitando que el peor caso sume el timeout de cada
        intento (P2.5). El candidato que estaba en curso cuenta como caido en
        su breaker: no respondio a tiempo, y asi una prueba HALF_OPEN no queda
        colgada.

        ``thinking`` es passthrough puro hacia el cliente subyacente (ADR-012 D4):
        no hay logica nueva, solo se hila por toda la cadena de candidatos.
        """
        tally = _BreakerTally()
        deadline = self._clock() + self._total_budget_s
        try:
            async with asyncio.timeout(self._total_budget_s):
                result = await self._run_candidates(
                    tally,
                    deadline,
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking=thinking,
                    timeout_s=timeout_s,
                )
        except TimeoutError:
            # Budget global agotado: misma ruta degradada que agotar la cadena.
            tally.resolve(cut=True)
            logger.warning("llm: presupuesto agotado, respuesta degradada")
            return degraded_response(text=self._degraded_text, model_name=model)
        if result is not None:
            return result
        logger.warning("llm: sin candidatos disponibles, respuesta degradada")
        return degraded_response(text=self._degraded_text, model_name=model)

    async def _run_candidates(
        self,
        tally: _BreakerTally,
        deadline: float,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        max_tokens: int,
        temperature: float,
        thinking: bool | None,
        timeout_s: float | None,
    ) -> CompletionResult | None:
        """Recorre los candidatos (principal -> respaldo).

        Devuelve el primer resultado exitoso, o ``None`` si todos se agotan /
        tienen el breaker abierto (el caller degrada).
        """
        for client in self._pool.candidates(model):
            breaker = self._breakers[id(client)]
            if not breaker.allow():
                continue
            result = await self._try_candidate(
                client,
                breaker,
                tally,
                deadline,
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking=thinking,
                timeout_s=timeout_s,
            )
            if result is not None:
                return result
        return None

    async def _try_candidate(
        self,
        client: LLMClient,
        breaker: CircuitBreaker,
        tally: _BreakerTally,
        deadline: float,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        max_tokens: int,
        temperature: float,
        thinking: bool | None,
        timeout_s: float | None,
    ) -> CompletionResult | None:
        """Reintenta un candidato; ``None`` si se agota (toca el respaldo).

        Re-lanza los errores del request sin probar el siguiente candidato.
        Una credencial rechazada o un 429 que no entra en el presupuesto cortan
        los reintentos de este candidato; el resto de los ``LlmError`` se
        reintentan con backoff. El breaker se resuelve siempre (ver
        ``_BreakerTally``).
        """
        tally.start(breaker)
        last_error: LlmError | None = None
        for attempt in range(self._max_attempts):
            try:
                result = await client.complete(
                    model=model,
                    messages=messages,
                    tools=tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    thinking=thinking,
                    timeout_s=timeout_s,
                )
            except _REQUEST_ERRORS:
                tally.resolve()
                raise
            except LlmError as exc:
                # Transitorio, credencial o LlmError no clasificado (4xx no
                # mapeado, respuesta sin choices, tool parse error).
                tally.note(exc)
                last_error = exc
                delay = self._retry_delay(exc, attempt, deadline)
                if delay is None:
                    break
                await self._sleep(delay)
            else:
                tally.succeed()
                return result
        tally.resolve()
        logger.warning(
            "llm: candidato %s descartado (%s)",
            _client_label(client),
            type(last_error).__name__,
        )
        return None

    def _retry_delay(self, exc: LlmError, attempt: int, deadline: float) -> float | None:
        """Espera antes de reintentar el mismo candidato, o ``None`` para pasar al siguiente.

        Sin reintento tras el ultimo intento ni ante un error permanente (la
        credencial no cambia entre intentos). Un 429 con ``Retry-After`` espera lo
        que pidio el proveedor si entra en lo que queda del presupuesto; si no
        entra, pasa al siguiente candidato en vez de gastar el presupuesto
        esperando. El resto, backoff exponencial.
        """
        if isinstance(exc, _PERMANENT_ERRORS) or attempt + 1 >= self._max_attempts:
            return None
        if isinstance(exc, LlmRateLimitError) and exc.retry_after_s is not None:
            remaining = deadline - self._clock()
            return exc.retry_after_s if exc.retry_after_s <= remaining else None
        return self._backoff_delay(attempt)

    def _backoff_delay(self, attempt: int) -> float:
        """Backoff exponencial cap a ``max_backoff_s`` mas jitter."""
        exponential = self._base_backoff_s * (2**attempt)
        capped = min(exponential, self._max_backoff_s)
        # Jitter para descorrelacionar reintentos de instancias paralelas;
        # no es uso criptografico, asi que ``random`` esta bien (noqa S311).
        jitter = random.uniform(0.0, self._base_backoff_s)  # noqa: S311
        # Cap duro: el jitter no debe empujar el delay por encima del techo.
        return min(capped + jitter, self._max_backoff_s)

    def stream(
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
        """Streamea desde un candidato sano.

        Limitacion de M3: NO hay respaldo mid-stream ni retry una vez que el
        stream arranco. Se elige un candidato con el breaker cerrado y se
        delega; si el primer chunk falla, la excepcion se propaga (el router
        decide como degradar el streaming). El respaldo solo aplica al modo
        no-streaming (``complete``): por ejemplo, con la credencial del
        principal rechazada, ``stream`` falla y ``complete`` pasa al respaldo.

        El resultado del stream SI alimenta el breaker del candidato elegido, con
        el mismo criterio que ``complete`` (solo una caida lo abre; cualquier otra
        respuesta cuenta como exito): cierra la prueba HALF_OPEN que
        ``_pick_for_stream`` pudo haber consumido, para que un candidato sano no
        quede demoteado en ``complete`` tras un stream durante su ventana de
        recovery.

        ``thinking`` es passthrough puro hacia el cliente elegido (ADR-012 D4).
        """
        return self._stream(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            timeout_s=timeout_s,
        )

    async def _stream(
        self,
        *,
        model: str,
        messages: list[ChatMessage],
        tools: list[ToolSpec] | None,
        max_tokens: int,
        temperature: float,
        thinking: bool | None,
        timeout_s: float | None,
    ) -> AsyncIterator[CompletionChunk]:
        client = self._pick_for_stream(model)
        # El breaker del candidato elegido SIEMPRE se resuelve: ``_pick_for_stream``
        # puede haber consumido una prueba HALF_OPEN (``allow()`` pasa de OPEN a
        # HALF_OPEN y marca ``_probe_in_flight``), y sin cerrarla este candidato
        # quedaria salteado en ``complete()`` hasta reiniciar el proceso. Mismo
        # criterio que ``_try_candidate``: una caida (``_BREAKER_ERRORS``) o un error
        # que no es ``LlmError`` lo (re)abre; cualquier otro ``LlmError`` (request
        # invalido, credencial, 429) es una respuesta del proveedor y cuenta como
        # exito, asi un request invalido no abre el breaker de un proveedor sano.
        # Cancelacion / corte del consumidor (``CancelledError`` / ``GeneratorExit``,
        # ambos ``BaseException``) NO son fallo del proveedor: ``except Exception`` no
        # los atrapa, se propagan sin tocar el breaker.
        breaker = self._breakers[id(client)]
        try:
            async for chunk in client.stream(
                model=model,
                messages=messages,
                tools=tools,
                max_tokens=max_tokens,
                temperature=temperature,
                thinking=thinking,
                timeout_s=timeout_s,
            ):
                yield chunk
        except LlmError as exc:
            if isinstance(exc, _BREAKER_ERRORS):
                breaker.record_failure()
            else:
                breaker.record_success()
            raise
        except Exception:
            breaker.record_failure()
            raise
        else:
            breaker.record_success()

    def _pick_for_stream(self, model: str) -> LLMClient:
        """Primer candidato con el breaker cerrado.

        Si TODOS los breakers estan abiertos, cae al primer candidato igual
        (best-effort): streaming no degrada en M3 (ver ``stream``), asi que es
        preferible intentar el stream antes que no devolver nada. Mientras haya
        alguno cerrado, se prefiere ese para no martillar una instancia caida.
        """
        candidates = self._pool.candidates(model)
        if not candidates:
            raise ModelNotServedError(model)
        for client in candidates:
            if self._breakers[id(client)].allow():
                return client
        return candidates[0]


class _BreakerTally:
    """Lo que el candidato en curso de un ``complete()`` le hace a su breaker.

    Se resuelve UNA vez por candidato. Si devuelve un resultado, exito
    (``succeed``), aunque un intento anterior haya sido una caida: el proveedor
    respondio. Si se agota sin resultado o re-lanza un error del request
    (``resolve``), falla si alguno de sus intentos fue una caida
    (``_BREAKER_ERRORS``) o si el presupuesto global lo corto; si no, exito,
    porque el proveedor respondio (aunque haya sido con un error del request, de
    la credencial o un 429). Resolver siempre cierra tambien la prueba HALF_OPEN
    que ``allow()`` pudo haber abierto: sin eso el candidato quedaria salteado
    hasta reiniciar el proceso. Vive por llamada (no en el ``ResilientClient``)
    para que dos ``complete()`` concurrentes no se pisen.
    """

    def __init__(self) -> None:
        self._breaker: CircuitBreaker | None = None
        self._failed = False

    def start(self, breaker: CircuitBreaker) -> None:
        self._breaker = breaker
        self._failed = False

    def note(self, exc: LlmError) -> None:
        if isinstance(exc, _BREAKER_ERRORS):
            self._failed = True

    def succeed(self) -> None:
        breaker, self._breaker = self._breaker, None
        if breaker is not None:
            breaker.record_success()

    def resolve(self, *, cut: bool = False) -> None:
        breaker, self._breaker = self._breaker, None
        if breaker is None:
            return
        if self._failed or cut:
            breaker.record_failure()
        else:
            breaker.record_success()


def _client_label(client: LLMClient) -> str:
    """Nombre para los logs: el del proveedor si lo tiene, si no la clase del cliente."""
    name = getattr(client, "name", None)
    return name if isinstance(name, str) and name else type(client).__name__
