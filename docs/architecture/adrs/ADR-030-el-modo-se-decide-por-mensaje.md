# ADR-030: El modo se decide por mensaje y la memoria es una sola

## Estado

Propuesto

## Fecha

2026-11-24

## Contexto

Ynara tiene cinco modos ([`MODES.md`](../../product/MODES.md)). Hoy el modo lo
elige la persona y queda fijo para toda la conversación:

- El cliente manda `mode` en cada mensaje y el backend rechaza con 409 un modo
  distinto al de la sesión abierta.
- Cambiar de modo abre otra conversación (web) o retoma la última de ese modo,
  o crea una si no hay (móvil). En los dos casos se pierde el hilo.
- Estudio, Bienestar y Vida usan el alias `conversacional`, que no llama tools
  ([ADR-028](./ADR-028-inferencia-por-api.md)). Si en una charla de Vida la
  persona pide «recordame llamar a mamá mañana», Ynara no puede hacerlo: lo
  único correcto que le queda es decir que desde ahí no agenda.

El prototipo navegable promete lo contrario («Ynara cambia de modo sola según
lo que le pidas»), `MODES.md` dice que el alias `agente` «puede sugerir un
cambio de modo» y `TOOLS.md` describe una tool `mode.switch` planeada. Nada de
eso existe en el código.

La decisión de producto tiene dos partes:

- **Ynara cambia de modo sola** cuando el pedido es claro, lo sugiere cuando
  no lo es, y la conversación sigue siendo la misma.
- **Ynara te conoce en todos los modos.** El modo define cómo responde (tono,
  modelo, tools), no qué sabe de la persona.

En este ADR, **modos de charla** son los del alias `conversacional` (Estudio,
Bienestar y Vida) y **modos de acción** son los del alias `agente`
(Productividad y Memoria): los que llaman tools y cuyos turnos se consolidan
en memoria.

Este ADR fija las decisiones y lo que tiene que cumplirse siempre. El
mecanismo de cada una (campos, tablas, contadores) se define en los PRs de
implementación, con tests que comprueben lo que acá se promete.

## Decisión

### D1 — El modo es del mensaje, no de la conversación

Cada turno se responde en un modo y se guarda con ese modo. Una conversación
puede mezclar modos, y se elimina el 409.

Lo que tiene que cumplirse:

- El modo vigente lo determina el backend: es el del último turno guardado
  de la conversación y, si no hay ninguno, el modo con el que arrancó. No
  depende de que el cliente esté al día. Un turno que falló no se guarda, así
  que no cambia el modo vigente.
- El pedido dice de forma explícita cuándo el modo lo eligió la persona. Un
  pedido que no trae los campos nuevos es de un cliente anterior: se responde
  en el modo que manda y nunca se clasifica.
- `sessions.mode` pasa a ser «el modo con el que arrancó la conversación».
  Hoy lo leen el cierre de sesión y el reparto por modo del panel de
  administración; se revisan en el PR que saca el 409.

### D2 — Un paso de clasificación propone; la regla la aplica el código

Antes de responder, el backend clasifica el mensaje con una llamada corta al
alias `conversacional`. Recibe solo el mensaje y el modo vigente, sin
historial. Devuelve uno de los cinco modos, si el pedido es claro o dudoso, y
si el mensaje puede ser una crisis.

El clasificador vive en `apps/backend/app/llm/` y llama al modelo por el mismo
cliente que el chat: no hay HTTP nuevo fuera de `clients/providers/`. Corre
antes de `router.py`, que sigue siendo la entrada para responder el turno. No
se agrega un tercer alias.

El clasificador no decide qué pasa con el modo. La regla vive en código:

| Qué devuelve el clasificador | Qué pasa |
|---|---|
| Posible crisis, sea cual sea el modo que proponga | Responde el modo vigente. Sin cambio y sin sugerencia (D7). |
| El mismo modo, o no se pudo clasificar | Responde el modo vigente. Sin aviso. |
| Otro modo, claro, y ni el vigente ni el nuevo es Bienestar | **Cambio automático**: responde el modo nuevo y la respuesta lleva un aviso con deshacer. |
| Cualquier otro caso: dudoso, o el vigente o el nuevo es Bienestar | **Sugerencia**: responde el modo vigente y ofrece el otro. |

Consecuencias directas de la tabla:

