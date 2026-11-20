"""Cliente resiliente: retry + circuit breaker + respaldo (M3, ADR-028).

``ResilientClient`` implementa ``LLMClient`` (drop-in para el router) y envuelve un
``ClientPool``. Politica de errores, esperas y breaker vive en ``_resilience.py``; aca
queda el recorrido de los candidatos, el streaming y el presupuesto global.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Sequence

from app.llm.clients._resilience import (
    BREAKER_ERRORS,
    DEFAULT_MAX_WAIT_WITH_FALLBACK_S,
    DEFAULT_REQUEST_MARGIN_S,
    REQUEST_ERRORS,
    BreakerTally,
    ChainOutcome,
    RetryPolicy,
    aggregate_health,
    client_label,
    probe_all_health,
)
from app.llm.clients.base import LLMClient
from app.llm.clients.circuit import CircuitBreaker
from app.llm.clients.pool import ClientPool
from app.llm.errors import (
    LlmError,
    LlmTransientError,
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

_DEFAULT_DEGRADED_TEXT = "Estoy con un problema tecnico, proba en un ratito."

# Presupuesto global (en segundos) para TODA la cadena de candidatos + respaldo
# de un ``complete``. Sin este tope, el peor caso es ~timeout_per_request x
# max_attempts x N_candidatos (p.ej. 20s x 3 x 2 -> 2 min) antes de degradar.
# Al vencerse el budget se corta limpio y se devuelve la respuesta degradada
# (mismo contrato que agotar la cadena), dando ademas un punto de cancelacion
# async cooperativo. Es una cota defensiva, no el camino feliz.
#
# Relacion con el timeout POR request (``PROVIDER_REQUEST_TIMEOUT_S`` de la
# factory, 20s): para que un proveedor colgado deje lugar al respaldo, su timeout
# por request tiene que entrar varias veces en este tope (intentos x timeout +
# backoff + el request al respaldo); si no, el presupuesto corta antes y la
# respuesta degrada. ``check_resilience_budget`` lo verifica al armar el cliente.
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
        rate_limit_max_wait_with_fallback_s: float = DEFAULT_MAX_WAIT_WITH_FALLBACK_S,
        rate_limit_request_margin_s: float = DEFAULT_REQUEST_MARGIN_S,
        degraded_text: str = _DEFAULT_DEGRADED_TEXT,
        degrade: bool = True,
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

        Ante un 429, ``rate_limit_max_wait_with_fallback_s`` es el
        ``Retry-After`` mas largo que se espera si hay un respaldo disponible, y
        ``rate_limit_request_margin_s`` lo que tiene que quedar del presupuesto
        despues de esperar, para el request (ver ``RetryPolicy``).

        ``degrade`` decide que pasa cuando la cadena se agota sin resultado. ``True``
        (el default: el chat y el playground) devuelve ``degraded_response()``.
        ``False`` levanta el error, para quien puede reintentar mas tarde (la
        consolidacion de memoria en el worker): una respuesta degradada no distingue un
        429 de una credencial rechazada, y solo el primero vale la pena reintentar.
        """
        self._policy = RetryPolicy(
            max_attempts=max_attempts,
            base_backoff_s=base_backoff_s,
            max_backoff_s=max_backoff_s,
            max_wait_with_fallback_s=rate_limit_max_wait_with_fallback_s,
            request_margin_s=rate_limit_request_margin_s,
        )
        self._pool = pool
        self._total_budget_s = total_budget_s
        self._degraded_text = degraded_text
        self._degrade = degrade
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
        """Salud agregada: sano si al menos un cliente del pool responde."""
        return await aggregate_health(self._pool.clients)

    async def provider_health(self) -> list[ModelHealth]:
        """Salud de cada cliente del pool en orden (sondeados en paralelo)."""
        return await probe_all_health(self._pool.clients)

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
        intento (P2.5). Si el corte agarra un request en curso, ese candidato
        cuenta como caido en su breaker (no respondio a tiempo); si agarra una
        espera entre intentos, el proveedor ya habia contestado y cuenta como
        exito, salvo que un intento anterior haya sido una caida. En los dos
        casos una prueba HALF_OPEN no queda colgada.

        ``thinking`` es passthrough puro hacia el cliente subyacente (ADR-012 D4):
        no hay logica nueva, solo se hila por toda la cadena de candidatos.

        Con ``degrade=False``, en vez de degradar levanta: el rechazo del proveedor si
        TODOS los candidatos del modelo lo rechazaron (``LlmAuthError`` o
        ``LlmProviderRejectedError``: reintentar da lo mismo), ``ModelNotServedError``
        si ninguno sirve el modelo, y ``LlmTransientError`` en cualquier otro caso
        (caidas, 429, breaker abierto, presupuesto vencido: mas tarde puede andar).
        """
        tally = BreakerTally()
        outcome = ChainOutcome()
        deadline = self._clock() + self._total_budget_s
        try:
            async with asyncio.timeout(self._total_budget_s):
                result = await self._run_candidates(
                    tally,
                    outcome,
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
            # Budget global agotado: misma ruta que agotar la cadena.
            tally.resolve(cut=True)
            if not self._degrade:
                logger.warning("llm: presupuesto agotado, sin respuesta")
                raise LlmTransientError("presupuesto agotado") from None
            logger.warning("llm: presupuesto agotado, respuesta degradada")
            return degraded_response(text=self._degraded_text, model_name=model)
        except BaseException:
            # Cancelacion del caller (CancelledError) u otra BaseException: liberar
            # una prueba HALF_OPEN en vuelo sin marcar exito ni fallo del proveedor.
            tally.cancel()
            raise
        if result is not None:
            return result
        if not self._degrade:
            logger.warning("llm: sin candidatos disponibles, sin respuesta")
            raise outcome.error(model)
        logger.warning("llm: sin candidatos disponibles, respuesta degradada")
        return degraded_response(text=self._degraded_text, model_name=model)

    async def _run_candidates(
        self,
        tally: BreakerTally,
        outcome: ChainOutcome,
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
        tienen el breaker abierto (el caller degrada o levanta). ``outcome`` anota
        como termino cada candidato que no dio resultado.
        """
        candidates = self._pool.candidates(model)
        for index, client in enumerate(candidates):
            breaker = self._breakers[id(client)]
            if not breaker.allow():
                outcome.skipped = True
                continue
            result = await self._try_candidate(
                client,
                breaker,
                tally,
                outcome,
                deadline,
                candidates[index + 1 :],
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
        tally: BreakerTally,
        outcome: ChainOutcome,
        deadline: float,
        fallbacks: Sequence[LLMClient],
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
        Un rechazo del proveedor (credencial o 4xx propio) o un 429 que no
        conviene esperar cortan los reintentos de este candidato; el resto de
        los ``LlmError`` se reintentan con backoff. ``fallbacks`` son los
        candidatos que siguen: si alguno puede atender, un 429 no se espera
        salvo que sea corto (ver ``RetryPolicy``). El breaker se resuelve
        siempre (ver ``BreakerTally``). Si se agota, anota en ``outcome`` el ultimo
        error del candidato (el modo sin degradar elige con eso que levantar).
        """
        tally.start(breaker)
        last_error: LlmError | None = None
        for attempt in range(self._policy.max_attempts):
            tally.sending()
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
            except REQUEST_ERRORS:
                tally.resolve()
                raise
            except LlmError as exc:
                # Transitorio, rechazo del proveedor (credencial o 4xx propio) o
                # LlmError no clasificado (respuesta sin choices, tool parse error).
                tally.note(exc)
                last_error = exc
                delay = self._policy.delay(
                    exc,
                    attempt,
                    deadline - self._clock(),
                    has_fallback=self._any_available(fallbacks),
                )
                if delay is None:
                    break
                await self._sleep(delay)
            else:
                tally.succeed()
                return result
        tally.resolve()
        if last_error is not None:
            outcome.errors.append(last_error)
        logger.warning(
            "llm: candidato %s descartado (%s)",
            client_label(client),
            type(last_error).__name__,
        )
        return None

    def _any_available(self, clients: Sequence[LLMClient]) -> bool:
        """``True`` si el breaker de alguno de ``clients`` lo dejaria atender.

        Usa ``would_allow()``: preguntar no consume la prueba HALF_OPEN de un
        respaldo, que queda para cuando le toque.
        """
        return any(self._breakers[id(client)].would_allow() for client in clients)

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
        """Streamea desde el primer candidato que responda.

        Recorre los candidatos como ``complete`` (principal -> respaldo), pero sin
        reintentar el mismo ni esperar un ``Retry-After``: si uno falla ANTES del
        primer chunk (caida, credencial rechazada, 4xx propio, 429), pasa al
        siguiente; si fallan todos, propaga el error del ultimo (el caller decide
        como degradar el streaming). Un error del request se propaga sin probar
        el respaldo, que fallaria igual. Una vez emitido el primer chunk no hay
        respaldo ni retry: un corte a mitad del stream se propaga. Si ningun
        candidato tiene el breaker en condiciones, prueba igual el primero
        (best-effort: el streaming no degrada).

        Cada candidato que se prueba alimenta su breaker con el mismo criterio
        que ``complete`` (solo una caida lo abre; cualquier otra respuesta cuenta
        como exito), asi se cierra la prueba HALF_OPEN que ``allow()`` pudo haber
        abierto. ``allow()`` se pide recien al llegar a cada candidato, para no
        consumir la prueba de uno que no se usa.

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
        candidates = self._pool.candidates(model)
        if not candidates:
            raise ModelNotServedError(model)
        last_error: LlmError | None = None
        # El breaker de cada candidato que se prueba SIEMPRE se resuelve una vez:
        # ``allow()`` puede haber abierto una prueba HALF_OPEN, y sin cerrarla el
        # candidato quedaria salteado en ``complete()`` hasta reiniciar el proceso.
        # Una caida (``BREAKER_ERRORS``) o un error que no es ``LlmError`` lo
        # (re)abre; cualquier otro ``LlmError`` es una respuesta del proveedor y
        # cuenta como exito. Cancelacion / corte del consumidor (``CancelledError``
        # / ``GeneratorExit``, ambos ``BaseException``) NO son fallo del proveedor:
        # ``except Exception`` no los atrapa, se propagan sin tocar el breaker.
        for client in self._stream_candidates(candidates):
            breaker = self._breakers[id(client)]
            emitted = False
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
                    emitted = True
                    yield chunk
            except LlmError as exc:
                if isinstance(exc, BREAKER_ERRORS):
                    breaker.record_failure()
                else:
                    breaker.record_success()
                if emitted or isinstance(exc, REQUEST_ERRORS):
                    raise
                last_error = exc
                logger.warning(
                    "llm: candidato %s descartado (%s)",
                    client_label(client),
                    type(exc).__name__,
                )
            except Exception:
                breaker.record_failure()
                raise
            except BaseException:
                # CancelledError / GeneratorExit: no es fallo del proveedor, pero
                # libera la prueba HALF_OPEN para que no quede colgada.
                breaker.cancel_probe()
                raise
            else:
                breaker.record_success()
                return
        if last_error is not None:
            raise last_error

    def _stream_candidates(self, candidates: list[LLMClient]) -> Iterator[LLMClient]:
        """Candidatos de ``stream`` en orden, pidiendo ``allow()`` al llegar a cada uno.

        Si ninguno lo permite, da el primero igual (best-effort): el streaming no
        degrada, asi que es preferible intentar antes que no devolver nada.
        Mientras haya alguno disponible, se prefiere ese para no martillar una
        instancia caida.
        """
        allowed_any = False
        for client in candidates:
            if self._breakers[id(client)].allow():
                allowed_any = True
                yield client
        if not allowed_any:
            yield candidates[0]
