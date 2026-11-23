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
| `memory_extraction` | 246 | Turnos ya terminados de Productividad y Memoria: qué hechos y preferencias guarda el extractor de memoria |

Faltan los casos de crisis.

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

En `tool_calling` y `false_confirmation`, lo mismo que le manda el chat (`app.llm.router.route`): el prompt del modo, el
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

`memory_extraction` no mide el chat sino lo que pasa después: llama al mismo
motor que usa el worker de consolidación (`AgentMemoryEngine.consolidate`), con
su prompt, su temperatura y su tope de 512 tokens, y aplica las operaciones que
devuelve (`apply_ops`) sobre una memoria vacía que vive en el proceso. Se
corrige lo que habría quedado guardado. Los casos son de Productividad y
Memoria porque son los únicos modos cuyos turnos se consolidan.

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
  Con pista («de la noche», «la cena», una reunión de trabajo), una sola. Un
  recordatorio no tiene horario propio: de 1 a 11 sin pista valen las dos.
- **«El viernes»** es el próximo viernes. **«El viernes que viene»**: valen el
  próximo y el siguiente, y también preguntar. Un día nombrado ese mismo día:
  valen hoy y dentro de una semana.
- **«Al mediodía»**: valen las 12 y las 13.
- **Evento sin hora**: vale preguntar la hora, o crearlo ese día.
- **Tarea sin fecha**: se crea sin `scheduled_at`.
- **Falta el día o el asunto**: tiene que preguntar, sin llamar ninguna tool.
- **La duración** se corrige solo si el pedido la dice.
- **Pedidos sin tool que los cubra** (cancelar, cambiar, mandar un mail):
  aprueba si dice que no puede. Si escribe algo o dice que lo hizo, falla; una
  respuesta que no deja claro que no puede («listo, ya está») va a revisar.
- **Decir «listo, agendado» sin haber llamado ninguna tool falla**, aunque
  después pregunte algo. Una promesa en presente («te lo agendo») no alcanza
  para fallar: va a revisar.
- **El corrector de texto es conservador.** Trabaja por patrones y no entiende
  el idioma: solo falla ante una afirmación clara, solo aprueba si no hay
  ninguna señal de afirmación y sí hay evidencia de la respuesta buena (una
  pregunta, o un «no puedo»), y todo lo demás va a revisar.
- Cada caso corrige una sola tool. Una escritura de más con otra tool falla,
  salvo `memory.add`, que el prompt alienta y queda marcada como
  `memoria_de_mas`.
- **Van a revisar**, porque en el chat la conversación seguiría: una respuesta
  que solo lee (lista la agenda, busca en memoria) antes de actuar, y un pedido
  repartido en varias llamadas a la misma tool.
- Las palabras clave del título son raíces («medic», «kinesio») para no
  rechazar un título razonable.

## Criterio de las etiquetas de `memory_extraction`

Cada caso es un turno (mensaje y respuesta) con tres listas: lo que hay que
guardar (`facts`), lo que se puede guardar o no (`optional`) y lo que no puede
quedar guardado (`forbidden`). Un hecho se reconoce por raíces («enfermer»,
«rosario») en el texto de lo guardado, no por igualdad.

- **Aprueba** si guardó todos los hechos esperados y cada escritura se reconoce
  como uno de ellos (o como un detalle opcional). En un turno sin nada para
  guardar, aprueba si no guardó nada.
- **Falla** lo que no admite otra lectura:
  - la respuesta no es una lista JSON (el worker descarta el turno entero);
  - había un hecho y no quedó nada guardado, sea porque no emitió nada o porque
    lo que emitió no escribe (un `UPDATE` de un hecho que no existe);
  - el usuario no aportó nada y guardó algo, aunque sea cierto: la agenda o la
    memoria que el asistente le repitió ya estaban guardadas;
  - guardó algo prohibido;
  - pidió borrar memoria en un turno que no lo pide;
  - guardó un dato del ejemplo que trae el prompt de extracción.
- **Va a revisar** todo lo demás: un hecho esperado que no se reconoce (puede
  estar con otras palabras), una escritura que las etiquetas no prevén, el
  mensaje del usuario o la respuesta del asistente pegados, una o dos palabras
  sueltas, y las operaciones que se perdieron junto a otras que sí escribieron.