- **Ynara nunca entra ni sale de Bienestar por su cuenta.** Solo lo sugiere.
- **Todo mensaje recibe una respuesta.** Ningún camino retiene el mensaje a
  la espera de que la persona elija.
- Si la persona acepta una sugerencia, el mismo mensaje se responde otra vez
  en el modo nuevo. Es una elección de la persona, y es el único caso en que
  un mensaje se procesa dos veces. Si la rechaza o la ignora, el modo queda
  como está y ese cambio no se vuelve a sugerir durante los mensajes
  siguientes (los mismos de D3).

Si el clasificador tarda más que su tope, falla o devuelve algo que no se
puede leer, la conversación sigue en el modo vigente sin avisar. Un fallo del
clasificador nunca degrada el turno.

### D3 — Lo que elige la persona manda

Cuando la persona elige un modo a mano, acepta una sugerencia o deshace un
cambio, ese mensaje no se clasifica y el modo queda fijo durante los mensajes
siguientes (valor inicial: tres). Los cuenta el backend. Evita que Ynara
vuelva a cambiar justo después de que le dijeron que no.

La regla de D2 gobierna los cambios que propone el clasificador. Lo que la
persona elige a mano no pasa por ella: puede entrar o salir de Bienestar
cuando quiera.

**Deshacer revierte el modo, no la acción.** Si el cambio automático agendó
algo, el evento queda y se borra desde Agenda como cualquier otro. Lo que se
haya guardado en memoria tampoco se revierte.

### D4 — El contrato lleva el modo que respondió

La respuesta de `/v1/chat` y el stream de `/v1/chat/stream` informan el modo
que respondió y, si hubo cambio, de qué tipo (automático o sugerencia) y
entre qué modos. En el stream ese dato viaja antes del primer evento de
contenido, sea de razonamiento o de texto, para que la interfaz cambie de
color antes de escribir. Los campos exactos se documentan en
`apps/backend/docs/ENDPOINTS.md` y en `packages/shared-schemas` en el PR que
los agrega.

El aviso de un cambio es de la interfaz, no de Ynara: el nombre y el color
del modo cambian, y hay un deshacer. Ynara no narra el cambio en su respuesta
(«Pasé a modo X para esto»).

### D5 — Una sola memoria y un solo hilo

El modo no limita lo que Ynara sabe:

- **El historial cruza los modos.** El modelo recibe los turnos anteriores de
  la conversación sin importar en qué modo se respondió cada uno, Bienestar
  incluido.
- **Los cinco modos leen las tres capas de memoria** (semántica, episódica y
  procedural). Hoy solo Memoria lee las tres; Vida, por ejemplo, lee solo la
  procedural. Cambia `ynara.config.json[modes][*].memory_layers`, que necesita
  aprobación humana (regla 1 de `AGENTS.md`), y las tablas de capas de
  `MODES.md` y de `AGENTS.md` en el mismo PR.

Con esto puede ir más memoria a cada llamada al proveedor. No cambia la
regla 4a: cada llamada sigue llevando solo la memoria que se recupera para
ese mensaje, con los topes de hoy. Lo que cambia es que puede venir de las
tres capas en cualquier modo. Entra en lo que revisa la compuerta legal de
ADR-028.

No cambia quién escribe: los turnos de los modos de charla siguen sin
consolidarse por turno.

Lo dicho en Bienestar puede, entonces, terminar guardado: en el resumen de la
conversación, o porque un turno posterior de un modo de acción lo retoma. No
se esconde entre modos. Lo que se hace es marcarlo, dentro de esa
conversación (D6).

### D6 — Lo sensible se marca, no se esconde

Hoy la marca de sensible existe solo en el resumen episódico y depende del
modo de la sesión ([ADR-007](./ADR-007-memory-decay-retention-encryption.md)
D2). Pasa a depender de los turnos de la conversación.

**Turno sensible.** Un turno es sensible si se respondió en Bienestar. También
si el clasificador lo marcó como posible crisis o lo propuso para Bienestar,
aunque lo haya respondido otro modo.

**Qué se marca.** Lo que una conversación escriba en memoria a partir de su
primer turno sensible. Cuenta el orden de los turnos, no el momento en que
corre la consolidación, que es asíncrona:

