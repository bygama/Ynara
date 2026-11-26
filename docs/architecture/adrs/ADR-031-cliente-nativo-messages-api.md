# ADR-031: Cliente nativo de la Messages API de Anthropic

## Estado

Propuesto

## Fecha

2026-11-26

## Contexto

[ADR-028](./ADR-028-inferencia-por-api.md) fijó que la inferencia se consume por API,
con proveedores compatibles con OpenAI en una lista ordenada (`LLM_PROVIDERS`), y dejó
dicho que un proveedor que no hable ese protocolo lleva su propio cliente en
`app/llm/clients/providers/`, con un ADR propio. Este es ese ADR.

El equipo eligió a Anthropic (Claude) como proveedor principal del chat. Anthropic
ofrece una capa compatible con OpenAI, pero no sirve para producción:

- La propia documentación la presenta como una capa para probar, no para producción.
- No tiene caché de prompts, ignora `reasoning_effort` y en los modelos actuales
  devuelve un 400 con la `temperature` que manda el cliente de hoy.

La API nativa es la Messages API (`POST /v1/messages`). Difiere de Chat Completions en
puntos que tocan el contrato interno del backend:

- **Razonamiento.** En los modelos actuales el razonamiento es adaptativo
  (`thinking: {type: "adaptive"}`) y su intensidad se pide con un esfuerzo
  (`effort`). Apagarlo cambia según el modelo: uno acepta `{type: "disabled"}`, otro
  lo rechaza con un 400 y ofrece `between_tools` como mínimo, y otro no tiene forma
  de apagarlo. Los tokens de razonamiento cuentan dentro de `max_tokens`, y el texto
  del razonamiento no viene por defecto: hay que pedir el resumen.
- **Parámetros de muestreo.** Con la versión de API `2026-10-21`, `temperature`,
  `top_p` y `top_k` devuelven un 400 en cualquier modelo.
- **Bloques de razonamiento.** La respuesta puede traer bloques `thinking` antes del
  texto o de las llamadas a tools. Dentro de una vuelta con tools hay que devolverlos
  sin modificar junto con los resultados. En los modelos actuales, y por defecto en
  las cuentas nuevas, la API controla además que el historial que se reenvía sea solo
  un agregado al anterior: si cambian el system, las tools o un mensaje previo y se
  reenvía un bloque de razonamiento, responde con un 400.
- **Atadura a la cuenta.** Un bloque de razonamiento solo vale en la cuenta de
  Anthropic que lo produjo (o en una vinculada); en otra se descarta en silencio. La
  caché de prompts tampoco se comparte entre cuentas.
- **Caché de prompts explícita.** Se marca con `cache_control` en el último bloque del
  prefijo estable; el orden de render es tools → system → mensajes.
- **Errores propios.** 402 (facturación), 413 (request demasiado grande), 529
  (sobrecarga), un 429 por tope de gasto que no se resuelve reintentando, y un 400
  con el texto `prompt is too long` para el contexto excedido.
- **Streaming propio.** Eventos SSE con tipos (`content_block_delta`, `message_delta`,
  etc.), no los `chunks` de Chat Completions.
- **Tools.** `tool_choice` forzado (`any`/`tool`) devuelve un 400 en algunos modelos;
  `auto` y `none` funcionan. Cada `tool_use` necesita su `tool_result` en el mensaje
  siguiente, y todos los resultados de una vuelta van juntos en un solo mensaje.

El contrato interno de hoy (`LLMClient`, `ChatMessage`, `CompletionResult`) se pensó
para Chat Completions: no tiene dónde guardar bloques opacos del proveedor, manda
`temperature` siempre y arma el system en un solo string.

## Decisión

### D1 — Cliente nativo con httpx, sin SDK

`AnthropicClient`, en `app/llm/clients/providers/anthropic.py`, implementa el
protocolo `LLMClient` (`complete`, `stream`, `health`, `serves_model`) y el `aclose`
que el pool busca en cada cliente, sobre `httpx`, que ya es dependencia. No se agrega
el SDK oficial (ADR-028 D1: un solo cliente HTTP propio, sin dependencias nuevas).

- Autenticación con el header `x-api-key`, en una clase de auth propia que, igual que
  `BearerAuth`, nunca muestra la credencial en `repr`, logs ni errores.
- `anthropic-version: 2026-10-21`, fijo en una constante del módulo. Subir de versión
  es un cambio de código con su PR, no una variable de entorno.
