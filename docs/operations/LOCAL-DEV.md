# LOCAL-DEV.md — Flujo de desarrollo diario

> Asume que ya hiciste el INSTALL.md una vez. Para el backend se requiere `uv >= 0.11.0`
> (fijado en `apps/backend/pyproject.toml` para compatibilidad con `uv.lock`).

## Levantar el stack

Opción rápida con Make:

```sh
make docker-dev-up   # Redis local
make dev-backend     # FastAPI :8080
make dev-web         # Next.js :3000
```

En 3 terminales separadas, o usando un multiplexer (tmux,
WezTerm, ConEmu).

## Flujo típico

1. Crear branch desde `main`:
   `git checkout -b feat/web-modo-bienestar`.
2. Editar código.
3. Lint + format:
   - JS/TS: `pnpm biome check --write`.
   - Python: `cd apps/backend && uv run ruff check . --fix && uv run ruff format .`.
4. Tests:
   - `pnpm turbo test`.
   - Backend: `cd apps/backend && uv run pytest`.
5. Commit (Conventional Commits en español):
   `git commit -m "feat(web): agregar layout base del modo bienestar"`.
6. PR contra `main` con el template.

## Trabajar con la DB

- Migraciones: ver `apps/backend/docs/MIGRATIONS.md`.
- Reset de memoria local (DESTRUCTIVO):
  `make reset-memory`.

## IA en desarrollo: fake por default

El backend arranca con `LLM_BACKEND=fake` y `EMBEDDING_BACKEND=fake`, los
valores que trae `apps/backend/.env.example`: sin credenciales, GPU,
servidor de modelos ni red. Así corren los tests y anda el resto del
backend (auth, sesiones, memoria, agenda), pero el chat no contesta:

- `FakeEmbeddingClient` da vectores deterministas (mismo texto, mismo
  vector), sin similitud semántica real.
- `FakeLlmClient` responde solo lo que un test le programa. En el servidor
  de dev no tiene nada programado, así que un turno de `/v1/chat` o
  `/v1/chat/stream` devuelve una respuesta degradada («IA pausada», sin
  guardar el turno) y el playground del panel admin responde 409. Para
  chatear hace falta un proveedor: ver
  [Probar contra un proveedor de IA](#probar-contra-un-proveedor-de-ia).

El reranker es siempre un passthrough (ADR-028 D5).

## Probar contra un proveedor de IA

Es opcional. La IA se consume por API de proveedores compatibles con
OpenAI o con la Messages API de Anthropic
([ADR-028](../architecture/adrs/ADR-028-inferencia-por-api.md),
[ADR-031](../architecture/adrs/ADR-031-cliente-nativo-messages-api.md)):
no se levanta ningún modelo en tu máquina. **Hasta cerrar las compuertas
de ADR-028, al proveedor van solo datos sintéticos** (regla #4b): nada de
datos personales reales, tampoco los tuyos.

En `apps/backend/.env`, editando las líneas que ya trae `.env.example` (no
agregues una segunda línea de la misma variable):

```sh
# Chat, agente y consolidación.
LLM_BACKEND=api
# Una sola línea, en orden de prioridad: el primero es el principal y el resto,
# respaldo. Para probar alcanza con uno, y su models cubre los dos alias.
LLM_PROVIDERS=[{"name":"principal","base_url":"https://api.example.com/v1","models":{"conversacional":"proveedor/modelo-chat","agente":"proveedor/modelo-agente"},"supports_reasoning_effort":false}]
# La credencial de cada proveedor: LLM_API_KEY_ más su name en mayúsculas.
LLM_API_KEY_PRINCIPAL=

# Embeddings (opcional: si quedan en fake, la memoria usa vectores del fake).
EMBEDDING_BACKEND=api
EMBEDDING_BASE_URL=https://api.example.com/v1
EMBEDDING_API_KEY=
EMBEDDING_MODEL=BAAI/bge-m3
```

- Los valores son de ejemplo: la URL base va por https e incluye el `/v1`, y
  los ids de modelo son los del proveedor. El código solo conoce los alias
  `conversacional` y `agente`.
- `.env.example` trae dos proveedores de ejemplo, `principal` y `respaldo`:
  si dejás los dos, cada uno necesita su credencial. Con uno solo, la
  `LLM_API_KEY_RESPALDO` que sobra no molesta.
- Con `LLM_BACKEND=api` el backend no arranca si `LLM_PROVIDERS` está vacía
  o si falta la credencial de un proveedor; con `EMBEDDING_BACKEND=api`, si
  falta la URL, la credencial o el modelo. El error nombra la variable.
- Las credenciales son secretas (regla #2): solo en tu `.env`, nunca en el
  repo, en `ynara.config.json` ni en un log.
- Pasar los embeddings de `fake` a `api` sobre una DB que ya tiene memoria
  guardada mezcla vectores del fake con los del proveedor, y la búsqueda
  por similitud no encuentra los viejos. Probá con una DB de dev limpia
  (re-embeddear es una migración de datos, ADR-028 D4).
- Un `.env` de antes de ADR-028 con `LLM_BACKEND=vllm` o
  `EMBEDDING_BACKEND=vllm` no arranca: esos valores ya no son válidos.
  `LLM_SERVING` y las `RERANKER_*` que hayan quedado se ignoran.
- Para probar un modelo sin pasar por un modo, el playground del panel
  admin (`/playground`) llama al proveedor sin sesión ni memoria; con
  `LLM_BACKEND=fake` responde 409.

## Hot reload

- Web: Next.js auto-reload.
- Mobile: Expo auto-reload.
- Backend: `--reload` en uvicorn.
- Workers Celery: requieren restart manual al cambiar código de
  tasks. `make dev-backend` por ahora no levanta el worker; ver
  `apps/backend/AGENTS.md` para correrlo aparte.

## Trucos

- `pnpm turbo run dev --filter web --filter backend` levanta solo
  esos dos.
- `pnpm biome check --write` antes de commitear suele evitar
  reverts.
- Para debuggear queries SQL de SQLAlchemy: setear
  `SQLALCHEMY_ECHO=1` en `.env`.

## Convención de archivos chicos

- TS strict: target menos de 300 líneas por archivo, refactor si
  pasa de 500.
- Python: idem.
- Si necesitás romper la regla, justificalo en PR.