- El resumen episódico, como hoy.
- Lo que se escriba en la memoria semántica, sea por la consolidación o por
  una tool dentro del turno. Hoy la semántica no tiene marca.
- En la memoria procedural no se escribe. Esa capa no tiene marca y guarda
  su valor sin cifrar (ADR-007 D3), y qué va a cada capa lo decide el modelo
  que extrae. Desde el primer turno sensible, la consolidación de esa
  conversación guarda solo en la semántica.

Una entrada marcada sigue marcada cuando se actualiza. La marca no se edita:
la persona puede borrar el dato, no desmarcarlo.

**Qué implica la marca.**

- En lo episódico, lo mismo que hoy: la retención de Bienestar, el registro
  de auditoría diferenciado y el export aparte.
- En lo semántico, identificar: auditoría diferenciada, export aparte, y que
  la persona pueda ver lo marcado y borrarlo, de a uno o todo junto. **Un dato
  no vence por estar marcado**: dura como cualquier otro.

**Qué queda sin marca.**

- Los datos que vienen de turnos anteriores al primer turno sensible de la
  conversación, aunque se consoliden después.
- La agenda: los eventos y tareas se crean por pedido expreso de la persona.
- Lo que Ynara retome en otra conversación. Si recuerda algo marcado y un
  turno posterior lo reformula, lo que se guarde de ese turno no hereda la
  marca y puede ir a cualquier capa, la procedural incluida.

Heredar la marca por haber leído algo marcado no sirve. La memoria se
recupera por cercanía, sin umbral, y con D5 la leen los cinco modos: un solo
recuerdo marcado entraría en el contexto de casi todos los turnos y en poco
tiempo toda la memoria quedaría marcada. Una marca que lo cubre todo deja de
identificar.

**Qué es garantía y qué es medida.**

- **Garantía por código:** desde un turno respondido en Bienestar, lo que esa
  conversación escribe en la episódica y en la semántica queda marcado, y no
  escribe en la procedural. Depende del modo guardado en el turno, no del
  clasificador ni del cliente.
- **Medida:** la marca de los turnos que no se respondieron en Bienestar
  depende de que el clasificador haya corrido y lo haya detectado. No existe
  si está apagado, si falló, si el modo estaba fijo o si el pedido es de un
  cliente anterior. Se mide en D8.
- **Sin medir todavía:** cuánto de lo marcado reaparece sin marca en otras
  conversaciones. Pasa también con el clasificador apagado, así que D8 no lo
  condiciona. Medirlo pide una suite que encadene dos conversaciones, que el
  set de evaluación hoy no tiene. Queda como deuda declarada de este ADR: no
  es requisito para usar D1 y D5.

La regla marca de más a propósito: un dato cualquiera («dentista el jueves»)
dicho después de un turno sensible también queda marcado.

**La salida legal de ADR-028 se conserva** para lo que se guardó desde
conversaciones con turnos sensibles, en la episódica y en la semántica: si el
visto bueno legal pidiera no guardar memoria de Bienestar, la marca permite
no guardarlo o borrarlo. No alcanza a la agenda ni a lo que se haya
reformulado después en otra conversación.

**Al cerrar.** El resumen se marca sensible si la conversación tuvo algún
turno sensible. Se atribuye al modo con el que arrancó: la marca va aparte,
como ya permite el registro de auditoría.

**Requisitos.** D1 y D5 no se usan con personas reales sin estas dos cosas,
que van en sus propios PRs:

- La marca de sensible en la memoria semántica, con la pantalla de Memoria
  mostrando lo marcado y dejando borrarlo. Toca una tabla protegida por la
  regla 3 de `AGENTS.md`: migración con aprobación humana.
- Que las conversaciones se cierren (por inactividad, de forma explícita o
  ambas). El resumen y su marca se generan en el cierre, y este ADR no cambia
  quién lo dispara ni cuándo.

### D7 — Una crisis no se enruta

Cuando el clasificador marca una posible crisis, el turno queda en el modo
vigente: sin cambio y sin sugerencia. Vale también para una crisis dicha
dentro de un pedido («agendame… porque ya no doy más»).

La detección es una medida, no una garantía. Si el clasificador no la
detecta, el mensaje se enruta como cualquier otro y puede terminar en otro
modo. Lo que sí está garantizado es que algún modo lo responde (D2). Por eso
el protocolo de crisis no puede depender del modo.

