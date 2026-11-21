# DEPLOY.md — Deploy de Ynara

## Topología

Ver `docs/architecture/diagrams/deploy-topology.md`.

- **Web** → Vercel (Next.js 16).
- **Mobile** → Expo EAS Build (iOS + Android).
- **Backend** → VPS LATAM sin GPU, detrás de Cloudflare Tunnel.
- **DB** → Supabase (MVP) → Postgres self-hosted (V2).
- **Cache / queue / auth store** → Redis (Upstash o Docker en VPS).
  **Requerido**: además de caché y broker/result de Celery, desde #63 es
  el store de AUTH (blocklist de tokens para logout/refresh + rate-limit
  de login). La auth degrada a fail-open si Redis cae, pero el servicio
  debe estar provisto.
- **Storage** → Cloudflare R2.
- **IA** → proveedores externos por API compatible con OpenAI
  ([ADR-028](../architecture/adrs/ADR-028-inferencia-por-api.md)). El chat,
  el agente y la consolidación de memoria van contra `LLM_PROVIDERS`
  (principal y respaldo) y los embeddings contra `EMBEDDING_BASE_URL`. La
  VPS no sirve modelos: ver [Proveedores de IA](#proveedores-de-ia).

## Pipelines CI/CD

GitHub Actions:
- `.github/workflows/ci.yml` — **Backend CI** scopeada a `apps/backend/**`:
  `ruff check` + `ruff format --check` + `pip-audit` + `pytest` (unit +
  integration con gate de cobertura). No corre typecheck.
- `.github/workflows/ci-web.yml` — **Web CI** scopeada a `apps/web/**` +
  packages compartidos: biome + tsc (typecheck) + vitest + `next build`.
  `apps/mobile` queda **fuera de CI** por ahora (su typecheck falla: los tipos
  de NativeWind no exponen `className`).
- `.github/workflows/deploy-web.yml` — **placeholder** `workflow_dispatch`
  (echo TODO); el deploy real de web lo maneja **Vercel vía Git
  integration** (este slot queda para tareas auxiliares post-deploy).
- `.github/workflows/deploy-mobile.yml` — trigger EAS Build manual
  (workflow_dispatch).
- `.github/workflows/deploy-backend.yml` — build Docker + push +
  deploy a VPS (manual o tag `backend-v*`).

## Web (Vercel)

- Conectar el repo a Vercel apuntando a `apps/web`.
- Variables de entorno desde el dashboard de Vercel.
- Preview por PR automático.
- Production en push a `main`.
- El deploy lo dispara **Vercel vía Git integration**, no GitHub Actions.
  `deploy-web.yml` es un slot manual (`workflow_dispatch`) para tareas
  auxiliares post-deploy (smoke tests, purge de cache, notificaciones).

## Mobile (EAS)

- Build manual con `eas build --profile production --platform all`.
- Submit a stores con `eas submit`.
- Secrets desde el dashboard de EAS (no en el repo).
- Detalle en `apps/mobile/EAS.md`.

## Backend (VPS)

### Build
```sh
docker build -f infra/docker/Dockerfile.backend -t ynara-backend:latest .
```

### Deploy
1. Push de imagen a registry privado.
2. SSH a la VPS.
3. `docker compose -f infra/docker/docker-compose.yml pull`.
4. `docker compose -f infra/docker/docker-compose.yml up -d`.
5. Verificar health: `curl https://api.ynara.app/v1/health`.
6. Verificar los proveedores de IA: página System Health del panel admin
   (`GET /v1/admin/system`, bloque `ai`), con cada proveedor y los
   embeddings en `ok`. `/v1/health/ready` mira solo la DB y Redis.
7. Verificar logs: `docker compose logs -f api`.

Si `api`, `worker` o `beat` no arrancan por la config de IA, ver
"App no arranca por la config de IA" en [`RUNBOOK.md`](./RUNBOOK.md).

### Rollback
1. `docker compose pull <tag-anterior>`.
2. `docker compose up -d`.
3. Verificar.

## Proveedores de IA

La IA se consume por API (ADR-028): en la VPS no hay GPU, pesos ni
servicios de modelos que levantar. La configuración vive en
`apps/backend/.env`, el `env_file` que comparten `api`, `worker` y `beat`.
Variables, fail-fast y salida a internet en
[`infra/prod/README.md`](../../infra/prod/README.md#proveedores-de-ia). En
corto:

- `LLM_BACKEND=api` y `LLM_PROVIDERS`: lista JSON en orden de prioridad (el
  primero es el principal y el resto, respaldo), con la credencial de cada
  proveedor en `LLM_API_KEY_<NAME>`.
- `EMBEDDING_BACKEND=api`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` y
  `EMBEDDING_MODEL` (default `BAAI/bge-m3`, 1024 dimensiones). Los
  embeddings tienen un solo proveedor, sin respaldo.
- Con `ENVIRONMENT=production`, el backend no arranca si el LLM o los
  embeddings quedan en `fake`, si `LLM_PROVIDERS` está vacía o si falta una
  URL, un modelo o una credencial (ADR-028 D7). El error nombra la
  variable, nunca su valor.

Las credenciales son secretos (regla #2): viven solo en el entorno de la
VPS, nunca en el repo, en `ynara.config.json` ni en el `environment:` del
compose. Un cambio en `apps/backend/.env` se aplica recreando los
contenedores: `docker compose -f infra/docker/docker-compose.yml up -d --force-recreate api worker beat`. Un
`docker compose restart` sigue con el entorno viejo.

**Datos reales.** Hasta cerrar las cinco compuertas de ADR-028 (legal,
crisis, costo, respaldo probado y latencia), los proveedores reciben solo
datos sintéticos (regla #4b).

El techo de concurrencia lo pone la cuota de los proveedores: los procesos
del worker se ajustan con `CELERY_CONCURRENCY`
([`infra/prod/README.md`](../../infra/prod/README.md#concurrencia-del-worker-de-celery)).
Rotar credenciales, pasar al respaldo a mano o atender una caída del
proveedor: [`RUNBOOK.md`](./RUNBOOK.md).

## Migración a V2

Cuando llegue el momento de mover de Supabase a Postgres self-hosted,
seguir `MIGRATION-SUPABASE-TO-SELFHOSTED.md`.

## Open questions

<!-- TODO -->
- Estrategia de blue/green vs rolling para backend.
- Window de mantenimiento estándar.
- Monitoring stack (Grafana + Loki vs Sentry-only).
