# apps/backend — Backend de Ynara

FastAPI + Pydantic v2 + SQLAlchemy 2 async + Alembic + Celery + uv.
Cabeza del producto: **router LLM, memoria y tools del agente**. La
inferencia se consume por API (ADR-028) con dos alias de modelo:
`conversacional` y `agente`. Los datos de usuario solo van a los
proveedores aprobados y solo para la inferencia (regla #4).

## Antes de tocar nada

1. [`../../AGENTS.md`](../../AGENTS.md) — 10 reglas no negociables del repo.
2. [`./AGENTS.md`](./AGENTS.md) — **reglas + mapa operativo del backend** (gates, capa LLM, tests, playbooks). Lectura obligatoria.
3. [`./docs/`](./docs/) — catálogos vivos (models, endpoints, tools, migraciones).

## Estado

- **Construido y mergeado**: capa LLM **M0–M8** completa — config single-source, cliente resiliente de los proveedores por API (pool + circuit breaker + respaldo), prompts por modo, tools de agente (calendar + task reales, síncronas en el chat — ADR-022; reminder stub), tools `memory.*` (M7), router LLM (M8). Auth JWT real (`/v1/auth` register/token/me). Endpoints `/v1/chat` (sync + SSE streaming), `/v1/sessions` (list/detail/close), `/v1/memory` (list/detail/export, PATCH/DELETE individual por capa, wipe total). Persistencia de turnos crudos cifrados (`conversation_turns`, operativa) en `/v1/chat` + consolidación **episódica** async al cerrar la sesión (`consolidate_session`: resume con el alias `agente`, embeddea, cifra y persiste en `episodic_memory`, purga los turnos). Workers Celery: consolidación async (semantic/procedural + episódica, con reintentos ante fallas transitorias del proveedor e idempotente por turno) + decay procedural. Cifrado AES-256-GCM per-user (`app/core/crypto.py`). Guard anti-prod (`app/core/db_guard.py`). Migraciones: cadena de **15** (de `initial_schema` hasta `users.preferences`); 12 tablas, 9 enums, pgvector + pgcrypto. Ver [`docs/MIGRATIONS.md`](./docs/MIGRATIONS.md).
- **Inferencia**: `LLM_BACKEND=api` arma un `OpenAICompatClient` por proveedor de
  `LLM_PROVIDERS`, en orden de prioridad, detrás de un `ResilientClient` con respaldo
  (ADR-028). El código pide un alias (`conversacional` o `agente`) y cada proveedor lo
  traduce a su id de modelo. Default `LLM_BACKEND=fake` (`FakeLlmClient` determinista,
  sin red); production no arranca con el fake. `EMBEDDING_BACKEND=api` arma el
  `ApiEmbeddingClient` contra el proveedor de embeddings (`EMBEDDING_BASE_URL`,
  `EMBEDDING_API_KEY`, `EMBEDDING_MODEL`, default `BAAI/bge-m3`); el default es `fake`
  y production no arranca con él. El reranker es un passthrough en todos los entornos
  (ADR-028 D5).

## Estructura

```
app/
├── main.py          # entrypoint FastAPI (lifespan, CORS, 13 routers v1)
├── enums.py         # StrEnums cross-domain (Mode, MemoryLayer, LlmModel, AuditOperation, TurnRole, EventStatus, TaskStatus, DevicePlatform, ReminderStatus)
├── core/            # config (Settings lazy), constants (EMBEDDING_DIM, alias de modelo), crypto, deps (engine async lazy), security (JWT PyJWT+bcrypt — ADR-015), db_guard, ratelimit, token_store, observability, paths, urls (URL base de un proveedor)
├── api/v1/          # routers, un archivo por dominio (auth, chat, sessions, events, tasks, reminders, devices, memory, modes, users, health) + subpaquete admin/ (metrics, playground)
├── models/          # SQLAlchemy 2 (user, session, conversation_turn operativa, calendar_event, task, reminder, device_token, admin_audit, memory 🔴, audit 🔴)
├── schemas/         # Pydantic v2 (mirror de models + payloads de API; *_api.py: envelopes)
├── services/        # lógica de negocio sin framework (auth, chat, memory, admin_metrics, notifications) + stores de dominio operativo (calendar.py, tasks.py, reminders.py, devices.py)
├── llm/             # capa de inferencia — config, clients/, prompts/, tools/, router (M8)
├── memory/          # 🔴 wrappers de las 3 capas sagradas + AuditStore (escritura de audit_log)
│                     #   + módulos neutrales (no sagrados): hashing.py (digests de audit_log),
│                     #   embedding.py (embed_one), config.py (decay+retention de [memory]),
│                     #   conversation_turns.py (store del buffer operativo de turnos)
├── workers/         # Celery (consolidación async + beat_schedule de jobs periódicos)
└── workflows/       # consolidation, decay, episodic_retention, audit_retention, agent_pass (dormant), _engine, _turn_marker

alembic/             # Migraciones (env.py acepta TEST_DATABASE_URL)
docs/                # Catálogos vivos (MODELS, ENDPOINTS, TOOLS, MIGRATIONS)
tests/               # Pytest async (unit + integration con DB real)
```

Detalle de la capa LLM y los gates: [`./AGENTS.md`](./AGENTS.md).

## Comandos

Canónico con `uv`. Si `uv` no está en PATH (p.ej. Windows), reemplazá
`uv run python` por `.venv\Scripts\python.exe`.

```sh
uv sync                                            # instalar deps (dev: uv sync --extra dev)
uv run uvicorn app.main:app --reload --port 8080   # dev server
uv run pytest                                      # tests (excluye integration)
uv run pytest -m integration                       # tests de DB real (necesita TEST_DATABASE_URL)
uv run ruff check . && uv run ruff format .        # lint + format
uv run alembic upgrade head                        # aplicar migraciones
uv run celery -A app.workers.celery_app worker     # worker async
```

## Variables de entorno

Copiar `.env.example` a `.env` (gitignored). Críticas:

| Var | Para qué |
|---|---|
| `DATABASE_URL` | Postgres (`postgresql+asyncpg://...`). MVP: session pooler de Supabase. |
| `REDIS_URL` | Broker + result backend de Celery. |
| `JWT_SECRET` | Firma de tokens (auth). |
| `LLM_BACKEND` | `fake` (default, `FakeLlmClient` sin red) o `api` (proveedores de `LLM_PROVIDERS`). Production exige `api`. |
| `LLM_PROVIDERS` | Lista JSON de proveedores en orden de prioridad: `name`, `base_url`, `models` (`{alias: model_id}` con `conversacional` y `agente`) y `supports_reasoning_effort` (ADR-028). |
| `LLM_API_KEY_<NAME>` | Credencial de cada proveedor (`<NAME>` es su `name` en mayúsculas). Secreto: solo en el entorno, nunca en el JSON, la config ni los logs. |
| `EMBEDDING_BACKEND` | `fake` (default, `FakeEmbeddingClient` sin red) o `api` (el proveedor de `EMBEDDING_BASE_URL`). Production exige `api`. |
| `EMBEDDING_BASE_URL` | URL base de la API del proveedor de embeddings, con el `/v1` (https). |
| `EMBEDDING_API_KEY` | Credencial del proveedor de embeddings. Secreto, igual que las `LLM_API_KEY_<NAME>`. |
| `EMBEDDING_MODEL` | Id del modelo en el proveedor (default `BAAI/bge-m3`); tiene que dar vectores de 1024 dimensiones (ADR-028 D4). |
| `TEST_DATABASE_URL` | Solo tests de integración — DB **dedicada**, nunca prod. |
| `MEMORY_ENCRYPTION_MASTER_KEY` | Cifrado de memoria (implementado, ADR-007 D3). |

Los alias de modelo (`role`, `writes_memory` y `context_tokens`) y el alias de cada
modo NO van en `.env`: viven en [`../../ynara.config.json`](../../ynara.config.json).

## Base de datos: dev vs prod

> **Regla de oro:** en desarrollo apuntás a una DB **local**; a la DB de
> **prod** (Supabase) sólo se apunta a propósito. Un guard de arranque
> (`app/core/db_guard.py`, llamado en el lifespan de `app/main.py`) **aborta el
> boot** si la app corre en modo NO-producción contra un host de prod conocido
> sin opt-in explícito. Esto evitó que se repita el incidente del 2026-05-31,
> donde una corrida en dev contra la DB de prod creó y borró un usuario real.

Sólo hay **un** `DATABASE_URL` activo en `.env`. Cambiar entre dev y prod es
cambiar ese valor (y, para prod intencional en dev, setear un flag).

### DEV — DB local (default seguro)

1. **Levantá el Postgres local con pgvector.** Reusá el mismo contenedor que
   los tests de integración (puerto `5433`). Si todavía no lo tenés:

   ```sh
   docker run -d --name ynara-pg -p 5433:5432 \
     -e POSTGRES_PASSWORD=test pgvector/pgvector:pg16
   ```

2. **Creá la DB de dev** `ynara_dev` (una sola vez):

   ```sh
   docker exec ynara-pg psql -U postgres -c "CREATE DATABASE ynara_dev;"
   ```

   Alternativa: reusá directamente la DB de tests `ynara_test` en vez de crear
   `ynara_dev` (ojo: tu data de dev y la de tests compartirían DB).

3. **Apuntá `DATABASE_URL` a la DB local** en tu `.env`:

   ```sh
   DATABASE_URL=postgresql://postgres:test@localhost:5433/ynara_dev
   ```

4. (Primera vez) aplicá las migraciones contra la DB de dev:

   ```sh
   uv run alembic upgrade head     # o: .venv\Scripts\python.exe -m alembic upgrade head
   ```

Con un host local el guard **no se dispara**: la app boota normal.

Atajo de un solo comando: `scripts/run-local.ps1` (Windows) o
`scripts/run-local.sh` (Linux/macOS) exportan ese `DATABASE_URL` de dev y
levantan uvicorn. Ver [`scripts/`](./scripts/).

### PROD — Supabase (intencional)

Hay dos formas, según el caso:

- **Deploy real:** `ENVIRONMENT=production`. El guard nunca aplica y la app
  boota contra prod normalmente (también endurece `JWT_SECRET`, oculta `/docs`,
  etc.).
- **Corrida dev-contra-prod consciente** (debug puntual, sin cambiar
  `ENVIRONMENT`): apuntá `DATABASE_URL` a Supabase **y** activá el opt-in:

  ```sh
  # .env  (o export en la shell)
  DATABASE_URL=postgresql://postgres:[password]@db.[ref].supabase.co:5432/postgres
  YNARA_ALLOW_PROD_DB=1
  ```

  Sin `YNARA_ALLOW_PROD_DB=1`, el guard aborta el arranque con este mensaje
  (sólo muestra el **host**, nunca el connection string con credenciales):

  ```text
  RuntimeError: Guard anti-prod: la app está booteando en modo NO-producción
  contra una base de datos que parece de PRODUCCIÓN (host: '...supabase.com').
  Esto fue un incidente real: una corrida en dev contra esta DB creó y borró un
  usuario en producción.

  Qué hacer:
    • Para DEV (lo habitual): apuntá DATABASE_URL a tu Postgres LOCAL, por ej.
        DATABASE_URL=postgresql://postgres:test@localhost:5433/ynara_dev
      (mismo contenedor pgvector de los tests; ver 'Base de datos: dev vs prod'
      en apps/backend/README.md).
    • Si querés correr dev CONTRA PROD a propósito: exportá YNARA_ALLOW_PROD_DB=1
      (corrida consciente, bajo tu responsabilidad).
    • En el deploy de producción esto no aplica: ENVIRONMENT=production boota
      normal.
  ```

### Resumen rápido

| Quiero… | `ENVIRONMENT` | `DATABASE_URL` | `YNARA_ALLOW_PROD_DB` |
|---|---|---|---|
| Dev contra DB local (default) | `development` | `...@localhost:5433/ynara_dev` | (sin setear) |
| Deploy de producción | `production` | Supabase | (sin setear, no aplica) |
| Dev contra prod a propósito | `development` | Supabase | `1` |

Hosts que el guard considera de prod: `*.supabase.co`, `*.supabase.com` y
cualquiera que contenga `pooler.supabase`. Tests bajo pytest nunca disparan el
guard (overridean `get_db` con la DB de tests).
