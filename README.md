# Ynara

> Asistente personal adaptativo con memoria propia, en rioplatense,
> para estudiantes y profesionales jóvenes de LATAM.

[![Web: Next.js 16](https://img.shields.io/badge/Web-Next.js%2016-black)](./apps/web)
[![Mobile: Expo 53+](https://img.shields.io/badge/Mobile-Expo%2053%2B-1B1F23)](./apps/mobile)
[![Backend: FastAPI + Pydantic v2](https://img.shields.io/badge/Backend-FastAPI%20%2B%20Pydantic%20v2-009688)](./apps/backend)
[![LLM: API (conversacional+agente)](https://img.shields.io/badge/LLM-API%20(conversacional%2Bagente)-ff6b6b)](./docs/architecture/adrs/ADR-028-inferencia-por-api.md)
[![DB: Postgres 16 + pgvector](https://img.shields.io/badge/DB-Postgres%2016%20%2B%20pgvector-4169E1)](./docs/architecture/adrs/ADR-004-postgres-pgvector-vs-pinecone.md)
[![AI-friendly](https://img.shields.io/badge/AI--friendly-Claude%20%C2%B7%20Codex%20%C2%B7%20Gemini-8e44ad)](./AGENTS.md)
[![Phase: MVP](https://img.shields.io/badge/Phase-MVP-yellow)](./docs/architecture/adrs/ADR-005-supabase-mvp-postgres-selfhosted-v2.md)
[![License: Proprietary](https://img.shields.io/badge/License-Proprietary-red)](./LICENSE)

Cinco modos (productividad, estudio, bienestar, vida, memoria) sobre dos
alias de modelo (`conversacional` + `agente`), memoria semántica +
episódica + procedural sobre Postgres + pgvector. Ynara responde con modelos
de lenguaje de proveedores externos, consumidos por API, con una lista de
proveedores en orden de prioridad y respaldo (ver
[`ADR-028`](./docs/architecture/adrs/ADR-028-inferencia-por-api.md)).

La cuenta y la memoria viven en la infraestructura de Ynara; los hechos, los
resúmenes de sesión y los turnos de conversación se guardan cifrados por
usuario ([`ADR-007`](./docs/architecture/adrs/ADR-007-memory-decay-retention-encryption.md)).
A cada llamada, el proveedor recibe solo lo que esa llamada necesita. Solo se
trabaja con proveedores con contrato de tratamiento de datos y que no entrenen
con ellos, y la lista se publica en la política de privacidad antes de que
alguno reciba datos reales: hasta cerrar las compuertas de ADR-028, los
proveedores reciben solo datos sintéticos.

- **Humanos**: empezá por [Quick Start](#quick-start) y después
  [`docs/operations/LOCAL-DEV.md`](./docs/operations/LOCAL-DEV.md).
- **Agentes IA**: leer primero [`AGENTS.md`](./AGENTS.md). Es bloqueante.
- **Solo frontend**: [`apps/web/README.md`](./apps/web/README.md) o
  [`apps/mobile/README.md`](./apps/mobile/README.md).
- **Solo backend**: [`apps/backend/README.md`](./apps/backend/README.md) +
  [`apps/backend/docs/`](./apps/backend/docs/).
- **Diseño / producto**: [`IDENTITY.md`](./IDENTITY.md) +
  [`docs/product/`](./docs/product/).

## Repo snapshot

| Métrica | Valor actual |
| --- | --- |
| Modos del producto | 5 (productividad, estudio, bienestar, vida, memoria) |
| Alias de modelo | 2 (`conversacional` + `agente`) |
| Capas de memoria | 3 (semántica, episódica, procedural) |
| Tools del alias `agente` | 8 (calendar×2, reminder×2, memory×4) |
| Apps | 3 (web Next.js 16, mobile Expo 53+, backend FastAPI) |
| Packages compartidos | 5 (shared-types, shared-schemas, ui, config, core) |
| ADRs | índice y estados en [`docs/architecture/README.md`](./docs/architecture/README.md) |
| Skills reutilizables | 6 |
| Archivos tracked | ~500 |
| CODEOWNERS | 3 (@MateoGs013, @bygama, @querques20) |

## Los 5 modos

| Modo | Para qué sirve | Alias de modelo | Tools | Capas de memoria |
| --- | --- | --- | --- | --- |
| **Productividad** | Agendar, recordar, ejecutar | `agente` | calendar, reminder, memory | semántica, episódica |
| **Estudio** | Tutoría, explicar, procesar textos | `conversacional` | — | episódica, procedural |
| **Bienestar** | Descarga emocional, acompañar | `conversacional` | — | procedural, semántica |
| **Vida** | Charla casual, recomendaciones | `conversacional` | — | procedural |
| **Memoria** | Recall explícito de conversaciones | `agente` | memory | las 3 capas |

Detalle de cada modo en [`docs/product/MODES.md`](./docs/product/MODES.md).
Configuración canónica en [`ynara.config.json`](./ynara.config.json).

## Quick Start

> **Antes de instalar deps**: leer [`AGENTS.md`](./AGENTS.md). Regla #1
> pide OK humano para `pnpm install` y `uv sync`. El script `init.sh`
> no instala nada — solo verifica que tengas las herramientas y copia
> los `.env.example` a `.env`.

### 1. Clonar y verificar entorno

```bash
git clone https://github.com/bygama/Ynara.git
cd Ynara
bash scripts/init.sh
```

`init.sh` verifica `node`, `pnpm`, `python3`, `uv` y `docker`, y copia
los `.env.example` que falten. Si algo no está, instalá:

| Herramienta | Instalación |
| --- | --- |
| Node 20+ | https://nodejs.org |
| pnpm 10+ | `corepack enable && corepack prepare pnpm@latest --activate` |
| Python 3.12+ | https://python.org |
| uv | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| Docker | https://docker.com |

### 2. Crear proyecto Supabase (fase MVP)

1. Crear proyecto en [supabase.com](https://supabase.com).
2. Habilitar extensiones en Dashboard → Database → Extensions: activar
   `vector` y `pgcrypto`.
3. Copiar la connection string desde Settings → Database → URI a la
   variable `DATABASE_URL` en `apps/backend/.env`.

En V2 esto se reemplaza por Postgres self-hosted; el resto del flujo
no cambia (ver [`ADR-005`](./docs/architecture/adrs/ADR-005-supabase-mvp-postgres-selfhosted-v2.md)).

### 3. Instalar dependencias (requiere OK humano por regla #1)

```bash
pnpm install                                    # frontend + monorepo
cd apps/backend && uv sync && cd -              # backend Python
```

### 4. Levantar stack local

```bash
make docker-dev-up                              # Redis local
cd apps/backend && uv run alembic upgrade head  # migrar contra Supabase
make dev-backend                                # FastAPI :8080
make dev-web                                    # Next.js :3000 (otra terminal)
make dev-mobile                                 # Expo (opcional)
```

### 5. (Opcional) Conectar un proveedor de IA

Sin proveedor, el backend corre con el cliente fake (`FakeLlmClient`) por defecto.
Para hablar con un proveedor real, en `apps/backend/.env` setear
`LLM_BACKEND=api`, declarar los proveedores en `LLM_PROVIDERS` (lista JSON en orden
de prioridad, cada uno con `name`, `base_url`, `models` y
`supports_reasoning_effort`) y la credencial de cada uno en `LLM_API_KEY_<NAME>`.
El ejemplo está en `apps/backend/.env.example`. En `production` el backend no
arranca con el fake, sin proveedores o sin alguna credencial. Hasta cerrar las
compuertas de [`ADR-028`](./docs/architecture/adrs/ADR-028-inferencia-por-api.md), los
proveedores reciben solo datos sintéticos.

Guía paso a paso completa: [`docs/operations/INSTALL.md`](./docs/operations/INSTALL.md).
Flujo de desarrollo diario: [`docs/operations/LOCAL-DEV.md`](./docs/operations/LOCAL-DEV.md).

## Daily usage

| Comando | Qué hace |
| --- | --- |
| `make dev` | Stack completo (web + mobile + backend) vía Turbo |
| `make dev-web` | Solo Next.js (`:3000`) |
| `make dev-mobile` | Solo Expo |
| `make dev-backend` | Solo FastAPI con hot reload (`:8080`) |
| `make docker-dev-up` | Redis local en Docker |
| `make docker-dev-down` | Bajar Redis local |
| `make test` | Tests JS/TS (Turbo) + Python (pytest) |
| `make lint` | Biome (JS/TS) + Ruff (Python) |
| `make format` | Auto-fix de lint en ambos lenguajes |
| `make migrate-create m="texto"` | Nueva migración Alembic |
| `make migrate-up` | Aplicar migraciones pendientes |
| `make migrate-down` | Rollback de la última migración |
| `make reset-memory` | Borra memoria de un usuario (dev, destructivo) |
| `make export-user-data` | Exporta memoria + sesiones de un usuario |

Atajos completos en [`Makefile`](./Makefile).

## Stack

| Capa | Tecnología |
| --- | --- |
| Web | Next.js 16, TypeScript strict, Tailwind v4 (CSS-first), shadcn/ui, GSAP, Lenis, TanStack Query v5, Zustand v5, React Hook Form + Zod, Auth.js v5 |
| Mobile | Expo SDK 53+, Expo Router (file-based), NativeWind, TanStack Query, Zustand, expo-secure-store, expo-notifications |
| Backend | Python 3.12+, FastAPI, Pydantic v2, SQLAlchemy 2 async, Alembic, Celery 5.4+, uv, Ruff |
| LLM | Inferencia por API con proveedores compatibles con OpenAI o con la Messages API de Anthropic, y respaldo (ADR-028, ADR-031), clientes HTTP sobre httpx, sin SDK de proveedor |
| DB (MVP) | Supabase como Postgres 16 gestionado + pgvector, Redis (Upstash o Docker) |
| DB (V2) | Postgres 16 self-hosted + pgvector, Redis self-hosted |
| Monorepo | pnpm 10+, Turborepo 2.x, Biome 2.x |
| Deploy | Vercel (web), EAS (mobile), Cloudflare Tunnel + R2 (backend) |

## Alias de modelo

| Alias | Modos | Lee memoria | Escribe memoria | Llama tools |
| --- | --- | --- | --- | --- |
| `conversacional` | estudio, bienestar, vida | Sí | No | No |
| `agente` | productividad, memoria y la consolidación | Sí | Sí (async vía Celery) | Sí |

El código pide el alias y cada proveedor de `LLM_PROVIDERS` lo traduce a su id de
modelo: la identidad del modelo en el código es el alias. Razonamiento:
[`ADR-028`](./docs/architecture/adrs/ADR-028-inferencia-por-api.md), que mantiene la separación
de roles de [`ADR-002`](./docs/architecture/adrs/ADR-002-gemma-qwen-dual-stack.md) y reemplaza el
serving local de [`ADR-014`](./docs/architecture/adrs/ADR-014-serving-ollama-gguf-16gb.md).

## Memoria (3 capas)

| Capa | Qué guarda | Store | Quién escribe |
| --- | --- | --- | --- |
| **Semántica** | Hechos persistentes sobre el usuario | Postgres + pgvector + bge-m3 (1024-dim), engine in-house (ADR-010) | `agente` (async Celery) |
| **Episódica** | Resúmenes de sesiones pasadas con embedding | Postgres + pgvector + JSONB metadata | `agente` al cerrar sesión |
| **Procedural** | Preferencias y patrones | Postgres + JSONB | `agente` + heurísticas |

Estas 3 capas + el `audit_log` son **tablas sagradas**: cualquier migración
requiere tests + 1 aprobación humana explícita (regla #3 de
[`AGENTS.md`](./AGENTS.md)). Aparte existe `conversation_turns`, una tabla
**operativa** (no sagrada): buffer append-only de los turnos crudos de una
sesión, cifrado AES-256-GCM per-user, que el worker episódico lee al cerrar la
sesión y luego purga. Si la sesión no se consolida, sus turnos se borran 8 días
después del `ended_at` de la sesión. Derechos del usuario (export, borrado,
pausa, audit log) en
[`docs/product/MEMORY.md`](./docs/product/MEMORY.md).

## Flujo de un mensaje

```
Cliente (web/mobile)
  -> POST /v1/chat {text, mode}
  -> Auth JWT + rate limit
  -> Router LLM (decide el alias según el modo)
  -> memory.search (capas activas del modo)
  -> Proveedor de IA por API (alias conversacional o agente)
       agente: tool calls en loop -> tools (calendar/reminder/memory)
  -> Respuesta al cliente
  -> Celery worker: consolida memoria (async, fuera del path)
```

Diagrama completo:
[`docs/architecture/diagrams/flow-mensaje.md`](./docs/architecture/diagrams/flow-mensaje.md).

## Estructura del repo

```
ynara/
├── AGENTS.md                # Contrato del repo — leer primero (humanos e IAs)
├── CLAUDE.md                # Adapter Claude Code         -> AGENTS.md
├── DESIGN.md                # Sistema visual (vacío hasta aprobación)
├── IDENTITY.md              # 4 pilares de marca + voz
├── README.md                # Este archivo
├── ynara.config.json        # 5 modos + 2 alias de modelo + memoria + infra
│
├── apps/
│   ├── web/                 # Next.js 16 + Tailwind v4 + shadcn/ui
│   ├── mobile/              # Expo SDK 53+ + NativeWind
│   └── backend/             # FastAPI + LLM + memoria
│       ├── app/
│       │   ├── api/v1/      # rutas HTTP
│       │   ├── core/        # config, security, deps
│       │   ├── models/      # SQLAlchemy
│       │   ├── schemas/     # Pydantic v2
│       │   ├── services/    # lógica de negocio (sin framework)
│       │   ├── llm/         # router + prompts + tools
│       │   ├── memory/      # 3 capas (semántica, episódica, procedural)
│       │   ├── workers/     # Celery
│       │   └── workflows/   # consolidación async
│       ├── alembic/         # migraciones (políticas + plantilla async)
│       └── docs/            # MODELS, ENDPOINTS, TOOLS, MIGRATIONS
│
├── packages/
│   ├── shared-types/        # types TS compartidos web/mobile
│   ├── shared-schemas/      # Zod schemas (mirror de Pydantic)
│   ├── ui/                  # componentes UI compartidos
│   ├── core/                # lógica no-visual compartida web/mobile (stores, hooks, API) — ADR-012
│   └── config/              # tsconfig.base + biome + eslint
│
├── docs/
│   ├── architecture/        # ADRs (índice en su README) + 3 diagramas Mermaid
│   ├── product/             # VISION, MODES, MEMORY, TONE-OF-VOICE
│   ├── operations/          # INSTALL, LOCAL-DEV, DEPLOY, RUNBOOK, MIGRATION
│   └── conventions/         # COMMITS, GLOSSARY, AI-GUIDELINES, CODE-STYLE
│
├── infra/
│   ├── docker/              # docker-compose dev/prod + Dockerfile.backend
│   └── prod/                # Redis con auth, proveedores de IA, Cloudflare Tunnel, backups (TODO)
│
├── skills/                  # 6 SKILL.md reutilizables
│   ├── add-new-mode/        # agregar un modo al producto
│   ├── add-llm-tool/        # agregar una tool al alias agente
│   ├── add-memory-type/     # agregar una capa de memoria (proceso pesado)
│   ├── adr-create/          # crear un ADR
│   ├── create-ui-component/ # crear un componente UI
│   └── pr-review/           # workflow de review de PR (/pr-review)
│
├── scripts/                 # init, seed, reset-memory, export-user-data
├── tests/e2e/               # Playwright (TODO configurar)
├── .github/                 # workflows, CODEOWNERS, PR/issue templates
├── .claude/                 # adapter de Claude Code (sin contenido propio)
└── .omc/                    # local del dev (gitignored)
```

**Golden rule**: `AGENTS.md` y `docs/` son fuente de verdad.
`.claude/` es adapter — nunca contenido propio.

## Para agentes IA

Si sos un agente IA (Claude Code, OpenAI Codex, Gemini Code Assist, Cursor,
Copilot, etc.) trabajando en este repo:

1. **Leer [`AGENTS.md`](./AGENTS.md)** — contrato del repo, 10 reglas no
   negociables.
2. **Si usás Claude Code, leer su adapter** — [`CLAUDE.md`](./CLAUDE.md).
   Cualquier otra herramienta trabaja directo con `AGENTS.md`.
3. **Leer el `AGENTS.md` del app que vayas a tocar** —
   [`apps/web/AGENTS.md`](./apps/web/AGENTS.md),
   [`apps/mobile/AGENTS.md`](./apps/mobile/AGENTS.md),
   [`apps/backend/AGENTS.md`](./apps/backend/AGENTS.md).
4. **Si es cambio arquitectónico**: leer los ADRs relevantes en
   [`docs/architecture/adrs/`](./docs/architecture/adrs/).

### Reglas duras (resumen, no negociables)

- OK humano antes de `pnpm install`, `uv sync`, `git commit`, `git push`,
  cambios en `.md` raíz, cambios en `ynara.config.json`, migraciones
  Alembic.
- Datos de usuario y proveedores de IA: nunca contenido ni PII en logs; la
  IA se consume solo desde `apps/backend/app/llm/clients/providers/`, con
  proveedores aprobados y solo para la inferencia (regla #4).
- Cliente JavaScript de Supabase prohibido desde el frontend. Todo
  acceso a datos pasa por la API de FastAPI.
- Tablas `semantic_memory`, `episodic_memory`, `procedural_memory` son
  sagradas: tests + 1 aprobación humana explícita para cualquier migración.
- Conventional Commits en español imperativo:
  `feat(web): agregar modo bienestar`,
  `fix(backend): corregir extracción episódica`.

Reglas completas: [`AGENTS.md`](./AGENTS.md). Reglas extendidas (15 más):
[`docs/conventions/AI-GUIDELINES.md`](./docs/conventions/AI-GUIDELINES.md).

## Fase MVP → V2

Hoy estamos en **fase MVP**. La DB es Supabase como Postgres gestionado
para levantar rápido. La migración a Postgres self-hosted está planeada
para V2 post-validación de producto.

| Fase | DB | Cache | Inferencia | Por qué |
| --- | --- | --- | --- | --- |
| **MVP** (hoy) | Supabase (Postgres 16 + pgvector) | Upstash o Docker | Por API, con proveedores aprobados (ADR-028) | Velocidad sin DevOps inicial |
| **V2** (post-PMF) | Postgres 16 self-hosted | Redis self-hosted | Por API (ADR-028) | Control directo de la base (ADR-005) |

**Reglas que hacen la migración trivial**:

- Cliente JS de Supabase prohibido en frontend.
- Supabase Auth, Storage, Realtime, Edge Functions, RLS: todos prohibidos.
- Toda autorización vive en FastAPI.
- La única referencia a Supabase es `DATABASE_URL` en variables del backend.

Razonamiento: [`ADR-005`](./docs/architecture/adrs/ADR-005-supabase-mvp-postgres-selfhosted-v2.md).
Plan de cutover: [`docs/operations/MIGRATION-SUPABASE-TO-SELFHOSTED.md`](./docs/operations/MIGRATION-SUPABASE-TO-SELFHOSTED.md).

## Posicionamiento

Producto comercial. Las ventajas defensivas:

- **Modelos intercambiables** — la inferencia va por API detrás de dos alias, con
  una lista de proveedores y respaldo: cambiar de proveedor o de modelo es
  configuración (ADR-028).
- **Memoria propia** — engine in-house sobre la base de Ynara (ADR-010), no un
  servicio de memoria de terceros.
- **Tono y lengua** — rioplatense específico, no traducido a escala global.
- **Datos con reglas propias** — los datos del usuario no van a APIs externas,
  analytics ni servicios de IA de terceros, salvo a los proveedores de IA con
  contrato de tratamiento de datos y que no entrenen con ellos: solo para la
  inferencia y solo lo que cada llamada necesita (regla #4).

Visión completa: [`docs/product/VISION.md`](./docs/product/VISION.md).
Identidad de marca: [`IDENTITY.md`](./IDENTITY.md).

## Read Next

| Querés... | Leer |
| --- | --- |
| Mapa completo de docs | [`docs/README.md`](./docs/README.md) |
| Entender el contrato de IA | [`AGENTS.md`](./AGENTS.md) |
| Empezar a desarrollar | [`docs/operations/INSTALL.md`](./docs/operations/INSTALL.md) → [`docs/operations/LOCAL-DEV.md`](./docs/operations/LOCAL-DEV.md) |
| Trabajar en frontend web | [`apps/web/README.md`](./apps/web/README.md) + [`apps/web/AGENTS.md`](./apps/web/AGENTS.md) |
| Trabajar en mobile | [`apps/mobile/README.md`](./apps/mobile/README.md) + [`apps/mobile/EAS.md`](./apps/mobile/EAS.md) |
| Trabajar en backend | [`apps/backend/README.md`](./apps/backend/README.md) + [`apps/backend/docs/`](./apps/backend/docs/) |
| Agregar una tool al agente | [`skills/add-llm-tool/SKILL.md`](./skills/add-llm-tool/SKILL.md) |
| Agregar un modo nuevo | [`skills/add-new-mode/SKILL.md`](./skills/add-new-mode/SKILL.md) |
| Crear un ADR | [`skills/adr-create/SKILL.md`](./skills/adr-create/SKILL.md) |
| Crear migración Alembic | [`apps/backend/docs/MIGRATIONS.md`](./apps/backend/docs/MIGRATIONS.md) |
| Deploy | [`docs/operations/DEPLOY.md`](./docs/operations/DEPLOY.md) + [`docs/operations/RUNBOOK.md`](./docs/operations/RUNBOOK.md) |
| Entender la voz del producto | [`IDENTITY.md`](./IDENTITY.md) + [`docs/product/TONE-OF-VOICE.md`](./docs/product/TONE-OF-VOICE.md) |
| Glosario interno | [`docs/conventions/GLOSSARY.md`](./docs/conventions/GLOSSARY.md) |
| Política de seguridad | [`SECURITY.md`](./SECURITY.md) |
| Cómo contribuir | [`CONTRIBUTING.md`](./CONTRIBUTING.md) |

## Equipo

3 CODEOWNERS del repo:
[@MateoGs013](https://github.com/MateoGs013),
[@bygama](https://github.com/bygama),
[@querques20](https://github.com/querques20).
Responsabilidades por path en [`.github/CODEOWNERS`](./.github/CODEOWNERS).

## Licencia

Propietaria. Ver [`LICENSE`](./LICENSE).
