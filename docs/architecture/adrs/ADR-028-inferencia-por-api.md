# ADR-028: La inferencia se consume por API

## Estado

Aceptado

## Fecha

2026-11-16

## Contexto

Hasta acá Ynara corrió sus propios modelos. El dual stack de
[ADR-002](./ADR-002-gemma-qwen-dual-stack.md) (Gemma conversacional + Qwen agente)
se sirve con Ollama/GGUF en la RTX 4080 Super de 16 GB de una PC del equipo
([ADR-014](./ADR-014-serving-ollama-gguf-16gb.md)), vLLM quedó reservado para una GPU
de 24 GB o más ([ADR-009](./ADR-009-vllm-serving-topology-tool-parsers.md)) y el
backend se expone desde esa PC por Tailscale Funnel
([ADR-027](./ADR-027-web-vercel-backend-pc-tailscale-funnel.md)). La regla #4 de
`AGENTS.md` lo fijaba como principio: toda la inferencia on-prem, cero APIs externas
de IA.

Sostener ese esquema ya no se justifica:

- **Costo y operación de la GPU propia.** Los tres modelos (12B + 9B + bge-m3) entran
  en 16 GB con unos 150 MiB de margen y un techo de contexto de 8K
  ([ADR-012](./ADR-012-conversational-model-12b-single-process.md)); bajo vLLM ni
  siquiera entran dos LLM ([ADR-014](./ADR-014-serving-ollama-gguf-16gb.md)). Ganar
  calidad o ventana exige otra GPU. Mientras tanto, operar el serving (pesos,
  cuantización, parsers de tool-calling, el thinking de cada motor, una PC prendida y
  expuesta a internet) consume el tiempo de un equipo chico.
- **Nadie usa el backend en producción.** No hay usuarios ni datos reales que migrar,
  así que el cambio se hace en el lugar, sin una etapa en la que convivan los dos
  caminos.
- **Es un proyecto de tesis.** El valor está en el producto (modos, memoria cifrada,
  agente, tono), no en mantener infraestructura de GPU. Un modelo servido por un
  proveedor da más calidad y más ventana que un 12B/9B cuantizado, sin hardware propio.

El cambio tiene un costo: los datos del usuario pasan a procesarse en la
infraestructura de un tercero. Por eso esta decisión parte la regla #4 de
`AGENTS.md` en dos (4a, privacidad de datos; 4b, política de proveedores de IA) y fija
compuertas antes de que un proveedor reciba datos reales.

## Decisión

### D1 — Toda la inferencia se consume por API

El chat de los modos conversacionales, el agente (con tool-calling), la consolidación
de memoria y los embeddings se consumen por API de proveedores externos, con HTTP
compatible con OpenAI (Chat Completions y Embeddings). **El proveedor todavía no está
elegido**: todo es genérico y se define por configuración. No se instala ningún SDK
de proveedor; el cliente es HTTP sobre `httpx`, que ya es dependencia del backend.

Se elimina lo que existía solo para correr modelos propios: Ollama, vLLM, la
telemetría de GPU y VRAM, Tailscale y el «perímetro local» del panel admin.

### D2 — Proveedores en una lista ordenada, con respaldo

- `LLM_BACKEND` y `EMBEDDING_BACKEND` aceptan `fake` | `api`. El default es `fake`, que
  es lo que corre en dev y en CI.
- `LLM_PROVIDERS` es una lista JSON en una variable de entorno, **en orden de
  prioridad**: el primero es el principal y el resto, respaldo. Cada entrada lleva
  `name`, `base_url`, `models` (`{alias: model_id}`) y `supports_reasoning_effort`
  (bool).
- La credencial de cada proveedor va en una variable aparte, `LLM_API_KEY_<NAME>`
  (el `name` en mayúsculas), y se lee como secreto (`SecretStr`). Nunca va en el JSON,
  ni en `ynara.config.json`, ni en logs.
- `ResilientClient` y el circuit breaker siguen: cada proveedor es una entrada del
  pool, y la cadena es principal → respaldo → respuesta degradada.