Hoy ese protocolo está solo en el prompt de Bienestar. La segunda compuerta
de ADR-028 ya pide que viva en código; este ADR agrega que **tiene que valer
en los cinco modos antes de activar el clasificador para personas reales**.

La etiqueta «posible crisis» no viaja en el contrato de D4 ni queda en logs o
métricas asociada a una persona o a una conversación. Lo que persiste es la
marca de sensible de D6, que no distingue una crisis de un turno de
Bienestar.

### D8 — Se mide antes de activarlo

- El clasificador entra detrás de una opción de configuración, apagada por
  defecto. Apagada, no se clasifica nada: responde el modo vigente, o el que
  la persona eligió. D1, D5 y D6 no dependen de esa opción.
- El set de evaluación (`apps/backend/evals/`) suma una suite de enrutamiento:
  mensaje y modo vigente → modo esperado y tipo de cambio. Incluye pedidos de
  acción hechos desde modos de charla, pedidos indirectos, mensajes que solo
  se entienden con el anterior, y mensajes que deberían marcarse sensibles.
- Los mensajes de crisis del set de evaluación se corren por el clasificador
  desde cada modo. Lo esperado es siempre lo mismo: marca de crisis y modo
  vigente. Que el set tenga casos de crisis es un requisito de este ADR.
- La misma suite mide la marca de sensible que pone el clasificador: cuántos
  mensajes comunes marca de más y cuántos sensibles deja pasar.
- Se mide contra cada proveedor de la lista, principal y respaldo. Qué cuenta
  como «claro» y con qué resultado se activa se fija con la primera medición.
  Activarlo es una decisión de quien responde por el producto, y queda
  registrada en el PR que cambia la opción.
  Hasta que haya proveedor (ADR-028), el clasificador se puede construir y
  probar con dobles de prueba, pero su calidad no está medida.
- El clasificador manda el mensaje de la persona al mismo proveedor que el
  chat: rigen las mismas compuertas de ADR-028 (solo datos sintéticos hasta
  cerrarlas). En los logs queda el modo de origen, el de destino y el tipo de
  cambio, nunca el texto.

## Consecuencias positivas

- Ynara puede cumplir un pedido de acción hecho en medio de una charla, sin
  mandar a la persona a otra conversación.
- La promesa del prototipo pasa a ser cierta y medible.
- Ynara conoce a la persona en todos los modos: el modo cambia el cómo, no el
  qué.
- Lo que se guarda desde una conversación con turnos sensibles queda
  identificado, a la vista de la persona, y la salida legal de ADR-028 sigue
  disponible para eso.
- Los datos que Ynara aprende no vencen por haberse dicho cerca de algo
  sensible.
- Web y móvil convergen en un solo comportamiento al cambiar de modo.
- El clasificador se puede mergear apagado y activar cuando esté medido.

## Consecuencias negativas

- Una llamada más al modelo por cada mensaje no fijado: más costo y más
  espera antes de la respuesta. Hay que medir las dos cosas.
- Un cambio automático equivocado puede ejecutar una acción que la persona no
  pidió. Deshacer no la revierte (D3).
- El clasificador no ve el historial: un mensaje que solo se entiende con el
  anterior («mejor a las 11») puede clasificarse mal.
- Ynara puede traer a una charla de trabajo algo dicho en Bienestar. El
  modelo no sabe qué recuerdos son sensibles: el backend no le muestra la
  marca.
- La marca de sensible alcanza de más dentro de una conversación: una casi
  toda de Productividad queda sensible por un turno de Bienestar, y su
  resumen toma la retención corta.
- La marca no sigue al contenido entre conversaciones: lo marcado que Ynara
  retome y reformule más adelante puede guardarse sin marca, y cuánto pasa no
  está medido.
- Un dato marcado de más no se puede desmarcar: solo borrar.
- El plazo corto y configurable de ADR-007 D2 deja de acotar lo sensible que
  llega a la semántica: un dato marcado dura hasta que la persona lo borre.
- Ynara no aprende preferencias de una conversación desde su primer turno
  sensible, porque ahí no se escribe en la procedural.
- Con los cinco modos leyendo las tres capas, cada mensaje lleva más memoria
  en el contexto: más tokens, sobre todo en los modos de charla.
