# apps/backend/AGENTS.md — Reglas y mapa del backend

> Fuente canónica del repo: [`../../AGENTS.md`](../../AGENTS.md) (10 reglas
> no negociables). Este archivo es el **contrato + mapa operativo** del
> backend: si vas a tocar `apps/backend`, leelo entero antes de editar.

---

## 0. Gates bloqueantes — parar y pedir humano

| Gate | Qué lo dispara | Qué hacer |
|---|---|---|
| **Tablas sagradas** (regla #3) | Tocar `app/memory/`, `app/models/{memory,audit}.py`, `app/schemas/{memory,audit}.py` o `alembic/versions/` (tablas `semantic_memory`, `episodic_memory`, `procedural_memory`, `audit_log`) | Tests + **1 aprobación humana explícita** en el PR, distinta del operador que lo abrió. Commit aislado para que la review inspeccione un diff específico. |
| **Secrets** (regla #2) | `.env`, tokens, claves, certificados | Nunca leer, copiar, mover ni commitear. Si ves uno expuesto, alertá y no toques nada. |
| **Datos y proveedores de IA** (regla #4) | Inferencia o logging de contenido de usuario | La IA se consume solo desde `app/llm/clients/providers/` y solo con proveedores aprobados (4b, ADR-028): hasta cerrar las compuertas de ADR-028 reciben solo datos sintéticos. Nada de texto de usuario en logs, mensajes de error o Sentry (4a). |
| **Instalación / prod** (regla #1) | `uv add`, `alembic upgrade head` en prod, cambios mayores a `pyproject.toml` | Confirmación humana explícita antes. |
| **DB de prod en dev** (incidente 2026-05-31) | Correr la app real (uvicorn / app real sin override de `get_db`) con `DATABASE_URL` apuntando a Supabase prod | El guard de arranque (`app/core/db_guard.py`, en el lifespan) **aborta el boot** salvo `ENVIRONMENT=production` o opt-in `YNARA_ALLOW_PROD_DB=1`. Para dev usá la DB **local** (`localhost:5433/ynara_dev`). Switch documentado en [`README.md` → "Base de datos: dev vs prod"](./README.md#base-de-datos-dev-vs-prod). |

---

## 1. Stack y estado actual

- **FastAPI + Pydantic v2 strict + SQLAlchemy 2 async + Alembic + Celery + uv.** Python ≥ 3.12.
- **DB**: Postgres + pgvector. MVP en Supabase vía **session pooler** (puerto 5432, IPv4; la conexión directa es IPv6-only). V2 self-hosted (ADR-005).
- **LLM**: inferencia por API (ADR-028) con dos alias de modelo — `conversacional` (estudio, bienestar y vida; solo lee memoria, sin tools) y `agente` (productividad, memoria y la consolidación; lee y escribe memoria, llama tools). Los proveedores van en `LLM_PROVIDERS`, en orden de prioridad y con respaldo, y cada uno traduce el alias a su id de modelo. Ver ADR-002 (roles) y ADR-028 (inferencia por API).

**Construido y mergeado** (capa LLM M0–M8 completa):

- Config single-source, cliente resiliente de los proveedores por API (pool + circuit breaker + respaldo), prompts por modo, framework de tools (calendar + task reales, síncronas en el chat — ADR-022; reminder stub), tools `memory.*` (M7), router LLM (M8). Auth JWT real (`/v1/auth` register/token/me). Endpoints `/v1/chat` (sync + SSE streaming), `/v1/sessions` (list/detail/close), `/v1/memory` (list/detail/export, PATCH/DELETE individual por capa, wipe total). Workers Celery: consolidación async + decay procedural + retention de audit_log (24 meses). Cifrado AES-256-GCM per-user (`app/core/crypto.py`). Guard anti-prod (`app/core/db_guard.py`). Migraciones: cadena de **15** (de `initial_schema` hasta `users.preferences`, pasando por trigger block-update de `audit_log`, tabla `conversation_turns` + `turn_role_enum`, admin_audit, calendar_events, tasks, los índices operativos, `users.time_zone`, `device_tokens`, `reminders` y `users.preferences`); total 12 tablas, 9 enums, pgvector + pgcrypto. Persistencia de turnos para consolidación episódica: tabla `conversation_turns` + `consolidate_session` al cerrar la sesión (#209).

**Pendiente** (no empezar sin leer el plan):

- Compuertas de [ADR-028](../../docs/architecture/adrs/ADR-028-inferencia-por-api.md) antes de mandarle datos reales a un proveedor: hasta cerrarlas, solo datos sintéticos. El chat y los embeddings ya hablan con los proveedores por API (`LLM_BACKEND=api`, `EMBEDDING_BACKEND=api`), el reranker es un passthrough en todos los entornos (ADR-028 D5), y en dev sin flags corren los Fakes deterministas. Roadmap de memoria: [`../../docs/planning/BACKEND-MEMORY-ROADMAP.md`](../../docs/planning/BACKEND-MEMORY-ROADMAP.md).

**Implementados en #63**: rate-limit (token/register), refresh single-use y logout (blocklist Redis) — ver [`docs/ENDPOINTS.md`](./docs/ENDPOINTS.md). **Hardening en #142**: reuse-detection a nivel familia/`sid` en `/refresh` (grace window retry-safe + breach → family-revoke) y logout con revocación de familia entera.

---

## 2. Mapa del código (`app/`)

```
app/
├── main.py            # entrypoint FastAPI (lifespan, CORS, 13 routers v1, scrubbing de validación)
├── enums.py           # StrEnums cross-domain (Mode, MemoryLayer, LlmModel, AuditOperation, TurnRole, EventStatus, TaskStatus, DevicePlatform, ReminderStatus)
├── core/
│   ├── config.py      # Settings (pydantic-settings); get_settings() cacheado y lazy. LLM_BACKEND (fake|api), LLM_PROVIDERS y las credenciales LLM_API_KEY_<NAME> (SecretStr); EMBEDDING_BACKEND (fake|api) con EMBEDDING_BASE_URL, EMBEDDING_API_KEY (SecretStr), EMBEDDING_MODEL, EMBEDDING_BATCH_SIZE y EMBEDDING_TIMEOUT_S; production no arranca con el LLM o el embedder fake, sin proveedores o sin una credencial
│   ├── constants.py   # constantes cross-módulo (EMBEDDING_DIM: única fuente de verdad de la dim del embedding; DEFAULT_EMBEDDING_MODEL / DEFAULT_EMBEDDING_BATCH_SIZE: defaults del embedder por API; MODEL_ALIAS_CONVERSATIONAL / MODEL_ALIAS_AGENT / MODEL_ALIASES: los alias de modelo)
│   ├── crypto.py      # AES-256-GCM per-user (HKDF-SHA256); encrypt_for_user / decrypt_for_user
│   ├── db_guard.py    # guard anti-prod en lifespan: aborta el boot si DATABASE_URL apunta a Supabase prod sin opt-in
│   ├── deps.py        # engine async LAZY (get_engine/get_sessionmaker cacheados, no module-level) + get_db (AsyncSession por request); dispose en el shutdown del lifespan
│   ├── observability.py  # init_sentry() con before_send que scrubea PII (cuerpo, headers auth, user, extras) — regla #4
│   ├── paths.py       # resolución de rutas del repo (raíz, ynara.config.json) sin hardcodear cwd
│   ├── ratelimit.py   # rate-limit fail-open para login/register/refresh/chat/export, memory-wipe (`memory:ratelimit:wipe:`, solo el execute) y sessions (`sessions:ratelimit:read:`, bucket único list/get/close) (contadores Redis vía TokenStore)
│   ├── security.py    # JWT/hashing — implementado con PyJWT + bcrypt directo (ADR-015, no python-jose/passlib): create_access_token, verify_access_token, hash_password, verify_password
│   ├── token_store.py # blocklist de jti + revocación por familia (sid) + contadores genéricos; Protocol + RedisTokenStore + InMemoryTokenStore (tests)
│   └── urls.py        # validated_base_url(): URL base de un proveedor de IA (https; http solo contra la propia máquina), la usan Settings y los clientes de providers/
├── api/v1/            # routers, un archivo por dominio (health, auth, chat, sessions, events, tasks, reminders, devices, today, memory, modes, users) + subpaquete admin/ (metrics, playground, connectivity); privados _http.py / _sessions.py
├── models/            # SQLAlchemy 2 (user, session, conversation_turn, calendar_event, task, reminder, device_token, admin_audit, memory 🔴, audit 🔴) — base.py: mixins UUIDPK/Timestamp
├── schemas/           # Pydantic v2 mirror de models + payloads de API (*_api.py: envelopes de presentación)
├── services/          # lógica de negocio SIN framework, deps por argumento: auth, chat, memory, admin_metrics, notifications (push noop) + stores de dominio operativo (calendar.py: CalendarEventStore; tasks.py: TaskStore; reminders.py: ReminderStore; devices.py: DeviceTokenStore; ≠ Celery). Agenda/Tareas/Recordatorios son dominios ordinarios → layer-split acá (ADR-011 D1), no feature-packages
├── llm/               # capa de inferencia — ver §3
├── memory/            # 🔴 wrappers de las 3 capas sagradas (M7, implementado); audit.py: AuditStore (único punto de inserción en audit_log — sagrado, no editar). Módulos neutrales (no sagrados, siblings de COMPORTAMIENTO, no tocan columnas): hashing.py (digests de audit_log: compute_record_hash + procedural_hash_payload), embedding.py (embed_one compartido), config.py (loader de thresholds de `[memory]`: decay + retention, #211), conversation_turns.py (store del buffer operativo)
├── workers/           # Celery (celery_app.py + beat_schedule de los jobs periódicos) — autodiscovery en app.workflows
└── workflows/         # jobs async: consolidation, decay procedural, episodic_retention, audit_retention (purge_audit_log), agent_pass (DORMANT, ADR-022), _engine.py (worker_session NullPool compartido)
```

**Auth está layer-split a propósito (ADR-011).** Su superficie se reparte por capa —
`api/v1/auth.py` (router), `services/auth.py` (dominio), `schemas/auth.py` (wire),
`core/security.py` (JWT/bcrypt) y los auth-deps de `core/deps.py` (`get_current_user`,
`get_token_store`, `UNAUTHORIZED_DETAIL`, …)— porque auth es un **dominio ordinario**
(1 router + 1 service + 1 schema), no un subsistema pesado. Los feature-packages
(`llm/`, `memory/`) se reservan a subsistemas **pesados y autocontenidos**.
**Agenda (`calendar_events`) y Tareas (`tasks`) siguen el mismo criterio:** son
dominios operativos ordinarios (1 store + router + schema), así que sus stores
(`CalendarEventStore` / `TaskStore`) viven en `services/calendar.py` /
`services/tasks.py`, **no** en un package propio top-level (`app/calendar/`,
`app/tasks/` se eliminaron: eran feature-packages finos que violaban D1).
`core/token_store.py` y `core/ratelimit.py` se quedan en `core/` por ser **infra
compartida** (el rate-limit lo usan chat y memory-export, no solo auth): moverlos a un
paquete de auth invertiría la dependencia `core→dominio`. No crear `app/auth/` salvo que
auth cruce el umbral de peso (ver ADR-011 D5).

---

## 3. La capa LLM (`app/llm/`) — cómo está armada

```
llm/
├── config.py          # LlmRuntimeConfig + load_llm_config(): alias de modelo (role, writes_memory, context_tokens) y modos de ynara.config.json, fail-fast
├── schemas.py         # ChatRequest/Response, ChatMessage, ToolSpec/ToolCall, CompletionResult/Chunk, ModelHealth
├── errors.py          # taxonomía LlmError (transient/permanent/semantic, con LlmAuthError, LlmProviderRejectedError, LlmRateLimitError y EmbeddingDimensionError) + degraded_response(); NUNCA filtra texto de usuario
├── context.py         # build_memory_context() + render_context_block(): recupera capas, formatea en Markdown, aplica presupuesto de tokens
├── memory_engine.py   # QwenMemoryEngine: extrae MemoryOp del turno, aplica contra los stores vía apply_ops(); el módulo más grande de llm/
├── tool_loop.py       # run_tool_loop(): itera LLM call + ejecución de tools (guard MAX_TOOL_ITERATIONS=5) hasta finish_reason stop/length/degraded
├── clients/
│   ├── base.py        # Protocols LLMClient + ToolCallParser
│   ├── providers/     # clientes de proveedores de IA por API (regla #4b, ADR-028); sin SDKs, httpx inyectado
│   │   ├── openai_compat.py  # OpenAICompatClient: Chat Completions con Bearer (SecretStr), alias → model_id, reasoning_effort por proveedor, health GET /models
│   │   ├── embeddings.py     # ApiEmbeddingClient: POST /embeddings con Bearer (SecretStr), lotes de hasta EMBEDDING_BATCH_SIZE textos, cada vector de EMBEDDING_DIM o EmbeddingDimensionError (sin recortar ni rellenar), health GET /models
│   │   ├── _http.py          # BearerAuth + status HTTP → LlmError (401/403 credencial, 429 con Retry-After, 400 contexto excedido, otro 4xx rechazo propio del proveedor, 5xx) + errores de transporte (la URL base la valida app/core/urls.py)
│   │   ├── _wire.py          # mensajes, tools y respuestas en el formato de Chat Completions
│   │   ├── _stream.py        # SSE de Chat Completions → CompletionChunk
│   │   ├── _think.py         # separa el razonamiento inline (<think>) del texto, también en streaming; un <think> a mitad del texto que no cierra queda como texto
│   │   └── _tool_names.py    # nombres de tools `.` ↔ `__` (la API no acepta puntos), también en streaming
│   ├── parsers.py     # OpenAIToolCallParser (parse + accumulate de tool calls OpenAI)
│   ├── fakes.py       # FakeLlmClient (tests, sin red)
│   ├── circuit.py     # CircuitBreaker (stdlib, sin libs); would_allow() consulta sin consumir la prueba HALF_OPEN
│   ├── pool.py        # ClientPool + RoutingStrategy + build_provider_pool (proveedores en orden de prioridad)
│   ├── resilient.py   # ResilientClient: retry+backoff → respaldo → respuesta degradada; rechazo del proveedor (credencial, 4xx propio) sin reintento, 429 largo o sin Retry-After al respaldo, breaker solo por caídas; stream con respaldo antes del primer chunk
│   ├── _resilience.py # política del ResilientClient: qué se reintenta o pasa al respaldo, la espera de un 429 con margen en el presupuesto (RetryPolicy) y el breaker de cada candidato (BreakerTally)
│   ├── embedding.py   # Protocol EmbeddingClient + FakeEmbeddingClient determinista (dev y CI); el cliente real es providers/embeddings.py
│   ├── factory.py     # build_llm_client(): fake por default o, con LLM_BACKEND=api, un OpenAICompatClient por proveedor detrás del ResilientClient (check_resilience_budget verifica el presupuesto); build_embedder(): FakeEmbeddingClient por default o, con EMBEDDING_BACKEND=api, ApiEmbeddingClient; build_reranker(): siempre el FakeReranker passthrough (RERANKER_PASSTHROUGH, sin settings)
│   └── reranker.py    # Protocol Reranker + FakeReranker passthrough, el reranker de todos los entornos (ADR-028 D5); RERANKER_PASSTHROUGH es el nombre con que se reporta
├── prompts/           # shared.py (identidad/voz/seguridad) + loader.py (load_prompt(mode)) + 1 SYSTEM_PROMPT por modo
├── tools/             # base.py (Tool Protocol, to_spec, tool_error, IsoDatetime) + registry.py (default_registry: stubs playground) + agent_registry.py (_AGENT_TOOL_BUILDERS: vía prod ADR-022) + calendar.py + task.py + reminder.py + memory.py
└── router.py          # M8 — orquesta modo→alias→memoria→tools. Implementado.
```

**Invariantes que NO se rompen:**

- **Inferencia por API** (ADR-028): el código pide un alias (`conversacional` | `agente`), nunca un modelo concreto. Cada entrada de `LLM_PROVIDERS` (`name`, `base_url`, `models {alias: model_id}`, `supports_reasoning_effort`) es un `OpenAICompatClient` que traduce el alias a su id de modelo; van en orden de prioridad detrás del `ClientPool`. La única HTTP a proveedores vive en `clients/providers/` (regla #4b) y la credencial de cada uno es `LLM_API_KEY_<NAME>` (`SecretStr`), nunca en el JSON, la config ni los logs. Las tools viajan como `namespace__accion` y vuelven con punto.
- **Resiliencia**: cadena **principal → respaldo (el siguiente proveedor) → respuesta degradada**, con 20 s por request, 3 intentos y 90 s de presupuesto total; `check_resilience_budget` verifica al armar el cliente que el respaldo entre después del peor caso del principal. Nunca propaga una excepción de infra al caller.
- **Errores** (`errors.py`): la taxonomía nunca expone contenido del usuario en `__str__`/logs (regla #4).
- **Prompts** (`app/llm/prompts/`): el system va con lo fijo primero (identidad, voz, reglas y prompt del modo) y lo variable al final (preámbulo de fecha/hora y bloque de memoria), para la cache de prefijos del proveedor. Cuentan la inferencia con honestidad (modelos de lenguaje de proveedores externos; ante preguntas de privacidad, la política de privacidad) y no prometen inferencia local: `tests/llm/test_prompts.py` lo exige sobre todo system que ve el modelo y fija textual el protocolo de crisis de Bienestar.
- **Razonamiento por rol** (ADR-028 D3): `thinking` es intención, no un switch del motor. `conversacional` pide no razonar y `agente` pide razonar; el cliente manda `reasoning_effort` solo para `True` y solo si el proveedor declara `supports_reasoning_effort`. Para `False` no manda nada (el valor que lo apaga cambia de proveedor a proveedor).
- **Tools**: los errores vuelven SIEMPRE como dict estructurado `{"error": {"code", "message"}}` — el modelo **jamás** ve un traceback (el `ToolRegistry` blinda con `except Exception`). Fechas vía el tipo `IsoDatetime` (solo ISO 8601, rechaza epoch). **`conversacional` no llama tools** (solo `agente`); el registry por defecto (`default_registry()`) NO incluye `memory.*`: se construye por separado con `memory_registry(semantic_store)` (M7 implementado) y el router lo combina por modo cuando la memoria está habilitada.
- **Config single-source**: los proveedores (`LLM_PROVIDERS`) y sus credenciales viven en `Settings`/entorno; `ynara.config.json[models]` tiene los dos alias con `role`, `writes_memory` y `context_tokens` (el presupuesto de contexto que reparte el router), y cada modo de `[modes]` apunta a un alias. `load_llm_config()` valida coherencia (fail-fast: los dos alias con su rol, `conversacional` sin escribir memoria ni llamar tools, cada modo apuntando a un alias que existe). En production, `Settings` no arranca con el LLM fake, sin proveedores o sin la credencial de alguno, ni con el embedder fake o sin la URL, la credencial o el modelo de su proveedor (`EMBEDDING_*`). Los thresholds de decay procedural (`decay_interval_days`, `decay_factor`, `stale_threshold`, `hard_delete_threshold`, `hard_delete_min_days`, ADR-007 D1) viven en `ynara.config.json[memory]` y los parsea `app/memory/config.py` (`load_decay_config()`, mismo patrón fail-fast que `llm/config.py`: Pydantic frozen+strict, `MemoryConfigError`, cache `lru_cache`); `app/workflows/decay.py` los lee de ahí. Es **default-safe**: si el bloque `[memory]` no trae las keys, cae a los defaults de ADR-007 D1 (no rompe el job, #211). La **retención episódica diferenciada** (`retention_default_days`, `retention_sensitive_days`, `retention_sensitive_min_days`, `retention_sensitive_max_days`, ADR-007 D2) vive en el **mismo** bloque `[memory]` y la parsea `load_retention_config()` (`RetentionConfig`) con el MISMO patrón (frozen/strict/`extra='forbid'` + default-safe + fail-fast en `MemoryConfigError`).

---

## 4. Convenciones

- **Async-first**: `async def` en rutas y servicios; SQLAlchemy 2 async (`AsyncSession`).
- **Pydantic v2 strict** en schemas. Sin `Any` salvo justificación puntual.
- **Type hints completos.** Ruff con `E/W/F/I/B/C4/UP/ASYNC/S/RUF`, line-length 100 (config en `pyproject.toml`).
- **Services sin framework**: la lógica en `app/services/` no importa FastAPI ni el *engine/sessionmaker* de SQLAlchemy — recibe sus dependencias por argumento (testeable). Usar `select(...)` y tipar la `AsyncSession` recibida SÍ está OK (es el patrón de todos los services: `auth`/`chat`/`memory`/`admin_metrics` y los stores `calendar`/`tasks`); lo prohibido es instanciar el engine o atar la sesión a nivel de módulo. "SQLAlchemy directo" = construir/poseer la conexión, no consultar con la sesión inyectada.
- **`get_settings()`** es el acceso a config (cacheado con `lru_cache`); no se instancia `Settings` a nivel de módulo (así los imports no exigen `.env`).
- **Consolidación de memoria siempre async** (Celery). Nunca en el path de respuesta.
- **Naming de schemas** — dos patrones según colisión de nombre:
  - **Infijo `Http`** (`ChatHttpRequest` / `ChatHttpResponse`): se usa cuando el nombre pelado ya existe en un schema de dominio LLM (`app/llm/schemas.py` tiene `ChatRequest`/`ChatResponse`). El infijo evita la colisión de nombres y deja claro que es el contrato wire del endpoint HTTP.
  - **Sufijo `Request` / `Out` pelado** (`RegisterRequest`, `LoginRequest`, `TokenOut`): se usa cuando no hay colisión con ningún schema de dominio (auth no tiene `Register`/`Login`/`Token` en otro módulo).
  - **Envelopes de presentación** (`*Page`, `*Response`, `*Preview`, `*Confirm`, `*Result`): viven en los archivos `*_api.py` (p.ej. `memory_api.py`, `session_api.py`) y paginan / agrupan los `*Out` sagrados sin tocarlos. Ejemplos: `SemanticMemoryPage`, `MemoryGroupedResponse`, `MemoryWipePreview`, `MemoryWipeConfirm`, `MemoryWipeResult`, `SessionListPage`.
- **Commits**: Conventional Commits en español, descripción imperativa o noun-phrase del artefacto (regla #6), **atómicos** (regla #7). Tablas sagradas en commit aislado.

---

## 5. Tests

- **pytest async** (`asyncio_mode = "auto"`). Runner canónico: `uv run pytest`. Si `uv` no está en PATH (p.ej. Windows), usar el venv: `.venv\Scripts\python.exe -m pytest`.
- **Markers**: el run default excluye integración (`addopts = -m 'not integration'`). Los tests `@pytest.mark.integration` tocan **DB real** y se corren aparte.
- **DB real, sin mocks de DB** (los mocks de DB ocultan bugs de migración). Los unit tests de la capa LLM sí usan test doubles de **red** (`FakeLlmClient`, `httpx.MockTransport`) — eso está OK, no es un mock de DB.
- **Fixtures de DB** (`tests/conftest.py`): `db_url` / `db_engine` / `db_session` contra `TEST_DATABASE_URL` — una DB de tests **dedicada**, NUNCA prod ni la Supabase real. Sin esa env var, los tests de DB **skipean**. CI necesita un Postgres con pgvector ≥ 0.5.0 (índices HNSW).

```sh
uv run pytest                                  # run default (sin integration)
uv run pytest -m integration                   # solo integración (necesita TEST_DATABASE_URL)
uv run ruff check . && uv run ruff format .    # lint + format
```

---

## 6. Migraciones

Política completa: [`docs/MIGRATIONS.md`](./docs/MIGRATIONS.md).

- Naming: `YYYYMMDD_HHMM_descripcion.py`. Una migración = un cambio lógico. `downgrade()` siempre.
- `alembic/env.py` importa `app.models` (paquete completo) para que autogenerate / `alembic check` detecten todos los modelos. Acepta `TEST_DATABASE_URL` para correr contra una DB de tests.
- **Tablas sagradas** → tests + 1 aprobación humana explícita (regla #3).

---

## 7. Playbooks

**Agregar un endpoint:** schema Pydantic en `app/schemas/` → modelo en `app/models/` (si es nuevo) → lógica en `app/services/` → ruta en `app/api/v1/` → test de integración → documentar en [`docs/ENDPOINTS.md`](./docs/ENDPOINTS.md).

**Agregar una tool LLM (con efecto real en el chat):** modelo Pydantic de args en `app/llm/tools/<namespace>.py` (fechas con `IsoDatetime`) → `execute` que devuelve resultado o `tool_error(...)`, nunca `raise` → store de dominio + builder `<namespace>_registry(store)` → **registrar el `namespace → builder` en `_AGENT_TOOL_BUILDERS` de `app/llm/tools/agent_registry.py`** → habilitar el namespace en `ynara.config.json[modes][*].tools_enabled` → [`docs/TOOLS.md`](./docs/TOOLS.md) → tests. (Namespace y `name` en singular, como `calendar`/`task`.)

> **Tres superficies de registry — NO confundir** (ver `app/llm/tools/agent_registry.py` y [`docs/TOOLS.md`](./docs/TOOLS.md)). El chat de **producción** consume `build_chat_tool_registry()` (ADR-022), que arma las tools reales desde `_AGENT_TOOL_BUILDERS`. `default_registry()` son **solo los stubs `not_wired` del playground observado** (ADR-019, cero efecto), y `_build_agent_registry()` es la pasada async **dormant** (ADR-021). Una tool registrada **solo** en `default_registry()` —y habilitada en `tools_enabled`— **NO se ejecuta en el chat real**: el builder tiene que estar en `_AGENT_TOOL_BUILDERS`.

**Agregar un modo LLM:** ADR aprobado → entrada en `ynara.config.json[modes]` → `SYSTEM_PROMPT` en `app/llm/prompts/<modo>.py` (registrar en `loader.py`) → [`../../docs/product/MODES.md`](../../docs/product/MODES.md) → tests de invariantes (voz, sin promesas de inferencia local, tools).

**Agregar un modelo:** clase en `app/models/` (mixins de `base.py`) → schema mirror en `app/schemas/` → migración Alembic → [`docs/MODELS.md`](./docs/MODELS.md). Si es tabla de memoria → gate regla #3.

---

## 8. Docs del backend

| Doc | Para qué |
|---|---|
| [`docs/MODELS.md`](./docs/MODELS.md) | Catálogo de modelos SQLAlchemy. |
| [`docs/ENDPOINTS.md`](./docs/ENDPOINTS.md) | Catálogo de endpoints HTTP. |
| [`docs/TOOLS.md`](./docs/TOOLS.md) | Catálogo de tools que puede llamar el alias `agente`. |
| [`docs/MIGRATIONS.md`](./docs/MIGRATIONS.md) | Política de migraciones Alembic. |

**Regla de los catálogos**: si agregás un modelo, endpoint, tool o migración, **actualizás el catálogo correspondiente en el mismo PR**. La review humana lo verifica (CI todavía no).

ADRs relevantes: [ADR-028](../../docs/architecture/adrs/ADR-028-inferencia-por-api.md) (inferencia por API: proveedores, alias de modelo y respaldo; reemplaza ADR-002, ADR-009, ADR-012, ADR-013 y ADR-014), [ADR-005](../../docs/architecture/adrs/ADR-005-supabase-mvp-postgres-selfhosted-v2.md) (Supabase MVP), [ADR-015](../../docs/architecture/adrs/ADR-015-auth-deps-pyjwt-bcrypt.md) (auth con PyJWT + bcrypt directo).