- Reusa lo que ya es común a los proveedores: el mapeo de estados HTTP
  (`_http.py`), el mapeo de nombres de tools con punto a `__` (`_tool_names.py`) y
  la taxonomía `LlmError`.

### D2 — `protocol` en cada entrada de `LLM_PROVIDERS`

`LlmProvider` suma el campo `protocol: "openai" | "anthropic"`, con `openai` por
default para no romper las entradas existentes. `build_llm_client` elige el cliente
por ese campo.

- Para Anthropic, `base_url` es `https://api.anthropic.com/v1`.
- La credencial sigue en `LLM_API_KEY_<NAME>` (ADR-028 D2), sin cambios.
- `supports_reasoning_effort` no aplica a `anthropic`: el validador rechaza `true` en
  esa combinación para que la config no prometa algo que el cliente ignora.
- La configuración del razonamiento (D3), el piso de `max_tokens` (D4) y el timeout
  (D9) van en la misma entrada.

### D3 — Razonamiento: un modo configurado por alias y por intención

El router no cambia: sigue pidiendo `thinking=False` para el rol conversacional y
`True` para el agente, y el motor de memoria sigue pidiendo `False`. Lo que cambia es
cómo traduce eso el cliente. Cada entrada `anthropic` configura, por alias, qué modo
manda para `True` y cuál para `False`. Hay tres modos:

| Modo | Qué manda el cliente |
| --- | --- |
| adaptativo | `thinking: {type: "adaptive", display: "summarized"}` y un esfuerzo |
| `disabled` | `thinking: {type: "disabled"}`, sin `display` |
| `between_tools` | `thinking: {type: "between_tools"}`, sin ningún otro campo |

- `thinking=None` usa el modo configurado para `True`.
- El modo depende del modelo y no se puede deducir del id, por eso es config. Un
  modelo que no se puede apagar lleva el modo adaptativo con esfuerzo mínimo también
  para `False`.
- El validador rechaza `disabled` y `between_tools` junto con un esfuerzo `xhigh` o
  `max` (la API da un 400 en esa combinación).
- `display: "summarized"` hace falta para que `reasoning` (y el evento SSE
  `reasoning` de ADR-028 D9) tenga texto: por defecto la API manda el bloque sin
  texto, solo con su firma.
- Los valores son **provisorios** hasta calibrarlos con el set de evals. Punto de
  partida, siguiendo la recomendación del proveedor para chat: `conversacional`,
  adaptativo con esfuerzo bajo para las dos intenciones; `agente`, adaptativo con
  esfuerzo medio para `True` y el modo apagado del modelo para `False`.
- El cliente **no manda** `temperature`, `top_p` ni `top_k`. El argumento
  `temperature` del contrato se ignora para este protocolo; los llamadores que hoy lo
  bajan para extraer o resumir (el motor de memoria) dependen del razonamiento apagado
  y del prompt, no de la temperatura.

### D4 — `max_tokens` con margen para el razonamiento

Como el razonamiento cuenta dentro de `max_tokens`, cuando el modo que sale en el
request es el adaptativo (lo haya pedido el llamador con `True` o lo imponga la config
para `False`), el cliente usa el mayor entre lo que pide el llamador y un piso
configurado por alias. Con `disabled` o `between_tools` respeta lo que pide el
llamador. Así, un modelo que no se puede apagar no se come los 512 tokens que pide el
motor de memoria.

### D5 — Bloques opacos del proveedor en el historial de la vuelta

`ChatMessage` y `CompletionResult` suman un campo opcional para el contenido crudo del
proveedor: `provider_blocks`, con el nombre de la entrada de `LLM_PROVIDERS` que lo
produjo y la lista de bloques tal como llegaron.

- El tool loop copia `provider_blocks` del resultado al mensaje `assistant` que
  agrega al historial, sin tocarlo, y ese mensaje lleva en `tool_calls` **todas** las
  llamadas del modelo.
- Un cliente reenvía esos bloques sin modificar (mismos bloques, mismo orden, mismos
  campos, incluidos los bloques de razonamiento sin texto) solo si los produjo una
  entrada con su mismo nombre. Si no (el pool cambió de entrada a mitad de la vuelta),
  arma los `tool_use` desde `tool_calls` y no reenvía razonamiento: el request sigue
  siendo válido y solo se pierde el razonamiento previo. Esto es lo que hace seguro
  tener dos entradas de Anthropic con credenciales de cuentas distintas (D10).
