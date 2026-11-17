# infra/prod/

Configs y notas específicas de producción que no entran en el
docker-compose principal:

- Cloudflare Tunnel ingress config.
- Cron de backups encriptados a R2.
- Reglas de firewall.
- Tuning Postgres para la fase V2 (cuando se migre a self-hosted).

La VPS de prod no tiene GPU: la IA (chat, agente, consolidación de memoria
y embeddings) se consume por API de proveedores externos, según
[ADR-028](../../docs/architecture/adrs/ADR-028-inferencia-por-api.md). No
hay modelos que servir.

## Broker Redis con auth (SEC-001)

El `redis` del `docker-compose.yml` corre con `--requirepass` (defensa en
profundidad: no se expone a internet, pero exige credencial igual). Para que el
stack levante hay que setear **dos** lugares de forma consistente:

1. **`REDIS_PASSWORD`** en el entorno de compose (la VPS o un `.env` junto al
   compose). Si falta, el servicio `redis` no arranca (`:?` lo exige).
2. Los **broker URLs** en `apps/backend/.env`, incluyendo la pass:

   ```sh
   REDIS_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
   CELERY_BROKER_URL=redis://:${REDIS_PASSWORD}@redis:6379/0
   CELERY_RESULT_BACKEND=redis://:${REDIS_PASSWORD}@redis:6379/0
   ```

   (host `redis` = nombre del servicio en la red interna `ynara`). Si los URLs no
   incluyen la pass, api/worker/beat fallan con `NOAUTH Authentication required`.

Para TLS extremo-a-extremo (solo si el broker cruza hosts, no en la VPS
single-host actual) usar `rediss://` + `--tls-port` en redis; hoy el broker vive
en la red bridge interna, así que `requirepass` es la cota suficiente.

## Proveedores de IA

Los proveedores se configuran en `apps/backend/.env`, el `env_file` que
comparten `api`, `worker` y `beat`. Las credenciales nunca van inline en
el compose (regla #2):

- `LLM_BACKEND=api` y `LLM_PROVIDERS`: lista JSON en orden de prioridad
  (el primero es el principal y el resto, respaldo), con la credencial de
  cada proveedor en `LLM_API_KEY_<NAME>`.
- `EMBEDDING_BACKEND=api`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY` y
  `EMBEDDING_MODEL`.

Las URL base van por https. Con `ENVIRONMENT=production` el backend no
arranca si el LLM o los embeddings quedan en `fake`, si `LLM_PROVIDERS`
está vacía o si falta alguna de esas variables o credenciales (ADR-028
D7). El detalle de cada variable está en
[`apps/backend/.env.example`](../../apps/backend/.env.example).

La salida a internet de `api` y `worker` va a esos proveedores (regla
#4b), a la DB de Supabase y a Sentry si `SENTRY_DSN` está seteado. Es
política del backend: la red del compose no filtra la salida.

## Concurrencia del worker de Celery

`CELERY_CONCURRENCY` fija los procesos del worker, con 2 por default. Igual
que `REDIS_PASSWORD`, va en el entorno de compose (la VPS o un `.env` junto
al compose) y no en `apps/backend/.env`, porque compose la interpola en el
`command` del worker.

El techo lo pone la cuota del proveedor de IA: cada proceso consolida en
paralelo y le pega al proveedor, y por encima de la cuota llegan 429 que
las tasks de consolidación reintentan con backoff. Los 2 workers de
gunicorn de `api` son otra cota, fija en `Dockerfile.backend` (ADR-020).

## TODO inicial (pendiente)

- [ ] `cloudflared.yml` con ingress hacia el contenedor api.
- [ ] `backup-db.sh` + crontab para pg_dump diario cifrado.
- [ ] `postgres-tuning.conf` para Postgres self-hosted (V2).