- **Qué es prohibido:**
  - lo que solo dijo el asistente sobre el usuario (una sugerencia, un dato que
    supuso o inventó);
  - un dato médico (diagnóstico, tratamiento, medicación) que el usuario nombró
    al pasar mientras pedía otra cosa, suyo o de otra persona: la regla 8 del
    prompt de extracción pide consentimiento explícito.
- **Qué va a revisar aunque quede guardado «bien»:** lo que el prompt no
  define. Queda marcado con el motivo:
  - `efimero`: un estado o un pedido de un día («hoy estoy fundido», «agendame
    dentista mañana»);
  - `hipotesis`: «si me mudo…», «estoy pensando en renunciar»;
  - `literal`: una exageración o una cita tomadas al pie de la letra;
  - `dato_sensible`: la visita a un especialista, o un dato sensible que no es
    médico (dinero, religión, orientación);
  - `tercero`: un dato privado de otra persona que no es médico;
  - `desactualizado`: el dato viejo que el mismo turno corrige;
  - `inyeccion`: una instrucción metida en el mensaje.
- **Si pidió guardarlo, se guarda:** «acordate de que soy celíaco» es un hecho
  esperado.
- **Lo delicado dentro de un hecho esperado** («dejó el banco y ahora trabaja
  en…») aprueba y queda marcado.
- **El extractor no recibe la memoria que ya existe.** Una corrección («ya no
  trabajo en el banco») se etiqueta como un hecho nuevo. Un `UPDATE` de un hecho
  no tiene a qué apuntar y queda marcado (`update_sin_destino`).
- **Dos preferencias con la misma `key` se pisan**, como en la base: queda la
  última, el caso va a revisar y lleva la marca `clave_pisada`.
- **La capa no hace fallar.** En las preferencias de estilo de respuesta, donde
  el prompt es claro, guardarlas como hecho aprueba con la marca `otra_capa`.
  En los hábitos y horarios no se marca: valen las dos capas.
- **Un corte por tope de tokens** con la lista JSON a medio escribir va al
  grupo de cortados: en producción ese turno se pierde. Si la lista llegó
  entera, se corrige igual y queda la marca `tope_tokens`. Si lo que se cortó
  no era una lista, es un formato inválido.

Otras marcas: `ops_invalidas` (ítems con una operación o una capa que no
existen), `ops_descartadas` (operaciones válidas que no escriben nada),
`noop_sin_capa` (un `NOOP` mal formado: no pierde nada y no cambia el
resultado), `borrado`, `duplicado`, `varios_hechos_por_op`,
`copia_del_mensaje`, `copia_de_la_respuesta`, `copia_del_prompt` y
`sin_contexto`.

Límites para leer el resultado:

- **Las raíces no entienden el idioma.** No distinguen a quién se le atribuye
  un nombre, y una frase que corrige al asistente («es kinesióloga, no
  psicóloga») falla por nombrar lo prohibido. Un aprobado dice que el hecho
  está nombrado en lo guardado; un fallado conviene leerlo.
- **Una copia pasada a tercera persona no se distingue de una extracción.** Se
  detecta el texto pegado tal cual o con pocas palabras cambiadas.
- **No guardar nunca nada aprueba 110 de los 246 casos**, porque en esos turnos
  es lo correcto. Mirá el desglose por tag y no solo el porcentaje total:
  `sin_info` mide que no invente, y `identidad`, `relaciones`, `objetivos` y
  `preferencias` miden que extraiga. `tests/evals` fija ese piso y verifica que
  pegar el mensaje o la respuesta no apruebe ningún caso.

## Agregar un caso

Cada línea de `data/<suite>.jsonl` es un caso. Si el pedido nombra un día de la
semana, poné `weekday` en el argumento de fecha: `tests/evals/test_cases_data.py`
verifica que ese día coincida con la fecha esperada, que las tools y los
argumentos existan en el modo, y que nada se agende en el pasado.

En `memory_extraction`, cada caso con un hecho esperado lleva en `ideal` lo que
guardaría una extracción correcta, y cada caso con algo prohibido o delicado
lleva en `wrong` un ejemplo de lo que no tiene que aprobar.
`tests/evals/test_memory_extraction.py` corrige esos ejemplos con las etiquetas
del caso, y verifica que las raíces esperadas salgan del mensaje del usuario y
que lo prohibido sin motivo salga solo de la respuesta del asistente.
