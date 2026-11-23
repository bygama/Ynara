# Set de evaluación sintético

Casos escritos a mano y un runner para medir cómo responde un modelo en las
tareas que le pide Ynara. Sirve para comparar proveedores antes de elegir uno
y para notar si un cambio de prompt o de modelo empeora algo.

No es parte del producto: vive fuera de `app/`, no corre en la CI y no cuenta
en la cobertura. Lo que sí corre en la CI son los tests de `tests/evals/`, que
prueban el runner con el cliente fake y controlan las etiquetas de los casos.

## Reglas

- **Solo datos sintéticos.** Los casos son inventados, con nombres inventados.
  El runner no abre ninguna sesión de base de datos: no puede mandar memoria ni
  historial de nadie. No agregues casos sacados de conversaciones reales.
- **El modelo se llama por el código del backend** (`app/llm`), nunca con un
  cliente HTTP propio ni con un SDK (regla #4b de `AGENTS.md`, ADR-028 D1).
- **Sin dependencias nuevas.**
- Antes de correrlo contra un proveedor, releé la regla #4b: hasta cerrar las
  compuertas de ADR-028 solo se usan proveedores que no entrenen con los datos.

## Suites

| Suite | Casos | Qué mide |
| --- | ---: | --- |
| `tool_calling` | 118 | Pedidos en rioplatense con fechas relativas en modo Productividad: qué tool llama y con qué argumentos |
| `false_confirmation` | 30 | Pedidos de «agendame…» en Estudio, Bienestar y Vida, que no tienen tools: ¿contesta «listo, agendado»? |

Faltan la extracción de memoria y los casos de crisis.

## Cómo se corre

Desde `apps/backend`, con `LLM_BACKEND=api` y los proveedores cargados en tu
`.env` (ver `docs/operations/LOCAL-DEV.md`):

```bash
uv run python -m evals list
uv run python -m evals run --suite tool_calling --provider principal
uv run python -m evals run --suite tool_calling --provider respaldo
uv run python -m evals compare evals/out/<reporte-a>.json evals/out/<reporte-b>.json
```

- `run` llama a un proveedor real y gasta tokens. `list` y `compare` no llaman
  a nadie.
- `--provider` es el `name` de una entrada de `LLM_PROVIDERS`. Cada corrida usa
  ese proveedor solo, sin respaldo: si falla, se anota como error en vez de que
  conteste otro.
- `--repeat N` corre cada caso N veces (el chat usa temperatura 0,7, así que la
  misma pregunta no siempre da la misma respuesta). `--limit N` corre los
  primeros N casos, para una prueba corta.
- El reporte queda en `evals/out/`, que git ignora. Guarda cada respuesta, para
  revisar a mano lo que el corrector no pudo decidir.

## Qué se le manda al modelo

Lo mismo que le manda el chat (`app.llm.router.route`): el prompt del modo, el
preámbulo de fecha, las tools del modo, el alias y la intención de
razonamiento, con los `max_tokens` y la temperatura por default. Hay dos
diferencias, a propósito:

- **Sin memoria ni historial.** El bloque de memoria va vacío, cada caso es el
  primer mensaje de una conversación y ninguna tool se ejecuta: se corrige la
  primera respuesta del modelo.
- **El «ahora» lo fija el caso.** Por default es el miércoles 25 de noviembre de
  2026 a las 10:30, hora de Buenos Aires. Así «mañana» tiene una sola respuesta.

`tests/evals/test_route_drift.py` compara este armado con el del chat: si el
chat cambia, el test falla.

## Cómo se lee el resultado

Cada caso termina en uno de cinco grupos:

| Grupo | Significa |
| --- | --- |
| aprobado | Respondió lo esperado |
| fallado | Respondió otra cosa |
| a revisar | El corrector no puede decidir; hay que leer la respuesta en el reporte |
| error de llamada | El proveedor no contestó (timeout, límite de tasa, rechazo). No es una respuesta mala |
| cortado por tope de tokens | Se gastaron los tokens de salida sin dejar texto (por ejemplo, razonando) o en medio de una tool call, que el chat no ejecutaría |

El porcentaje de aprobación se calcula sobre los tres primeros. Los errores y
los cortes se cuentan aparte. El reporte suma además tokens, latencia (p50 y
p95) y cuántas respuestas trajeron razonamiento.

En `tool_calling`, una fecha sin offset (`2026-11-26T15:00:00`) se toma como
hora local y aprueba, pero queda marcada como `fecha_sin_offset`: el prompt
pide el offset.

## Criterio de las etiquetas de `tool_calling`

Lo que el prompt de Productividad define, se exige. Donde no dice nada, se
aceptan las alternativas razonables en vez de imponer una.

- **Hora sin pista** («a las 7»): valen las 7 y las 19, y también preguntar.
  Con pista («de la noche», «la cena», una reunión de trabajo), una sola.
- **«El viernes»** es el próximo viernes. **«El viernes que viene»**: valen el
  próximo y el siguiente, y también preguntar. Un día nombrado ese mismo día:
  valen hoy y dentro de una semana.
- **«Al mediodía»**: valen las 12 y las 13.
- **Evento sin hora**: vale preguntar la hora, o crearlo ese día.
- **Tarea sin fecha**: se crea sin `scheduled_at`.
- **Falta el día o el asunto**: tiene que preguntar, sin llamar ninguna tool.
- **La duración** se corrige solo si el pedido la dice.
- **Pedidos sin tool que los cubra** (cancelar, cambiar, mandar un mail): no
  tiene que escribir nada, ni decir que lo hizo.
- **Decir «listo, agendado» sin haber llamado ninguna tool falla**, aunque
  después pregunte algo.
- Cada caso corrige una sola tool. Una escritura de más con otra tool falla,
  salvo `memory.add`, que el prompt alienta y queda marcada como
  `memoria_de_mas`.
- **Van a revisar**, porque en el chat la conversación seguiría: una respuesta
  que solo lee (lista la agenda, busca en memoria) antes de actuar, y un pedido
  repartido en varias llamadas a la misma tool.
- Las palabras clave del título son raíces («medic», «kinesio») para no
  rechazar un título razonable.

## Agregar un caso

Cada línea de `data/<suite>.jsonl` es un caso. Si el pedido nombra un día de la
semana, poné `weekday` en el argumento de fecha: `tests/evals/test_cases_data.py`
verifica que ese día coincida con la fecha esperada, que las tools y los
argumentos existan en el modo, y que nada se agende en el pasado.
