#!/usr/bin/env python3
"""Prueba de humo de ``AnthropicClient`` contra la Messages API real (ADR-031).

Verifica lo que los tests con ``httpx.MockTransport`` no pueden: que la API acepta el
request tal como lo arma el cliente. Un caso por cada punto que quedó sin verificar:

- ``adaptativo``: el modo adaptativo con ``block_binding`` y su header beta.
- ``entre_tools``: ``between_tools`` sin tools y con ``max_tokens`` bajo.
- ``apagado``: el razonamiento apagado, con el modo de ``--apagado``.
- ``vuelta_con_tools``: una vuelta del tool loop real que reenvía los bloques firmados.
- ``confirmacion_forzada``: la confirmación con ``tool_choice="none"`` y
  ``thinking=False`` dentro de la misma vuelta.
- ``cambio_de_esfuerzo``: la misma confirmación con otro esfuerzo en el modo apagado,
  que el validador de ``LLM_PROVIDERS`` rechaza con ``disabled``. Dice si hace falta;
  antes corre un control con el mismo esfuerzo y solo concluye si ese pasa.
- ``streaming``: el modo adaptativo por SSE.
- ``cache``: dos llamadas con el mismo system fijo; la segunda tiene que leer la caché.

Los casos con tools solo concluyen si el modelo llamó a la tool y su primera respuesta
trajo razonamiento firmado: sin eso no hay bloques que reenviar. Por eso piden esfuerzo
alto y una cuenta antes de la tool: con un pedido trivial, el adaptativo no razona.

``--apagado`` elige cómo se apaga el razonamiento en todos los casos: ``disabled`` (el
default) o ``between_tools``, para los modelos que rechazan ``disabled`` con un 400.

Antes de los casos consulta ``GET /models``: si la credencial o la versión fallan, no
manda nada más.

Uso, desde ``apps/backend``::

    .venv/Scripts/python.exe scripts/check_anthropic.py --modelo <id>          # lista
    .venv/Scripts/python.exe scripts/check_anthropic.py --modelo <id> --enviar  # gasta

Sin ``--enviar`` solo lista los casos y no hace ningún request. ``--caso`` elige uno o
más casos. La credencial sale de ``LLM_API_KEY_<NOMBRE>`` (``--nombre``, por default
``claude``): del entorno o, si no está, del ``.env`` de la carpeta actual. No usa
``LLM_PROVIDERS`` ni ``Settings``. La base URL tiene que ser ``api.anthropic.com``
salvo ``--otro-host``, porque la credencial viaja a ese host.

Datos: todos los mensajes son constantes sintéticas de este archivo; no acepta texto
libre. Salida: el resultado de cada caso con su detalle (``finish_reason``, cantidad
de llamadas), los tokens y los tipos de bloque cuando los hay, y de cada request que
falló, el status, el tipo y el mensaje del error y el request-id: describen el request
sintético, no datos de nadie. Nunca imprime la credencial, el texto del modelo, el
razonamiento ni las firmas.

No corre en la CI. Los tests (``tests/scripts/test_check_anthropic.py``) usan
``httpx.MockTransport``, sin red.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

import httpx
from pydantic import SecretStr

# El script vive fuera del paquete ``app``: se suman ``apps/backend`` y esta carpeta.
_SCRIPTS_DIR = Path(__file__).resolve().parent
for _path in (_SCRIPTS_DIR.parent, _SCRIPTS_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from _smoke_support import (  # noqa: E402
    FAILED,
    INCONCLUSIVE,
    OFF_TYPES,
    OK,
    SIGNED_THINKING,
    CaseResult,
    EchoTool,
    OffType,
    Recorder,
    check_base_url,
    clean_reason,
    format_exchange,
    format_result,
    load_api_key,
    with_off_type,
)

from app.core.llm_providers import LLM_API_KEY_PREFIX, AliasThinking, ThinkingMode  # noqa: E402
from app.llm.clients.providers.anthropic import AnthropicClient  # noqa: E402
from app.llm.errors import LlmBadRequestError, LlmContextOverflowError, LlmError  # noqa: E402
from app.llm.schemas import ChatMessage, CompletionResult  # noqa: E402
from app.llm.tool_loop import ToolLoopResult, run_tool_loop  # noqa: E402
from app.llm.tools.base import to_spec  # noqa: E402
from app.llm.tools.registry import ToolRegistry  # noqa: E402

DEFAULT_BASE_URL = "https://api.anthropic.com/v1"
DEFAULT_NAME = "claude"

_ALIAS = "agente"
_FLOOR = 1024
_TOOL_FLOOR = 2048

# Modos de razonamiento propios de cada caso: no dependen de la config del operador.
_ADAPTIVE = AliasThinking(
    on=ThinkingMode(type="adaptive", effort="low"),
    off=ThinkingMode(type="disabled", effort="low"),
    max_tokens_floor=_FLOOR,
)
_BETWEEN_TOOLS = AliasThinking(
    on=ThinkingMode(type="between_tools", effort="low"),
    off=ThinkingMode(type="between_tools", effort="low"),
)
_DISABLED = AliasThinking(on=ThinkingMode(type="disabled"), off=ThinkingMode(type="disabled"))
# Los casos con tools: esfuerzo alto, para que el adaptativo razone antes de la tool.
_TOOLS = AliasThinking(
    on=ThinkingMode(type="adaptive", effort="high"),
    off=ThinkingMode(type="disabled", effort="high"),
    max_tokens_floor=_TOOL_FLOOR,
)
# El validador rechaza un esfuerzo distinto entre on y off con off disabled; este caso
# existe para ver si la API también lo rechaza, así que saltea el validador a propósito.
_EFFORT_CHANGE = AliasThinking.model_construct(
    on=ThinkingMode(type="adaptive", effort="high"),
    off=ThinkingMode(type="disabled", effort="low"),
    max_tokens_floor=_TOOL_FLOOR,
)

_REPLY_PROMPT = "Respondé solo con la palabra: listo."
_SUM = "Calculá la suma de los números primos menores que 50 y llamá a la tool de eco"
_TOOL_ROUND_PROMPT = (
    f"{_SUM} con ese número como texto. Cuando tengas su respuesta, respondé solo con la "
    "palabra: listo."
)
_TOOL_ONLY_PROMPT = f"{_SUM} con ese número como texto. No escribas texto: solo la tool."
# System fijo largo, para pasar el mínimo de tokens que la API pide para cachear.
_FIXED_SYSTEM = "Sos un asistente de prueba. Estas reglas son sintéticas.\n" + "\n".join(
    f"Regla de prueba {i}: respondé corto, en español y sin listas." for i in range(1, 401)
)
_VARIABLE_SYSTEM = "Fecha de prueba: 2026-01-01."
_FALLBACK = "sin respuesta"


ClientFactory = Callable[[AliasThinking, Recorder], AnthropicClient]


def api_client_factory(
    *, base_url: str, api_key: SecretStr, model_id: str, name: str, off_type: OffType
) -> ClientFactory:
    """Un ``AnthropicClient`` por caso, con el razonamiento del caso y el ``Recorder``."""

    def make(thinking: AliasThinking, recorder: Recorder) -> AnthropicClient:
        return AnthropicClient(
            name=name,
            base_url=base_url,
            api_key=api_key,
            model_map={_ALIAS: model_id},
            thinking={_ALIAS: with_off_type(thinking, off_type)},
            http_client=httpx.AsyncClient(event_hooks={"response": [recorder]}),
            default_timeout_s=60.0,
        )

    return make


def _usage(result: CompletionResult) -> dict[str, int]:
    return {
        "entrada": result.prompt_tokens,
        "salida": result.completion_tokens,
        "cache_leida": result.cache_read_tokens,
        "cache_creada": result.cache_creation_tokens,
    }


async def _with_client[T](
    make: ClientFactory,
    thinking: AliasThinking,
    action: Callable[[AnthropicClient], Awaitable[T]],
) -> tuple[T | None, LlmError | None, Recorder]:
    recorder = Recorder()
    client = make(thinking, recorder)
    try:
        return await action(client), None, recorder
    except LlmError as exc:
        return None, exc, recorder
    finally:
        await client.aclose()


def _failure(name: str, exc: LlmError | None, recorder: Recorder, hint: str = "") -> CaseResult:
    """Un caso que no llegó a resultado: el error del proveedor y la pista, si hay."""
    detail = (type(exc).__name__ if exc is not None else "sin resultado") + (
        f"; {hint}" if hint else ""
    )
    return CaseResult(name, FAILED, detail, exchanges=recorder.exchanges)


def _user(text: str) -> list[ChatMessage]:
    return [ChatMessage(role="user", content=text)]


async def _single_completion(
    make: ClientFactory, name: str, thinking: AliasThinking, max_tokens: int, hint: str = ""
) -> CaseResult:
    result, exc, recorder = await _with_client(
        make,
        thinking,
        lambda c: c.complete(model=_ALIAS, messages=_user(_REPLY_PROMPT), max_tokens=max_tokens),
    )
    if exc is not None or result is None:
        return _failure(name, exc, recorder, hint)
    reason = clean_reason(result.finish_reason)
    outcome = OK if reason == "stop" else INCONCLUSIVE
    return CaseResult(
        name,
        outcome,
        f"finish_reason {reason}",
        finish_reason=reason,
        usage=_usage(result),
        block_types=recorder.posts[0].block_types,
        exchanges=recorder.exchanges,
    )


async def case_adaptive(make: ClientFactory) -> CaseResult:
    return await _single_completion(make, "adaptativo", _ADAPTIVE, max_tokens=256)


async def case_between_tools(make: ClientFactory) -> CaseResult:
    return await _single_completion(make, "entre_tools", _BETWEEN_TOOLS, max_tokens=512)


async def case_disabled(make: ClientFactory) -> CaseResult:
    hint = "si rechaza este modo, el 400 dice cuál acepta (--apagado); si no, off va en adaptive"
    return await _single_completion(make, "apagado", _DISABLED, max_tokens=256, hint=hint)


async def _tool_loop(
    make: ClientFactory, thinking: AliasThinking, prompt: str, max_iterations: int
) -> tuple[ToolLoopResult | None, LlmError | None, Recorder]:
    registry = ToolRegistry([EchoTool()])
    specs = [to_spec(tool) for tool in registry.tools()]
    return await _with_client(
        make,
        thinking,
        lambda c: run_tool_loop(
            llm_client=c,
            model=_ALIAS,
            messages=_user(prompt),
            specs=specs,
            registries=(registry, None),
            max_iterations=max_iterations,
            thinking=True,
            fallback_text=_FALLBACK,
        ),
    )


def _unproven_round(name: str, recorder: Recorder, called_tool: bool) -> CaseResult | None:
    """``NO CONCLUYE`` si la vuelta no tuvo una tool llamada con razonamiento firmado."""
    first = recorder.posts[0].block_types if recorder.posts else []
    if not called_tool:
        detail = "el modelo no llamó a la tool"
    elif SIGNED_THINKING not in first:
        detail = "la primera respuesta no trajo razonamiento firmado: no hubo bloques que reenviar"
    else:
        return None
    return CaseResult(name, INCONCLUSIVE, detail, block_types=first, exchanges=recorder.exchanges)


async def case_tool_round(make: ClientFactory) -> CaseResult:
    name = "vuelta_con_tools"
    loop, exc, recorder = await _tool_loop(make, _TOOLS, _TOOL_ROUND_PROMPT, max_iterations=3)
    if exc is not None or loop is None:
        return _failure(name, exc, recorder)
    unproven = _unproven_round(name, recorder, called_tool=bool(loop.actions))
    if unproven is not None:
        return unproven
    reason = clean_reason(loop.finish_reason)
    outcome = OK if reason == "stop" else INCONCLUSIVE
    calls = len(recorder.posts)
    detail = f"{len(loop.actions)} tool, {calls} llamadas, finish_reason {reason}"
    return CaseResult(
        name,
        outcome,
        detail,
        finish_reason=reason,
        block_types=recorder.posts[0].block_types,
        exchanges=recorder.exchanges,
    )


def _rejected_on_confirmation(exc: LlmError, recorder: Recorder) -> bool:
    """La primera llamada pasó y la confirmación volvió con un 400 de request inválido.

    El contexto excedido también es un 400, pero no dice nada del esfuerzo.
    """
    posts = recorder.posts
    return (
        isinstance(exc, LlmBadRequestError)
        and not isinstance(exc, LlmContextOverflowError)
        and len(posts) >= 2
        and posts[0].status == httpx.codes.OK
        and posts[1].status == httpx.codes.BAD_REQUEST
    )


async def _forced(
    make: ClientFactory, name: str, thinking: AliasThinking, *, probe: bool
) -> CaseResult:
    """Una vuelta de una iteración: si el modelo solo llama a la tool, hay confirmación.

    ``probe``: el caso prueba una regla del validador. Un 400 de request inválido en la
    confirmación es una respuesta (la regla queda); cualquier otro error es una falla.
    """
    loop, exc, recorder = await _tool_loop(make, thinking, _TOOL_ONLY_PROMPT, max_iterations=1)
    first = recorder.posts[0].block_types if recorder.posts else []
    if exc is not None or loop is None:
        if probe and exc is not None and SIGNED_THINKING in first:
            rejected = _rejected_on_confirmation(exc, recorder)
        else:
            rejected = False
        if rejected:
            detail = "la API rechaza el cambio de esfuerzo (400): la regla queda"
            return CaseResult(name, OK, detail, block_types=first, exchanges=recorder.exchanges)
        return _failure(name, exc, recorder)
    unproven = _unproven_round(name, recorder, called_tool=bool(loop.actions))
    if unproven is not None:
        return unproven
    if len(recorder.posts) < 2:
        detail = "el modelo escribió texto: no hubo confirmación forzada"
        exchanges = recorder.exchanges
        return CaseResult(name, INCONCLUSIVE, detail, block_types=first, exchanges=exchanges)
    reason = clean_reason(loop.finish_reason)
    if probe:
        detail = "la API acepta el cambio de esfuerzo: la regla se puede relajar"
    else:
        detail = f"confirmación forzada aceptada, finish_reason {reason}"
    exchanges = recorder.exchanges
    return CaseResult(name, OK, detail, reason, block_types=first, exchanges=exchanges)


async def case_forced_confirmation(make: ClientFactory) -> CaseResult:
    return await _forced(make, "confirmacion_forzada", _TOOLS, probe=False)


async def case_effort_change(make: ClientFactory) -> CaseResult:
    """Primero un control con el mismo esfuerzo; la sonda solo cuenta si el control pasa.

    Sin el control, un 400 que no tiene que ver con el esfuerzo (por ejemplo, de la
    confirmación misma) se leería como «la regla queda».
    """
    name = "cambio_de_esfuerzo"
    control = await _forced(make, name, _TOOLS, probe=False)
    if control.outcome != OK:
        why = f"{control.outcome}: {control.detail}"
        detail = f"el control con el mismo esfuerzo no pasó ({why}): no concluye"
        exchanges = control.exchanges
        return CaseResult(
            name, INCONCLUSIVE, detail, block_types=control.block_types, exchanges=exchanges
        )
    return await _forced(make, name, _EFFORT_CHANGE, probe=True)


async def case_streaming(make: ClientFactory) -> CaseResult:
    name = "streaming"

    async def consume(client: AnthropicClient) -> tuple[int, str | None]:
        chunks, finish = 0, None
        async for chunk in client.stream(
            model=_ALIAS, messages=_user(_REPLY_PROMPT), max_tokens=256
        ):
            chunks += 1
            finish = chunk.finish_reason or finish
        return chunks, finish

    outcome, exc, recorder = await _with_client(make, _ADAPTIVE, consume)
    if exc is not None or outcome is None:
        return _failure(name, exc, recorder)
    chunks, raw_finish = outcome
    finish = clean_reason(raw_finish)
    result = OK if finish == "stop" else INCONCLUSIVE
    detail = f"{chunks} chunks, finish_reason {finish}"
    return CaseResult(name, result, detail, finish, exchanges=recorder.exchanges)


async def case_cache(make: ClientFactory) -> CaseResult:
    name = "cache"
    messages = [
        ChatMessage(role="system", content=_FIXED_SYSTEM),
        ChatMessage(role="system", content=_VARIABLE_SYSTEM),
        ChatMessage(role="user", content=_REPLY_PROMPT),
    ]

    async def twice(client: AnthropicClient) -> tuple[CompletionResult, CompletionResult]:
        first = await client.complete(model=_ALIAS, messages=messages, max_tokens=256)
        second = await client.complete(model=_ALIAS, messages=messages, max_tokens=256)
        return first, second

    pair, exc, recorder = await _with_client(make, _ADAPTIVE, twice)
    if exc is not None or pair is None:
        return _failure(name, exc, recorder)
    first, second = pair
    created, read = first.cache_creation_tokens, second.cache_read_tokens
    if read > 0:
        outcome, detail = OK, f"la segunda llamada leyó {read} tokens de la caché"
    elif created == 0:
        outcome = INCONCLUSIVE
        detail = "ninguna llamada creó ni leyó caché (quizás el prefijo no llega al mínimo)"
    else:
        outcome, detail = FAILED, f"creó {created} tokens de caché y la segunda no los leyó"
    return CaseResult(name, outcome, detail, usage=_usage(second), exchanges=recorder.exchanges)


Case = Callable[[ClientFactory], Awaitable[CaseResult]]

CASES: dict[str, tuple[Case, str]] = {
    "adaptativo": (case_adaptive, "adaptive con block_binding y el header beta"),
    "entre_tools": (case_between_tools, "between_tools sin tools, max_tokens 512"),
    "apagado": (case_disabled, "el razonamiento apagado (--apagado)"),
    "vuelta_con_tools": (case_tool_round, "tool loop real con los bloques firmados reenviados"),
    "confirmacion_forzada": (case_forced_confirmation, "tool_choice none con thinking=False"),
    "cambio_de_esfuerzo": (case_effort_change, "otro esfuerzo en off: la regla del validador"),
    "streaming": (case_streaming, "adaptive por SSE"),
    "cache": (case_cache, "dos llamadas con el mismo system fijo"),
}


async def check_health(make: ClientFactory) -> tuple[bool, Recorder]:
    """``GET /models`` con la credencial; no gasta tokens."""
    recorder = Recorder()
    client = make(_ADAPTIVE, recorder)
    try:
        health = await client.health()
    finally:
        await client.aclose()
    return health.healthy, recorder


async def run_checks(
    make: ClientFactory, names: Sequence[str], out: Callable[[str], None] = print
) -> int:
    """Corre ``health`` y los casos pedidos. Devuelve 1 si alguno falla."""
    healthy, recorder = await check_health(make)
    if not healthy:
        out("[FALLA] health: GET /models no respondió 200; no se manda nada más")
        for exchange in recorder.exchanges:
            out(f"    {format_exchange(exchange)}")
        return 1
    out("[OK] health: GET /models respondió 200")
    failed = False
    for name in names:
        case, _ = CASES[name]
        result = await case(make)
        failed = failed or result.outcome == FAILED
        for line in format_result(result):
            out(line)
    return 1 if failed else 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prueba de humo del cliente de Anthropic.", allow_abbrev=False
    )
    parser.add_argument("--modelo", required=True, help="id del modelo en la API")
    parser.add_argument("--nombre", default=DEFAULT_NAME, help="nombre de la credencial")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument(
        "--otro-host", action="store_true", help="permite una base URL fuera de Anthropic"
    )
    parser.add_argument("--caso", action="append", choices=list(CASES), help="repetible")
    parser.add_argument(
        "--apagado", default="disabled", choices=OFF_TYPES, help="cómo se apaga el razonamiento"
    )
    parser.add_argument("--enviar", action="store_true", help="manda los requests (gasta tokens)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    names = args.caso or list(CASES)
    if not args.enviar:
        print(f"Casos contra {args.base_url} con {args.modelo} (no se mandó nada):")
        print(f"  modo apagado: {args.apagado}")
        for name in names:
            print(f"  - {name}: {CASES[name][1]}")
        print("Para mandarlos, sumá --enviar.")
        return 0
    check_base_url(args.base_url, allow_other_host=args.otro_host)
    variable = f"{LLM_API_KEY_PREFIX}{args.nombre.upper()}"
    make = api_client_factory(
        base_url=args.base_url,
        api_key=load_api_key(variable, Path.cwd() / ".env"),
        model_id=args.modelo,
        name=args.nombre,
        off_type=args.apagado,
    )
    return asyncio.run(run_checks(make, names))


if __name__ == "__main__":
    sys.exit(main())
