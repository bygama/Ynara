# GLOSSARY.md — Vocabulario de Ynara

Términos que aparecen recurrentemente en el repo y en conversaciones
del equipo. Si encontrás un término ambiguo y no está acá, agregalo
con un PR.

## Producto

- **Modo** — uno de los 5 perfiles operativos del producto:
  productividad, estudio, bienestar, vida, memoria. Definidos en
  `ynara.config.json`.
- **Hand-off** — cuando Ynara cambia de modo (manual o sugerido) en
  medio de una conversación.
- **Sesión** — span continuo de interacción de un usuario, marcado
  por inactividad > N minutos o cambio explícito.

## Memoria

- **Capa semántica** — hechos persistentes sobre el usuario.
  Embeddings en pgvector; implementación in-house (ADR-010).
- **Capa episódica** — resúmenes de conversaciones pasadas.
- **Capa procedural** — preferencias y patrones (JSONB).
- **Consolidación** — proceso async (Celery) que extrae memoria
  nueva, deduplica y persiste.
- **Recall** — operación de búsqueda en memoria.
- **Tabla sagrada** — `semantic_memory`, `episodic_memory`,
  `procedural_memory`. Requieren tests + 1 aprobación humana
  explícita para cualquier migración (regla #3).

## Modelos

- **Alias de modelo** — nombre con que el código pide un modelo sin
  atarse a uno concreto: `conversacional` o `agente` (ADR-028 D3). Cada
  modo de `ynara.config.json[modes]` apunta a uno, y cada proveedor de
  `LLM_PROVIDERS` lo traduce a su id de modelo.
- **`conversacional`** — alias de estudio, bienestar y vida. Solo lee
  memoria y no llama tools.
- **`agente`** — alias de productividad, memoria y la consolidación de
  memoria. Lee y escribe memoria y llama tools.
- **Proveedor de IA** — servicio externo que sirve los modelos con una
  API compatible con OpenAI (ADR-028). Los del chat van en
  `LLM_PROVIDERS`, en orden de prioridad, con la credencial en
  `LLM_API_KEY_<NAME>`; el de embeddings, en las `EMBEDDING_*`. Solo se
  usan los aprobados por la regla #4b, y hasta cerrar las compuertas
  reciben solo datos sintéticos.
- **Principal / respaldo** — el primer proveedor de `LLM_PROVIDERS` es
  el principal y los siguientes, el respaldo. El `ResilientClient` pasa
  al siguiente cuando uno falla (timeout, caída, 429 largo o credencial
  rechazada).
- **Respuesta degradada** — mensaje fijo que recibe el chat cuando se
  agota la cadena de proveedores, en vez de un error
  (`finish_reason: "degraded"`). Un turno degradado no se consolida.
- **Compuertas** — las cinco condiciones de ADR-028 (legal, crisis,
  costo, respaldo probado y latencia) que se cierran antes de mandarle
  datos reales a un proveedor.
- **Gemma / Qwen** — los modelos propios de antes de ADR-028 (Gemma 4
  12B conversacional, Qwen 3.5 9B agente), hoy reemplazados por los
  alias. Quedan en nombres históricos: el enum `llm_model_enum`
  (`gemma` / `qwen`) de `audit_log.origin_model` y la clase
  `QwenMemoryEngine`.
- **bge-m3** — modelo de embeddings, 1024 dimensiones. Lo sirve un
  proveedor por API (`EMBEDDING_MODEL`, default `BAAI/bge-m3`; ADR-028
  D4) a través de `ApiEmbeddingClient`.
- **Reranker** — reordena los candidatos del ANN (el top-K que devuelve
  pgvector) por relevancia antes del recall final. Contrato `Reranker`
  (Protocol) en `apps/backend/app/llm/clients/reranker.py`. Hoy es un
  passthrough en todos los entornos, production incluida: el
  `FakeReranker` deja el orden del ANN (ADR-028 D5). Un rerank real por
  API queda como palanca futura.
- **Clientes fake** — `FakeLlmClient` y `FakeEmbeddingClient`:
  deterministas y sin red. El `FakeLlmClient` contesta solo lo que le
  programa un test, así que en el servidor de dev el chat no contesta.
  Son el default de dev y CI (`LLM_BACKEND=fake`,
  `EMBEDDING_BACKEND=fake`); production no arranca con ellos.

## Stack

- **Router LLM** — `apps/backend/app/llm/router.py`. Toma el alias del
  modo activo, arma el contexto de memoria y corre el tool loop.
- **Tool** — función que el alias `agente` puede invocar (calendar,
  task, reminder, memory). Definidas en
  `apps/backend/docs/TOOLS.md`.
- **Tabla operativa** — cualquier tabla que no sea de memoria
  sagrada (users, sessions, audit_log, etc.).

## Auth

- **`sid`** — claim (UUID hex) que identifica la familia/sesión de un
  token. Agrupa el access + todos los refresh rotados de esa sesión bajo
  una unidad revocable. Lo llevan tanto el access como el refresh (#142).
- **Familia de sesión** — conjunto de tokens que comparten el mismo `sid`.
  Se revocan de golpe con `revoke_family` (un logout o un breach matan la
  familia entera, no un solo `jti`).
- **Reuse-detection** — mecanismo de `/v1/auth/refresh`: un refresh ya
  rotado que reaparece se evalúa contra el grace. Dentro del grace
  (`AUTH_REFRESH_REUSE_GRACE_SECONDS`) es un retry benigno e idempotente;
  fuera del grace es un breach → revoca la familia entera → 401 (#142).
- **Grace marker** — flag Redis efímero que marca un refresh recién rotado
  y apunta al `jti` del sucesor. Habilita la idempotencia del retry dentro
  de la ventana `AUTH_REFRESH_REUSE_GRACE_SECONDS` (#142).

## Infra

- **Fase MVP** — etapa actual. DB sobre Supabase como Postgres
  gestionado.
- **Fase V2** — etapa post-validación. DB Postgres self-hosted
  (ADR-005); la IA sigue por API (ADR-028).
- **VPS** — servidor en Argentina / LATAM, sin GPU, donde corren el API
  y los workers de Celery. No sirve modelos (ADR-028 D8).
- **Cutover** — momento de la migración Supabase → self-hosted.
- **R2** — Cloudflare R2, storage de objetos.
- **Cloudflare Tunnel** — túnel desde la VPS al edge de Cloudflare,
  evita exponer puertos.

## Equipo

- **CODEOWNER** — persona del equipo responsable de aprobar PRs que
  tocan ciertos paths. Definidos en `.github/CODEOWNERS`.
- **Tabla sagrada review** — review humano explícito (1 aprobación
  formal en el PR, además del operador autor) + tests pasando,
  requerido para migraciones que tocan capas de memoria.

## IA / agentes

- **AGENTS.md** — contrato de comportamiento para IAs que trabajan
  sobre el repo.
- **Adapter** — archivo `.md` o carpeta específica de una
  herramienta de IA (Claude, Codex, Gemini). Solo punteros, no
  contenido propio.
- **Sub-agent** — agente especializado dentro de una sesión (planner,
  executor, verifier, etc.).
- **ADR** — Architecture Decision Record. Documento corto en
  `docs/architecture/adrs/` que captura una decisión técnica.