- La agenda queda fuera de la marca (D6).
- Aceptar una sugerencia procesa el mensaje dos veces: queda repetido en el
  historial y en el resumen, con dos respuestas, y entre dos modos de acción
  puede ejecutar tools dos veces.
- Migraciones sobre `conversation_turns` y sobre la memoria semántica, un
  cambio en `ynara.config.json`, y cambios en el contrato del chat que tocan
  backend, web y móvil.
- La calidad depende de un umbral que no se puede ajustar hasta elegir
  proveedor.
- Cuatro requisitos que no resuelve este ADR: el protocolo de crisis en los
  cinco modos (D7), la marca de sensible en la memoria semántica y el cierre
  de las conversaciones (D6), y los casos de crisis del set de evaluación
  (D8).

## Alternativas descartadas

- **Que el modo lo elija solo la persona.** Es lo que hay hoy. Deja la promesa
  sin cumplir y los modos de charla sin salida ante un pedido de acción.
- **Un solo modo, con las conversaciones etiquetadas solas por categoría.**
  Es más simple de usar, y con el cambio automático la experiencia queda
  cerca. Pero pierde el modelo liviano para la charla y los tonos por modo, y
  obliga a rehacer configuración, prompts, evaluaciones e identidad visual.
- **Aislar Bienestar:** que los modos de acción no vean sus turnos ni sus
  resúmenes. Daba una garantía simple, pero contradice que Ynara conozca a la
  persona en todos los modos. La marca de D6 no da esa garantía: la cambia
  por identificar lo sensible y dejarlo a la vista de la persona.
- **Una pasada del agente por detrás del modo de charla.**
  [ADR-022](./ADR-022-tools-sincronos-en-chat.md) D5 dejó `agent_turn_pass`
  reservada para este caso. La acción ocurriría después de la respuesta, así
  que Ynara no podría confirmar en el mismo mensaje lo que hizo, que es la
  razón por la que ADR-022 pasó las tools al turno. `agent_turn_pass` sigue
  como la dejó ADR-022.
- **Una tool `mode.switch` que llame el modelo.** El alias `conversacional` no
  llama tools, así que justo los modos que más la necesitan no podrían usarla.
  Además dejaría la regla de Bienestar a criterio del modelo.
- **Reglas por palabras clave.** No aguantan el español coloquial ni los
  pedidos indirectos («tendría que acordarme de…»).
- **Abrir otra conversación en cada cambio**, como hace hoy la web. Se pierde
  el hilo, que es justo lo que se quiere conservar.
- **Retener el mensaje con una pregunta** cuando se pide una acción desde
  Bienestar, sin responder hasta que la persona elija. Ahorraba una llamada,
  pero una crisis no detectada dicha como pedido quedaba sin respuesta.
- **Que deshacer revierta también la acción.** Exige registrar qué creó cada
  mensaje y un camino para borrarlo, y aun así no alcanza a lo ya guardado en
  memoria.
- **Sugerir modos fuera del chat** (desde Hoy o por notificación). Queda
  afuera: la proactividad es dentro de la conversación.

## Relación con otros ADRs

- **Enmienda** [ADR-028](./ADR-028-inferencia-por-api.md) D3 y D6: el alias
  `conversacional` suma un uso, la clasificación previa al turno, que llama
  al modelo antes de `router.py`. Usa los dos alias y las compuertas que ya
  existen; no agrega alias ni proveedor. `AGENTS.md` (la lista de usos de la
  regla 4b y «único punto de entrada») se actualiza en el PR que implementa
  el clasificador.
- **Enmienda** [ADR-007](./ADR-007-memory-decay-retention-encryption.md) D2:
  la marca de sensible deja de depender del modo de la sesión y se extiende a
  la memoria semántica, sin cambiarle la retención (D6). Al aceptar este ADR
  se anotan las dos enmiendas en el índice de ADRs y en el Estado de ADR-007 y
  de ADR-028, como se hizo con ADR-010.
- **Se apoya en** [ADR-022](./ADR-022-tools-sincronos-en-chat.md): un turno
  que cambió a Productividad ejecuta sus tools en ese mismo turno. No
  reemplaza su D5.
- **Convive con** [ADR-024](./ADR-024-streaming-atomicity-over-ttfb.md): el
  stream empieza con el turno ya completo, así que el modo que respondió
  llega junto con el texto y no adelanta la espera.
