"""Tool loop del router LLM (M8 Ola 1).

Ejecuta iteraciones de llamada al LLM + ejecucion de tools hasta que el
modelo termina (finish_reason in {'stop','length','degraded','content_filter'}) o se agotan
las iteraciones del guard (MAX_TOOL_ITERATIONS).

Notas y limitaciones conocidas:
- El ``ChatMessage`` de assistant SI preserva ``tool_calls`` (M9): Chat Completions
  lo necesita para correlacionar cada respuesta ``role='tool'`` con su llamada
  (``tool_call_id``) en multi-turn. Con FakeLlmClient es inocuo; la correlacion
  contra cada proveedor real todavia no esta validada E2E.
- No hay historial multi-turno persistido: cada llamada a ``run_tool_loop``
  parte de los mensajes que recibe (system + user). El historial vivo de la
  sesion sigue siendo trabajo posterior (no lo entrega M9).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from app.llm.schemas import ChatMessage, ToolCall, ToolSpec
from app.llm.tools.base import tool_error
from app.llm.tools.registry import ToolRegistry

MAX_TOOL_ITERATIONS: int = 5

# Tope defensivo de tool_calls a ejecutar por turno. Si el modelo emite mas, se
# ejecutan solo las primeras ``MAX_CALLS_PER_TURN``; las extra no se ejecutan y reciben
# un ``tool_error`` (ADR-031 D5: el turno del assistant queda con TODAS sus calls, como
# lo devolvio el proveedor, y cada una necesita su resultado). Acota el fan-out de un
# turno patologico (modelo en loop emitiendo decenas de calls) sin cambiar el camino
# feliz, donde un turno trae pocas calls.
MAX_CALLS_PER_TURN: int = 8

# Tope de bytes del JSON de UN resultado de tool antes de inyectarlo en el historial de
# mensajes del LLM. Defensa en profundidad sobre el cap por-tool (las list tools ya acotan
# filas con ``AGENT_LIST_RESULT_LIMIT``): cualquier resultado anormalmente grande (una tool
# futura sin cap, o un dict patologico) NO inunda el context window. Si se excede, el
# resultado real se reemplaza por un ``tool_error`` estructurado (el modelo ve "el resultado
# era demasiado grande", no el payload). El cap es generoso (32KB) para no truncar listas
# legitimas dentro del ``AGENT_LIST_RESULT_LIMIT``.
TOOL_RESULT_MAX_BYTES: int = 32_768

# El modelo rechazo responder (``refusal`` de la Messages API, ADR-031 D7). Corta la
# vuelta, no dispara la confirmacion forzada (se rechazaria igual) y su texto parcial y
# su razonamiento se descartan.
_REFUSAL_REASON = "content_filter"

# finish_reason que indica que el loop debe terminar (con o sin tool_calls).
_TERMINAL_REASONS = frozenset({"stop", "length", "degraded", _REFUSAL_REASON})

# Ack determinista cuando el modelo EJECUTO tools pero no devolvio texto en lenguaje
# natural (ni siquiera tras la completion forzada): el usuario no debe ver el
# ``fallback_text`` generico (lee como error) tras una accion exitosa. Solo se usa si
# hay ``actions``; una respuesta vacia SIN acciones cae al ``fallback_text``.
_ACTIONS_DONE_TEXT: str = "Listo, hecho."


@dataclass(frozen=True)
class ToolLoopResult:
    """Resultado inmutable del tool loop (reemplaza la 4-tupla mixta previa).

    Contenedor con campos nombrados (coding-style del repo) en vez de una tupla
    posicional:

    - ``text``: respuesta final (nunca vacia): el texto del modelo; si hubo tools sin
      texto y el turno corto limpio, ``_ACTIONS_DONE_TEXT``; si no, ``fallback_text``.
    - ``actions``: tools ejecutadas, en orden, como dicts
      ``{'id', 'name', 'arguments', 'result'}``.
    - ``finish_reason``: ``finish_reason`` del ultimo ``CompletionResult`` procesado,
      o ``'max_iterations'`` si se agoto el guard sin converger.
    - ``reasoning``: razonamiento del modelo (canal ``reasoning`` SEPARADO de cada
      ``CompletionResult``) acumulado y concatenado a lo largo de TODAS las iteraciones
      del loop, igual que ``actions``. ``None`` si ninguna iteracion expuso razonamiento
      (thinking off, o modelo sin canal de razonamiento). Aditivo: los consumidores que
      lo ignoran no cambian.
    """

    text: str
    actions: list[dict[str, object]]
    finish_reason: str
    reasoning: str | None = None


async def _execute_anywhere(
    name: str,
    args: dict[str, object],
    registries: tuple[ToolRegistry, ToolRegistry | None],
) -> dict[str, object]:
    """Busca ``name`` en cada registry no-None y ejecuta la primera que lo tenga.

    Si ninguna lo tiene, devuelve ``tool_error('unknown_tool', ...)``.
    La ejecucion ya esta blindada en ``ToolRegistry.execute``: nunca propaga
    excepcion, devuelve ``tool_error('execution_error', ...)`` si falla.

    Args:
        name: Nombre de la tool (``namespace.action``).
        args: Argumentos ya parseados de la tool call.
        registries: Tupla (default_registry, memory_registry|None).

    Returns:
        Dict de resultado de la tool (o de error estructurado).
    """
    for reg in registries:
        if reg is not None and reg.has(name):
            return await reg.execute(name, args)
    return tool_error("unknown_tool", f"tool desconocida: {name}")


def _tool_message(call: ToolCall, result: dict[str, object]) -> ChatMessage:
    """El ``ChatMessage(role='tool')`` que se inyecta al historial para ``call``.

    Aplica el tope de bytes (un resultado demasiado grande se reemplaza por un
    ``tool_error``) y marca ``is_error`` si lo que se inyecta tiene la forma de
    ``tool_error`` (ADR-031 D5): el cliente de Anthropic lo manda como un
    ``tool_result`` con ``is_error``.
    """
    injected = result
    content = json.dumps(injected, ensure_ascii=False)
    if len(content.encode("utf-8")) > TOOL_RESULT_MAX_BYTES:
        injected = tool_error("result_too_large", f"resultado de {call.name} demasiado grande")
        content = json.dumps(injected, ensure_ascii=False)
    return ChatMessage(
        role="tool",
        tool_call_id=call.id,
        name=call.name,
        content=content,
        # La forma de ``tool_error``: ``{"error": {...}}``. Un resultado exitoso que trae
        # una clave ``error`` en ``None`` no cuenta.
        is_error=isinstance(injected.get("error"), dict),
    )


async def run_tool_loop(
    *,
    llm_client: object,
    model: str,
    messages: list[ChatMessage],
    specs: list[ToolSpec],
    registries: tuple[ToolRegistry, ToolRegistry | None],
    max_iterations: int = MAX_TOOL_ITERATIONS,
    thinking: bool | None = None,
    fallback_text: str,
) -> ToolLoopResult:
    """Loop principal de inferencia + ejecucion de tools.

    Por cada iteracion:
    1. Llama a ``llm_client.complete(model=model, messages=messages,
       tools=specs or None)``.
    2. Termina si no hay tool_calls O si finish_reason es terminal
       (``stop`` / ``length`` / ``degraded`` / ``content_filter``). Un rechazo
       (``content_filter``) descarta el texto y el razonamiento de esa llamada.
    3. Si hay tool_calls: agrega un ChatMessage(role='assistant') al historial
       con ``tool_calls`` preservado, ejecuta cada tool via
       ``_execute_anywhere``, acumula en ``actions`` y agrega un
       ChatMessage(role='tool') por cada resultado.
    4. Al agotar ``max_iterations`` sin converger, usa el ultimo ``result.text``
       como texto final; si esta vacio, fuerza UNA completion final con las mismas
       tools y ``tool_choice="none"`` (no puede llamarlas) para que el modelo
       responda en lenguaje natural (en vez de seguir tool-calleando) y
       el usuario vea una respuesta real, no el ``fallback_text`` generico. Si esa
       completion forzada tambien vuelve vacia: ``_ACTIONS_DONE_TEXT`` (ack) cuando hubo una
       tool EXITOSA y el turno corto limpio, y recien sin eso, ``fallback_text``.
       finish_reason reportado sera ``'max_iterations'`` (parada forzada por el guard;
       un sentinel honesto para telemetria, no se confunde con un ``'stop'`` real).

    Args:
        llm_client: Implementacion de ``LLMClient`` (real o fake).
        model: Modelo que se le pide a ``llm_client.complete``: el del modo, que
            resuelve el caller desde la config (decision #5).
        messages: Historial inicial (system + user actual). Se muta con los
            turnos del tool loop; el caller puede pasar una copia si necesita
            preservar el original.
        specs: ``ToolSpec`` a pasar al modelo; ``None`` si la lista esta vacia
            (el alias conversacional no ve tools).
        registries: Tupla ``(default_registry, memory_registry|None)`` de
            ``MemoryContext.registries``.
        max_iterations: Guard maximo de iteraciones (default ``MAX_TOOL_ITERATIONS``).
        thinking: Intencion de razonamiento (ADR-028 D3): ``True`` razonar, ``False``
            no razonar, ``None`` sin preferencia. Passthrough puro hacia
            ``llm_client.complete``, que la traduce a ``reasoning_effort`` solo si el
            proveedor lo soporta; lo decide el router por rol.
        fallback_text: Texto de fallback si el texto final esta vacio.

    Returns:
        Un ``ToolLoopResult`` (inmutable) con ``text``, ``actions``, ``finish_reason``
        y ``reasoning`` (ver el docstring del dataclass). El ``reasoning`` acumula el
        canal de razonamiento separado de cada iteracion del loop (concatenado), igual
        que ``actions`` acumula las tools ejecutadas; ``None`` si ninguna iteracion
        expuso razonamiento.
    """
    actions: list[dict[str, object]] = []
    tool_specs: list[ToolSpec] | None = specs if specs else None

    last_text: str = ""
    last_finish_reason: str = "stop"
    # Razonamiento acumulado de TODAS las iteraciones (patron ``reasoning_parts`` del
    # playground): cada ``CompletionResult`` trae el canal de razonamiento SEPARADO del
    # ``content``; se concatena al final. Hoy el router lo descartaba; ahora se devuelve
    # para que el endpoint pueda re-trocearlo como evento SSE ``reasoning``.
    reasoning_parts: list[str] = []

    for _ in range(max_iterations):
        result = await llm_client.complete(
            model=model,
            messages=messages,
            tools=tool_specs,
            thinking=thinking,
        )
        last_finish_reason = result.finish_reason
        if result.finish_reason == _REFUSAL_REASON:
            # Rechazo: ni el texto parcial ni el razonamiento de esta llamada se usan.
            last_text = ""
            break
        last_text = result.text
        if result.reasoning:
            reasoning_parts.append(result.reasoning)

        # Sin tool_calls o finish_reason terminal -> terminar
        if not result.tool_calls or result.finish_reason in _TERMINAL_REASONS:
            break

        # Cap defensivo: ejecutar a lo sumo MAX_CALLS_PER_TURN por turno. El assistant
        # message lleva TODAS las calls y la respuesta cruda del proveedor sin tocar
        # (ADR-031 D5: el historial de la vuelta es solo agregado); las que pasan el tope
        # no se ejecutan, no entran en ``actions`` y reciben un ``tool_error``.
        calls_this_turn = list(result.tool_calls[:MAX_CALLS_PER_TURN])
        skipped_calls = list(result.tool_calls[MAX_CALLS_PER_TURN:])

        messages.append(
            ChatMessage(
                role="assistant",
                content=result.text or None,
                tool_calls=list(result.tool_calls),
                provider_blocks=result.provider_blocks,
            )
        )

        # Ejecutar cada tool call y acumular en actions + historial.
        tool_call: ToolCall
        for tool_call in calls_this_turn:
            tool_result = await _execute_anywhere(tool_call.name, tool_call.arguments, registries)
            actions.append(
                {
                    "id": tool_call.id,
                    "name": tool_call.name,
                    "arguments": tool_call.arguments,
                    "result": tool_result,
                }
            )
            # Cap de bytes ANTES de inyectar al historial del LLM (defensa en profundidad):
            # un resultado anormalmente grande NO inunda el context window.
            messages.append(_tool_message(tool_call, tool_result))
        for skipped in skipped_calls:
            not_run = tool_error("call_limit", "no se ejecutó: tope de llamadas por turno")
            messages.append(_tool_message(skipped, not_run))
    else:
        # Guard agotado sin converger: el modelo nunca produjo una respuesta final en
        # lenguaje natural (tipicamente loopeo llamando tools, p.ej. el modelo reintentando
        # un stub). Se marca el sentinel 'max_iterations' (honesto, no un 'stop' real). El
        # rescate del texto vacio se hace abajo, COMPARTIDO con el corte temprano.
        last_finish_reason = "max_iterations"

    # Rescate de respuesta vacia tras accionar tools (gotcha MEDIDO en E2E con el modelo
    # local de antes de ADR-028; puede pasar con cualquier modelo): el modelo puede cortar
    # con finish_reason terminal ('stop') y content VACIO DESPUES de ejecutar una tool
    # (p.ej. agendar) — o agotar el guard. En ambos casos hay acciones
    # ejecutadas pero NO una confirmacion en lenguaje natural, y el usuario veria el
    # fallback generico (lee como error aunque la accion SI ocurrio). Forzamos UNA
    # completion final sin poder llamar tools (un unico shot -> no loopea) para
    # que el modelo confirme lo accionado. Si la forzada vuelve con texto, su
    # finish_reason real reemplaza al terminal-vacio / al sentinel 'max_iterations'
    # (telemetria honesta); si vuelve vacia, cae al fallback de abajo (no empeora el caso
    # previo). Cualquier LlmError de esta llamada propaga al caller (route), que degrada.
    # Se gatea en ``actions`` (no en ``not last_text`` a secas) para no forzar una segunda
    # vuelta ante una respuesta conversacional normal que legitimamente vino vacia.
    # Tras un rechazo no se fuerza: el modelo rechazaria igual (ADR-031 D7).
    if not last_text and actions and last_finish_reason != _REFUSAL_REASON:
        # thinking=False a proposito: la confirmacion no necesita razonar, y un modelo que
        # razona puede volcar el texto al canal de reasoning y dejar el content VACIO. Es
        # intencion, no un switch: cada cliente la traduce a lo que entiende su proveedor;
        # si el modelo razona igual y su texto queda solo en el reasoning, el turno cae al
        # ack o al fallback de abajo. Las tools van IGUALES pero con ``tool_choice="none"``
        # (ADR-031 D5): sacarlas cambiaria el prefijo que el proveedor controla.
        forced = await llm_client.complete(
            model=model,
            messages=messages,
            tools=tool_specs,
            thinking=False,
            tool_choice="none",
        )
        if forced.finish_reason == _REFUSAL_REASON:
            last_finish_reason = _REFUSAL_REASON
        else:
            last_text = forced.text
            if forced.reasoning:
                reasoning_parts.append(forced.reasoning)
            if last_text:
                last_finish_reason = forced.finish_reason

    # Texto final: el del modelo si vino. Si no: ack determinista cuando hubo una tool
    # EXITOSA y el turno corto LIMPIO (no se agoto el guard) — no mostrar el fallback-error
    # tras una accion exitosa que solo quedo sin confirmacion en lenguaje natural. Se
    # EXCLUYEN las acciones con error (``{"error": ...}``, ver tool_error): tras un fallo de
    # tool el ack mentiria exito, ahi se usa el fallback. En max_iterations (loop trabado) o
    # sin acciones exitosas, el ``fallback_text`` honesto.
    successful_actions = [a for a in actions if "error" not in a["result"]]
    if last_text:
        final_text = last_text
    elif successful_actions and last_finish_reason != "max_iterations":
        final_text = _ACTIONS_DONE_TEXT
    else:
        final_text = fallback_text
    reasoning = "".join(reasoning_parts) or None
    return ToolLoopResult(
        text=final_text,
        actions=actions,
        finish_reason=last_finish_reason,
        reasoning=reasoning,
    )