- Los bloques no se persisten: el historial guardado entre turnos sigue siendo texto
  plano, así que el razonamiento no cruza de un turno al siguiente.
- El cliente OpenAI ignora el campo.
- Dentro de la vuelta, el historial es solo agregado: nada se edita ni se reordena.

Dos ajustes del tool loop salen de esto:

- **Llamadas descartadas por el tope.** Hoy el loop ejecuta a lo sumo
  `MAX_CALLS_PER_TURN` llamadas y deja fuera las demás del mensaje `assistant`. Con
  los bloques reenviados tal cual, las descartadas seguirían ahí sin su resultado. El
  loop pasa a responderlas con un `tool_error` («no se ejecutó: tope de llamadas por
  turno») en vez de sacarlas. El cliente de Anthropic manda todo `tool_error` como
  `tool_result` con `is_error: true`.
- **Confirmación forzada.** Cuando el loop termina con acciones y sin texto, hoy llama
  una vez más sin tools. Quitar las tools cambia el prefijo y, con bloques de
  razonamiento reenviados, da un 400. El contrato suma `tool_choice: "auto" | "none"`
  (default `auto`), y la confirmación manda las mismas tools con `none`. El cliente
  OpenAI lo traduce a su `tool_choice`. El modo de razonamiento y `tool_choice` no
  forman parte del prefijo que controla la API, así que pedir `thinking=False` en esa
  llamada no da un 400; sí invalida la caché de esa llamada, que es una sola.

Y uno del cliente: los mensajes `tool` consecutivos de `ChatMessage` se agrupan en un
solo mensaje `user` con todos los `tool_result`, como pide la API.

### D6 — Caché de prompts: un corte entre lo fijo y lo variable

El router ya arma el system con lo fijo primero (prompt del modo) y lo variable al
final (preámbulo de fecha y hora, bloque de memoria). Para que la caché lo aproveche,
el router pasa a mandar **dos** mensajes `system` en vez de uno: el fijo y el
variable.

- Con dos mensajes `system`, el cliente de Anthropic los manda como dos bloques del
  `system` y pone `cache_control` en el primero. Como las tools se renderizan antes,
  quedan dentro del prefijo cacheado.
- Con un solo mensaje `system` (motor de memoria, playground), el cliente no marca
  caché: no sabe si ese texto es estable, y escribir caché que nadie lee cuesta más
  que no cachear.
- El cliente OpenAI une los `system` consecutivos con `\n\n`: el texto que recibe el
  proveedor es el mismo que hoy.
- Un prefijo por debajo del mínimo cacheable del modelo no se cachea y no da error.
- Un segundo corte sobre el historial dentro de la vuelta con tools (o la caché
  automática que ofrece la API) se evalúa en la implementación, con las mediciones de
  costo.

### D7 — Errores y motivos de corte

Se reusa `raise_for_provider_status`, con dos agregados:

- **`prompt is too long`** se suma a las firmas de contexto excedido, así un 400 por
  ventana llega como `LlmContextOverflowError` y no como request inválido.
- **El 429 por tope de gasto** (`enforced_spend_limit_reached` en el body) pasa a
  `LlmProviderRejectedError`: reintentarlo no sirve hasta que alguien suba el tope, y
  el pool pasa a la entrada siguiente. Para leerlo, la lectura del código de error
  suma la forma del body de Anthropic, además de la de OpenAI.

El resto ya cae bien y no cambia:

- 401 y 403 son `LlmAuthError`; 402 es `LlmProviderRejectedError`. En los dos casos el
  pool prueba la entrada siguiente.
- 529 es un 5xx: `LlmUnavailableError`, con backoff y breaker, como cualquier caída.
- 413 es `LlmProviderRejectedError`. Pasar a otra entrada de Anthropic falla igual,
  porque el límite es de la API; solo un respaldo de otro proveedor podría atenderlo.

| `stop_reason` | `finish_reason` |
| --- | --- |
| `end_turn`, `stop_sequence` | `stop` |
| `max_tokens`, `model_context_window_exceeded` | `length` |
| `tool_use` | `tool_calls` |
| `refusal` | `content_filter` |
| otro | se informa tal cual y se loguea |

El modelo puede rechazar una respuesta (`refusal`) y la API no ofrece un respaldo
automático para eso. Hoy ningún llamador trata `content_filter` aparte: un rechazo
sin texto termina en el texto de respaldo genérico del tool loop. Qué ve el usuario
ante un rechazo, en especial en Bienestar, queda como pendiente explícito de la
compuerta 2 de ADR-028 (crisis).