### D3 — Alias lógicos de modelo

El código no conoce modelos concretos: conoce dos **alias**.

| Alias | Dónde se usa | Memoria | Tools |
| --- | --- | --- | --- |
| `conversacional` | modos estudio, bienestar y vida | solo lee | no llama tools |
| `agente` | modos productividad y memoria, y la consolidación | lee y escribe; toda escritura de memoria va asíncrona vía Celery | llama tools |

La separación de roles de ADR-002 se mantiene; lo que cambia es que el rol ya no está
atado a un modelo. El `model_id` concreto lo resuelve el cliente del proveedor activo
con su mapa `models`. Desaparecen `served_name`, `served_models`, `engine` y
`LLM_SERVING`: la identidad del modelo en el código es el alias.

Los nombres de tools con punto (`calendar.create_event`) se mapean a `__`
(`calendar__create_event`) para la API y se revierten al parsear la respuesta.

El razonamiento por rol de ADR-012 (D4) sigue como intención: el cliente pide
esfuerzo de razonamiento solo si el proveedor declara `supports_reasoning_effort`.

### D4 — Embeddings: bge-m3 de 1024 dimensiones, por API

Sigue `BAAI/bge-m3` ([ADR-008](./ADR-008-embedding-model-bge-m3.md)) con su salida
densa de 1024 dimensiones (`EMBEDDING_DIM`), ahora servido por un proveedor:
`EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` (secreto) y `EMBEDDING_MODEL` (default
`BAAI/bge-m3`). **Las tablas de memoria no cambian**: sigue `vector(1024)` (regla #3).
Si hay vectores guardados con otro embedder (el fake de dev, por ejemplo), se
re-embeddean: es una migración de datos, no de schema, y pasa por la regla #3 y por
las compuertas de abajo.

### D5 — Reranker: passthrough

El reranker sobre vLLM se elimina. Queda el `Reranker` Protocol con su
`FakeReranker` (passthrough determinista). Un rerank real por API queda como palanca
futura, a decidir con una medición de recall.

### D6 — Un solo lugar habla con los proveedores

`apps/backend/app/llm/clients/providers/` es el **único** lugar del backend que habla
HTTP con proveedores de IA: ahí viven el cliente de chat (`openai_compat.py`,
`OpenAICompatClient`) y el de embeddings (`embeddings.py`, `ApiEmbeddingClient`). Los
Protocols y los clientes fake siguen en sus módulos actuales, y `app/llm/router.py`
sigue siendo la entrada para quien necesita el LLM. Llamar APIs de IA desde cualquier
otro lugar del backend, o desde web, mobile, admin o landing, está prohibido (regla
#4b). `scripts/ynara-doctor.sh` y la skill `pr-review` lo verifican.

### D7 — Arranque en producción: fail-fast

Con `ENVIRONMENT=production`, un backend `fake`, una lista de proveedores vacía o una
credencial faltante **no arrancan**: el error dice qué falta, sin imprimir secretos.

### D8 — Backend en una VPS sin GPU

El backend (FastAPI y los workers de Celery) pasa a una VPS común, sin GPU. La web
sigue en Vercel.

### D9 — Contratos con web y mobile: el mínimo cambio

- `GET /v1/modes` devuelve el alias en `model`.
- El evento SSE `reasoning` se mantiene y solo se emite si el proveedor devuelve
  razonamiento.
- `AGENT_MODES` y el límite de largo del mensaje no cambian de valor.

## Compuertas antes de mandar datos reales

Hasta cerrar las cinco, los proveedores reciben **solo datos sintéticos**:

1. **Legal.** DPA y cláusulas modelo de la AAIP firmadas con cada proveedor (el de
   embeddings incluido), con el compromiso de no entrenar con los datos; política de
   privacidad que nombre a los proveedores; consentimiento implementado; y visto bueno
   legal, en especial para Bienestar.
2. **Crisis.** El protocolo de crisis vive en código, no solo en el prompt (hoy es
   prompt y solo en Bienestar: hallazgos M-18 a M-20 de la
   [auditoría de producto](../../planning/AUDITORIA-PRODUCTO-MERCADO-2026-11.md)), y
   está probado con cada proveedor de la lista, principal y respaldo.
3. **Costo.** Medido por tipo de llamada (chat, vueltas del agente, consolidación,
   embeddings; con y sin razonamiento), con topes por usuario y alertas.
4. **Respaldo.** Probado: se corta el proveedor principal y responde el respaldo.
5. **Latencia.** El p95 queda dentro de la meta en staging.

## Consecuencias positivas

- Sin GPU propia ni serving que operar: se van los pesos, la cuantización, los parsers,
  el thinking por motor y la PC prendida y expuesta.
- Más calidad y más ventana de contexto que un 12B/9B cuantizado en 16 GB.
- El código queda desacoplado del modelo: cambiar de modelo o de proveedor es cambiar
  configuración, no código.
- La disponibilidad de la IA deja de depender de una máquina del equipo, y hay
  respaldo entre proveedores.
- Se conservan el contrato `LLMClient`, el cifrado de la memoria, las tools y los
  clientes fake de CI, y las tablas de memoria mantienen su schema (D4).

## Consecuencias negativas

### Legales

- Mandar datos de usuario a un proveedor del exterior es una **transferencia
  internacional**: art. 12 de la Ley 25.326 y art. 12 del Decreto 1558/2001. Se cubre
  con cláusulas modelo (Disposición 60/2016, Anexo II, y Resolución AAIP 198/2023)
  firmadas con cada proveedor.
- **Bienestar trata datos sensibles** (arts. 7 y 8 de la Ley 25.326). Es el modo de
  mayor riesgo: si el visto bueno legal lo objeta, la salida es no guardar memoria de
  Bienestar.
- Cambia el threat model: el cifrado at-rest de ADR-007 y ADR-010 sigue protegiendo la
  base, pero el texto en claro que se manda a inferir y a embeddear lo procesa el
  proveedor. El límite pasa a ser contractual (DPA, sin entrenamiento) además de
  técnico.
- Los textos públicos que prometen «on-prem» o «nada sale del perímetro» dejan de ser
  ciertos y hay que corregirlos.

### Costo por token

El costo pasa de fijo (hardware y luz) a variable: crece con el uso, y el agente y la
consolidación multiplican las llamadas. Sin topes por usuario, no tiene techo.

### Latencia

Se suman el viaje de red hasta el proveedor y su cola. El SSE del chat ya es cosmético
([ADR-024](./ADR-024-streaming-atomicity-over-ttfb.md)): el turno se computa entero
antes de emitirse, así que lo que pesa es la latencia total del turno.

### Dependencia del proveedor

La disponibilidad, las cuotas (429), los precios, los términos y el retiro de modelos
quedan en manos de terceros. Un proveedor puede cambiar o retirar el modelo que está
detrás de un alias.

### Contratos y auditoría

- La API que consumen web y mobile cambia solo en lo que fija D9; la del panel admin
  pierde lo que dependía del serving propio (D1).
- `audit_log` guarda el modelo de origen en un enum de Postgres que solo conoce `gemma`
  y `qwen` (`llm_model_enum`). Registrar el modelo de cada proveedor exige migrar esa
  tabla sagrada: va en un commit propio y pasa por la regla #3.

## Mitigaciones

- **Lista con respaldo** (D2) y **alias** (D3): un proveedor caído o un modelo retirado
  se reemplazan en configuración.
- **Compuertas** antes de cualquier dato real, y datos sintéticos hasta entonces.
- **Minimización** (regla #4a): a cada llamada va solo lo que esa llamada necesita.
- **Un solo lugar** habla con los proveedores (D6), con verificación mecánica.
- **Topes y alertas de costo** por usuario (compuerta 3).
- **Fail-fast** en producción (D7): no se sale a producción con un cliente fake ni sin
  credenciales.

## Alternativas descartadas

- **Seguir con la GPU propia** (Ollama en la 4080): techo de 12B/9B y 8K de contexto,
  disponibilidad atada a una PC y la operación del serving a cargo del equipo.
- **VPS con GPU** (vLLM en 24 GB o más): caro para un proyecto sin usuarios, y sigue
  sirviendo modelos chicos con la misma operación.
- **Convivencia de caminos** (local + API, con un corte gradual): sin nadie en
  producción, mantener dos caminos es costo sin beneficio. Se reemplaza en el lugar.
- **SDK propietario de un proveedor**: ata el código a un proveedor antes de elegirlo y
  complica el respaldo entre proveedores. El HTTP compatible con OpenAI cubre a la
  mayoría; si se elige uno que no lo sea, su cliente va en `providers/` con un ADR
  propio.
- **Llamar a la IA desde los clientes** (web o mobile): expone credenciales y saca la
  política de datos del backend.
- **Cambiar de modelo de embeddings**: obliga a re-embeddear todo y, si cambia la
  dimensión, a re-migrar una tabla sagrada.

## Relación con otros ADRs

- **Reemplaza** [ADR-002](./ADR-002-gemma-qwen-dual-stack.md): la separación de roles
  sigue, como alias (D3).
- **Reemplaza** [ADR-009](./ADR-009-vllm-serving-topology-tool-parsers.md): sin serving
  propio no hay topología ni parsers.
- **Reemplaza** [ADR-012](./ADR-012-conversational-model-12b-single-process.md): el
  razonamiento por rol (su D4) sigue como intención (D3 de este ADR).
- **Reemplaza el esquema de** [ADR-013](./ADR-013-serving-endpoints-config.md):
  `LLM_SERVING` pasa a `LLM_PROVIDERS`. Sigue la idea de una lista explícita en orden
  de preferencia.
- **Reemplaza** [ADR-014](./ADR-014-serving-ollama-gguf-16gb.md): no hay serving
  local.
- **Reemplaza** [ADR-027](./ADR-027-web-vercel-backend-pc-tailscale-funnel.md): el
  backend deja la PC y Tailscale Funnel por una VPS sin GPU. Sigue la web en Vercel.
- **Enmienda** [ADR-010](./ADR-010-memory-architecture-v2.md): D2 (el texto sale hacia
  el proveedor aprobado y el rerank queda en passthrough), D3 (bge-m3 por API), D4 (la
  extracción la hace el alias `agente`) y D6 (el proveedor entra al threat model).
- **Enmienda** [ADR-017](./ADR-017-admin-app-observabilidad-control-plane.md): la
  soberanía pasa a la regla #4 partida, salen el badge de perímetro y la telemetría de
  GPU y VRAM, y el control plane de su D7 opera sobre proveedores.
- **Enmienda** [ADR-018](./ADR-018-admin-playground-modelos.md) y
  [ADR-019](./ADR-019-playground-agente-observado.md): el serving real es
  `LLM_BACKEND=api`, y el modelo se nombra por alias.
- **Enmienda** [ADR-020](./ADR-020-circuit-breaker-cota-workers.md): la cota de workers
  deja de apoyarse en una sola GPU; el breaker per-proceso sigue, por proveedor.
- **Enmienda** [ADR-021](./ADR-021-qwen-pasada-asincrona-agente.md): gemma y qwen pasan
  a ser los alias `conversacional` y `agente`.
- **Ratifica** [ADR-008](./ADR-008-embedding-model-bge-m3.md): sigue bge-m3 de 1024
  dimensiones; cambia el serving, de on-prem a API.
- En los ADR vigentes que nombran modelos (por ejemplo, ADR-022 y ADR-026), «gemma» se
  lee como el alias `conversacional` y «qwen», como el alias `agente`.

## Fuentes

- Ley 25.326 de Protección de los Datos Personales: art. 12 (transferencia
  internacional) y arts. 7 y 8 (datos sensibles).
- Decreto 1558/2001, art. 12.
- Disposición 60/2016, Anexo II.
- Resolución AAIP 198/2023.