### D8 — Uso de tokens

`prompt_tokens` es la suma de `input_tokens`, `cache_read_input_tokens` y
`cache_creation_input_tokens`: el contexto real que ocupó el request, que es lo que
necesita el presupuesto de contexto. `CompletionResult` informa además las tres partes
por separado para la medición de costo (compuerta 3 de ADR-028), porque cada una tiene
un precio distinto. `completion_tokens` es `output_tokens` e incluye el razonamiento.

### D9 — Streaming, salud y timeouts

- **Streaming.** El cliente parsea los eventos SSE de la Messages API:
  - `text_delta` va a `delta_text` y `thinking_delta` a `reasoning_delta`;
  - `signature_delta` se acumula para rearmar el bloque de razonamiento completo;
  - los `input_json_delta` se acumulan y la llamada completa sale como
    `tool_call_delta` al cerrar el bloque, en la forma que hoy consumen los llamadores;
  - `message_delta` trae el `stop_reason`;
  - un evento `error` a mitad del stream se mapea a la taxonomía `LlmError`.
- **Salud.** `health()` hace `GET /v1/models`: valida la credencial sin gastar tokens.
- **Timeouts.** El tool loop no usa streaming, y con el razonamiento prendido 20 s por
  request pueden quedar cortos. El timeout pasa a configurarse por entrada de
  `LLM_PROVIDERS`, con el default actual, y `check_resilience_budget` valida el peor
  caso con el timeout de cada entrada. Hoy ese peor caso (3 intentos de 20 s, backoff
  y un request al respaldo) da 82,5 s contra un presupuesto de 90 s: subir el timeout
  obliga a bajar los intentos o a subir el presupuesto en el mismo cambio. Se arranca
  con 20 s y se ajusta con el p95 medido (compuerta 5 de ADR-028).
- Un turno con tools puede hacer varias llamadas `complete` (una por vuelta más la
  confirmación), cada una con su propio presupuesto. Un request cortado por timeout
  puede cobrarse igual, así que los reintentos por timeout se miden en la compuerta de
  costo.

### D10 — Respaldo dentro de Anthropic

La lista puede tener varias entradas con `protocol: "anthropic"`, cada una con su
`name` y su `LLM_API_KEY_<NAME>`. Una segunda entrada con la credencial de otra cuenta
de Anthropic cubre una credencial revocada o vencida (401) y el saldo agotado (402).

- Usarla también para pasar por encima de los límites de uso o de gasto de la cuenta
  principal (429) queda sujeto a confirmar que los términos comerciales de Anthropic
  lo permiten. Hasta confirmarlo, no se cuenta como cobertura.
- No cubre una caída de Anthropic: las dos entradas dependen del mismo servicio. Para
  eso, la cadena termina en la respuesta degradada de ADR-028.
- Cada cuenta es un contrato aparte con Anthropic, así que la compuerta legal de
  ADR-028 (DPA y cláusulas modelo) se cumple por cuenta, no una vez.

## Punto abierto: la compuerta 4 de ADR-028

La compuerta 4 pide probar que «se corta el proveedor principal y responde el
respaldo». Con Anthropic como único proveedor del chat, esa compuerta **no se cumple
tal como está escrita**: una segunda entrada de Anthropic no responde si Anthropic se
cae. Este ADR no la redefine. Las opciones para el equipo son:

1. Mantenerla y sumar un respaldo de otro proveedor (una entrada `openai` ya
   soportada).
2. Enmendarla en un ADR que lo diga explícitamente: probar el corte de la credencial
   principal y, ante la caída del proveedor, la respuesta degradada.

Mientras se decide, el texto de ADR-028 no cambia y la compuerta sigue abierta.

## Consecuencias positivas

- El chat usa la API que el proveedor soporta para producción, con caché de prompts,
  razonamiento configurable y errores con sus códigos propios.
- Los llamadores no cambian de forma: siguen pidiendo un alias y reciben
  `CompletionResult` y `CompletionChunk`.
- El razonamiento dentro de una vuelta con tools se preserva, que es lo que la API
  necesita para que el modelo siga su propio plan entre llamadas.
- La caché del prefijo fijo baja el costo y la latencia de cada turno.
- Varias entradas de Anthropic funcionan sin código extra, gracias al pool existente.

## Consecuencias negativas

- Un segundo cliente que mantener, con su propio streaming y sus propios casos de
  error, y que hay que seguir cuando cambie la versión de la API.
- El contrato crece en varios lugares:
  - `ChatMessage` suma `provider_blocks`;
  - `CompletionResult` suma `provider_blocks` y el desglose de caché;
  - `LLMClient.complete` suma `tool_choice`;
  - `LlmProvider` suma `protocol`, los modos de razonamiento por alias, el piso de
    `max_tokens` y el timeout.
- El router (dos mensajes `system`) y el tool loop (llamadas descartadas,
  confirmación forzada) cambian de forma.
- `temperature` deja de tener efecto con este proveedor.
- Los modos, los esfuerzos y los pisos de `max_tokens` no se pueden fijar sin correr
  las evals contra el proveedor real, que cuesta tokens.
- Si el pool cambia de entrada a mitad de una vuelta, esa vuelta pierde el
  razonamiento previo y la caché.

## Mitigaciones

- Tests del cliente con respuestas armadas a mano (datos sintéticos, sin llamar al
  proveedor): parseo de bloques, mapeo de errores y de `stop_reason`, streaming,
  agrupado de resultados de tools y reenvío de bloques solo a la misma entrada.
- Un test del tool loop que verifica que el historial de la vuelta es solo agregado
  entre una llamada y la siguiente.
- La versión de API fija en una constante y su cambio, en su propio PR.
- Modos, esfuerzos, pisos y timeouts en la config, no en el código: se calibran sin
  tocar el cliente.

## Alternativas descartadas

- **SDK oficial de Anthropic.** Resuelve el parseo y el streaming, pero suma una
  dependencia y un segundo cliente HTTP, contra ADR-028 D1, y esconde detrás de sus
  tipos el control de qué se manda en cada request.
- **La capa compatible con OpenAI de Anthropic.** Reusa el cliente de hoy sin código
  nuevo, pero no es para producción, no tiene caché y no respeta el esfuerzo de
  razonamiento.
- **Apagar siempre el razonamiento.** Simplifica el historial, pero no todos los
  modelos lo permiten y en el agente perdería calidad justo donde más importa.
- **Persistir los bloques de razonamiento entre turnos.** Daría continuidad al
  razonamiento, pero obliga a guardar contenido opaco del proveedor en la base y a no
  editar nunca el system, que hoy cambia en cada turno (fecha, memoria).
- **Mandar lo variable como mensajes `system` a mitad de la conversación.** Permitiría
  cachear también el historial entre turnos, pero cambia cómo se guarda y se reenvía
  la conversación; queda para cuando las mediciones de costo lo pidan.
- **Un marcador dentro del string del system** en vez de dos mensajes. Evita tocar la
  forma del historial, pero mete un separador mágico en el texto que puede aparecer
  en un prompt.

## Relación con otros ADRs

- **Implementa** lo que [ADR-028](./ADR-028-inferencia-por-api.md) previó para un
  proveedor no compatible con OpenAI: cliente propio en `providers/` y ADR propio.
  Extiende ADR-028 D2 (campo `protocol`) y D3 (razonamiento por alias).
- **Plantea** una enmienda posible a la compuerta 4 de ADR-028, sin hacerla (ver
  «Punto abierto»).
- Mantiene [ADR-020](./ADR-020-circuit-breaker-cota-workers.md): el breaker sigue por
  entrada del pool, y un 529 lo abre igual que cualquier 5xx.

## Fuentes

- Messages API: <https://platform.claude.com/docs/en/api/messages/create.md>
- Versiones de la API: <https://platform.claude.com/docs/en/api/versioning.md>
- Errores: <https://platform.claude.com/docs/en/api/errors.md>
- Límites de uso: <https://platform.claude.com/docs/en/api/rate-limits.md>
- Razonamiento por modelo:
  <https://platform.claude.com/docs/en/build-with-claude/thinking.md>
- Razonamiento adaptativo:
  <https://platform.claude.com/docs/en/build-with-claude/adaptive-thinking.md>
- Esfuerzo: <https://platform.claude.com/docs/en/build-with-claude/effort.md>
- Razonamiento preservado:
  <https://platform.claude.com/docs/en/build-with-claude/preserved-thinking.md>
- Caché de prompts:
  <https://platform.claude.com/docs/en/build-with-claude/prompt-caching.md>
- Streaming: <https://platform.claude.com/docs/en/build-with-claude/streaming.md>
- Tools: <https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview.md>
- Capa compatible con OpenAI:
  <https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk>
- Guía de migración de modelos:
  <https://platform.claude.com/docs/en/about-claude/models/migration-guide.md>
