# RUNBOOK.md — Incidentes y respuesta

> "Algo se rompió en producción, ¿qué hago?". Esta guía.

## Severidades

- **S0**: producción caída para todos los usuarios.
- **S1**: feature crítica rota (login, chat, memoria) para un subset.
- **S2**: degradación parcial, sin pérdida de datos.
- **S3**: feature secundaria rota, no urgente.

## Contactos

<!-- TODO: completar -->
- On-call principal: @MateoGs013
- On-call secundario: @bygama
- DBA (memoria): @querques20

## Comandos rápidos

| Qué | Cómo |
|-----|------|
| Health del backend | `curl https://api.ynara.app/v1/health` |
| Logs backend (VPS) | `docker compose logs -f api` |
| Logs Celery | `docker compose logs -f worker` |
| Estado de los proveedores de IA | Panel admin → System Health, o `GET /v1/admin/system` con JWT de admin: bloque `ai`, un `status` (`ok` / `down` / `unconfigured`) por proveedor y otro para embeddings |
| Logs de la IA | Ver el comando de abajo |
| Estado de la DB | `psql $DATABASE_URL -c "SELECT now();"` |
| Backup ad-hoc | `pg_dump $DATABASE_URL | gzip > backup-$(date +%F).sql.gz` |

Logs de la IA de la última media hora, en `api` y `worker` (sin contenido
de usuario: solo nombres de proveedor, clases de error e ids, regla #4a):

```sh
docker compose logs --since 30m api worker | grep -E 'llm:|route degrado|memory search:|consolidate_|memory_engine:'
```

## Escenarios comunes

### Backend no responde

1. `curl https://api.ynara.app/v1/health` → si no llega, problema de
   Cloudflare Tunnel o del proceso.
2. SSH a VPS: `docker compose ps`.
3. Si `api` está down: `docker compose logs api | tail -200`.
4. Restart: `docker compose restart api`.
5. Si persiste, rollback al tag anterior (ver `DEPLOY.md`).

### Proveedor de IA lento o caído

> **Aplica con `LLM_BACKEND=api`.** En dev y CI el backend usa
> `FakeLlmClient` (`LLM_BACKEND=fake`) y este escenario no aplica.

**Cómo responde el backend.** La cadena es principal → respaldo →
respuesta degradada, en el orden de `LLM_PROVIDERS` (ADR-028 D2). Cada
request a un proveedor espera hasta 20 s, con 3 intentos y 90 s de
presupuesto para toda la cadena. Cada proveedor tiene su circuit breaker,
por proceso: se abre después de 5 fallas seguidas por caída (timeout, 5xx
o error de conexión) y a los 30 s deja pasar una prueba. El chat nunca ve
el error: si se agota la cadena, responde el texto degradado
(`finish_reason="degraded"`).

**Síntomas.** Respuestas degradadas en el chat, y en los logs de `api`:
`llm: candidato <name> descartado (<Clase>)`,
`llm: presupuesto agotado, respuesta degradada` o
`llm: sin candidatos disponibles, respuesta degradada`.

1. Ver el estado de cada proveedor en System Health. `down` es que el
   sondeo de salud (`GET /models` con la credencial, 5 s) no dio 200: el
   proveedor está caído o lento, o rechazó la credencial. Con un proveedor
   caído, la página tarda hasta ese timeout.
2. La clase en `descartado (<Clase>)` dice qué pasa:
   - `LlmTimeoutError`: el proveedor está lento.
   - `LlmUnavailableError`: 5xx o sin conexión.
   - `LlmRateLimitError`: cuota agotada (ver "Cuota del proveedor agotada
     (429)").
   - `LlmAuthError`: credencial rechazada (ver "Rotar la credencial de un
     proveedor de IA").
3. Revisar la página de estado del proveedor.
4. Si cae el principal y el respaldo responde, no hace falta tocar nada: el
   tráfico pasa solo al respaldo y vuelve cuando el breaker del principal
   lo deja probar. Si el principal está lento sin llegar a caer, cada turno
   paga su espera antes del respaldo: pasarlo a mano (ver "Pasar al respaldo
   a mano").
5. Si caen todos, el chat sigue respondiendo degradado. Avisar en la app y
   esperar, o sumar otro proveedor a la lista (con las compuertas de ADR-028
   cumplidas para ese proveedor).

**Consolidación de memoria.** El worker no degrada: ante una caída o un
429, las tasks `consolidate_turn` y `consolidate_session` se reintentan
hasta 5 veces, con backoff exponencial y jitter (base de 30 s, tope de
10 min). En los logs de `worker`, `<task>: falla transitoria ... se
reintenta`. Si se agotan, queda `<task>: reintentos agotados user=<id>
session=<id>` y la task termina sin error:

- `consolidate_turn` no se puede reencolar, porque lleva el texto del
  turno y ese texto no queda en los logs (regla #4a). Ese turno no suma
  hechos ni preferencias, pero el resumen episódico de la sesión lo
  cubre.
- `consolidate_session` sí: lleva solo ids, es idempotente (un episodio por
  sesión) y los turnos crudos no se purgan hasta que el episodio se crea.
  Las sesiones cerradas sin episodio se cuentan en el `backlog` de
  consolidación (panel admin → Salud del Moat). Cuando el proveedor
  vuelva, reencolar cada una con el `mode` de su fila en `sessions` (de él
  dependen la marca de sensible y la retención del episodio):

  ```sh
  docker compose exec worker uv run celery -A app.workers.celery_app \
    call workflows.consolidate_session \
    -k '{"user_id": "<uuid>", "session_id": "<uuid>", "mode": "<modo>"}'
  ```

### Cuota del proveedor agotada (429)

**Síntomas.** `llm: candidato <name> descartado (LlmRateLimitError)` en
los logs de `api`, y respuestas degradadas si el respaldo tampoco tiene
cuota. En el worker, `falla transitoria ... LlmRateLimitError, se
reintenta`.

**Cómo responde el backend.** Un 429 no abre el breaker. Con un respaldo
disponible, el backend espera y reintenta el mismo proveedor solo si
`Retry-After` pide hasta 5 s y la espera entra en el presupuesto; si no,
pasa al respaldo sin esperar. Sin respaldo, espera lo que pida
`Retry-After` (o un backoff, si no lo manda) mientras entre en el
presupuesto, y si no entra, la respuesta sale degradada. La consolidación
lo reintenta como cualquier caída (ver arriba).

1. Ver en el panel del proveedor el uso contra la cuota (requests y tokens
   por minuto, gasto del día).
2. Bajar la presión de la consolidación, que es la que más llama: menos
   procesos del worker con `CELERY_CONCURRENCY`
   ([`infra/prod/README.md`](../../infra/prod/README.md#concurrencia-del-worker-de-celery)).
3. Pedir más cuota o subir de plan. Mientras tanto, si el respaldo tiene
   margen, ponerlo primero (ver "Pasar al respaldo a mano").

### Rotar la credencial de un proveedor de IA

Para una rotación programada, la baja de alguien con acceso o una
credencial expuesta. Si se expuso, revocarla primero en el proveedor:
mientras tanto el chat pasa al respaldo, o responde degradado si no hay
otro proveedor.

1. Generar la credencial nueva en el panel del proveedor, sin revocar
   todavía la vieja.
2. Reemplazar el valor en `apps/backend/.env` de la VPS:
   `LLM_API_KEY_<NAME>` (el `name` del proveedor en mayúsculas) o
   `EMBEDDING_API_KEY`. Nunca en el repo, en un ticket ni en un chat
   (regla #2).
3. Recrear los contenedores que leen ese archivo:
   `docker compose up -d --force-recreate api worker beat`. Un `restart`
   no alcanza: sigue con el entorno viejo.
4. Verificar en System Health que el proveedor quede en `ok` y que los
   logs no muestren `descartado (LlmAuthError)`.
5. Revocar la credencial vieja en el proveedor.

Una credencial rechazada (401/403) no se reintenta: el backend pasa directo
al respaldo y lo loguea como `llm: candidato <name> descartado
(LlmAuthError)`. No abre el breaker, así que cada turno vuelve a probar
primero ese proveedor. Si todos rechazan la credencial, la consolidación
descarta el turno sin reintentar (`memory_engine: extraccion descartada
(LlmAuthError)`): conviene rotar enseguida.

### Pasar al respaldo a mano

El paso al respaldo es automático cuando el principal cae. Forzarlo sirve
cuando el principal está lento sin llegar a caer, cuando se agotó su cuota
o cuando el proveedor cambió el modelo que está detrás de un alias.

1. Reordenar `LLM_PROVIDERS` en `apps/backend/.env`: el primero de la
   lista es el principal. Para sacar un proveedor, borrar su entrada.
2. Cada proveedor que quede en la lista necesita su `LLM_API_KEY_<NAME>`:
   si falta una, el backend no arranca (ADR-028 D7).
3. `docker compose up -d --force-recreate api worker beat`.
4. Verificar en System Health el orden nuevo (el primero es el principal)
   y el estado de cada proveedor.

Volver al orden original cuando el principal se normalice. El respaldo
tiene que cumplir las mismas compuertas de ADR-028 que el principal antes
de recibir datos reales.

### Proveedor de embeddings caído

Los embeddings no tienen respaldo: hay un solo `EMBEDDING_BASE_URL`.

**Síntomas.** En System Health, `embeddings` en `down`. `/v1/chat` y
`/v1/chat/stream` responden el texto degradado (`finish_reason="degraded"`)
en los modos que buscan en la memoria semántica o episódica
(productividad, estudio, bienestar y memoria): la búsqueda embeddea el
mensaje antes de armar el contexto y, si falla, el turno degrada sin
llamar al modelo (`route degrado al recuperar la memoria: <Clase>` en los
logs de `api`). Vida, que solo lee la memoria procedural, sigue andando.
`GET /v1/memory/search` responde 503
(`memory search: embeddings no disponibles: <Clase>`), con `Retry-After`
si el 429 del proveedor lo trajo. Editar un hecho
(`PATCH /v1/memory/semantic/{ref}`) todavía responde 500, porque
re-embeddea el contenido nuevo.

1. Confirmar en System Health y en la página de estado del proveedor.
2. La consolidación reintenta sola las caídas y los 429 del embedder (ver
   "Proveedor de IA lento o caído").
3. Si es una credencial rechazada, rotar `EMBEDDING_API_KEY` (ver "Rotar
   la credencial de un proveedor de IA"). Ese error no se reintenta:
   `consolidate_turn` saltea las operaciones de memoria que necesitan
   embedding (`memory_engine: op ... revertida (savepoint), skip`) y
   `consolidate_session` no crea el episodio
   (`consolidate_session: fallo ... LlmAuthError`). Esas sesiones se
   reencolan después de rotar, como en "Proveedor de IA lento o caído".
4. Cambiar de proveedor de embeddings exige el mismo modelo
   (`BAAI/bge-m3`, 1024 dimensiones): otro modelo obliga a re-embeddear la
   memoria guardada (ADR-028 D4).

### Worker Celery atascado

1. `docker compose logs worker | tail -200`.
2. Inspeccionar cola: `redis-cli -u $REDIS_URL LLEN celery`.
3. Restart: `docker compose restart worker`.

### Redis caído o degradado

**Síntoma:** `/v1/health/ready` devuelve **503** con `checks.redis.ok=false`.

**Impacto:** la auth pasa a **fail-open** (desde #63 Redis es el store de
blocklist de tokens + rate-limit de login). Con Redis caído:

- La **revocación anticipada** (logout / refresh single-use) se desactiva:
  los tokens valen hasta su `exp` (un logout previo no surte efecto hasta
  que el token expire).
- El **rate-limit** de `/auth/token` y `/auth/register` se desactiva (sin
  throttling aplicativo).

La auth **sigue funcionando** (degrada al baseline JWT-stateless); Celery sí
se ve afectado (broker/result backend).

**Acción:**

1. Verificar el estado: `redis-cli -u $REDIS_URL PING` (espera `PONG`).
2. Reiniciar/verificar Redis: `docker compose restart redis` (VPS) o revisar
   el panel de Upstash si es gestionado.
3. Confirmar recuperación: `/v1/health/ready` vuelve a **200** con
   `checks.redis.ok=true`.

### Revocar la sesión activa de un usuario

Para invalidar la sesión de **un** usuario sin rotar el `JWT_SECRET` global:

1. `POST /v1/auth/logout` con el JWT del usuario (`Authorization: Bearer
   <access>`; opcionalmente su `refresh_token` en el body).
2. El backend revoca la FAMILIA completa de esa sesión (desde #142: todos los
   tokens del mismo `sid` — access, refresh y rotaciones futuras — dejan de
   servir aunque no hayan expirado); para tokens pre-#142 sin `sid`, revoca solo
   el `jti` del access (compat). Requiere Redis arriba; fail-open si cae.

> Requiere Redis arriba (si está caído, la auth está en fail-open y la
> revocación no surte efecto — ver "Redis caído o degradado"). Para revocar
> **todas** las sesiones de golpe, rotar `JWT_SECRET` (ver "Secret expuesto
> en commit").

### App no arranca (RuntimeError del guard anti-prod)

El backend tiene un guard (`app/core/db_guard.py`) que aborta el boot
si `DATABASE_URL` apunta a Supabase (host de producción) y no se
cumple alguna de las condiciones de seguridad.

**Síntoma:** el proceso termina en el arranque con un `RuntimeError`
que menciona el host que disparó el guard.

**Resolución según contexto:**

1. **Dev local** — no usar `DATABASE_URL` de Supabase. Usar la DB
   local de tests:
   ```sh
   DATABASE_URL=postgresql://postgres:test@localhost:5433/ynara_dev
   ```
2. **Conexión a prod intencionada** (ej.: aplicar migración manual) —
   exportar la variable de override antes de arrancar:
   ```sh
   export YNARA_ALLOW_PROD_DB=1
   ```
   Usarlo solo en terminales efímeras y nunca commitear este valor.
3. **Deploy real en producción** — asegurarse de que `ENVIRONMENT=production`
   esté seteado en el entorno del contenedor/proceso.

### App no arranca por la config de IA

Con `ENVIRONMENT=production`, `Settings` frena el arranque de `api`,
`worker` y `beat` si la IA no está configurada (ADR-028 D7). El error dice
qué falta y nunca muestra un valor:

- `LLM_BACKEND tiene que ser 'api' en production` o
  `EMBEDDING_BACKEND tiene que ser 'api' en production`.
- `LLM_PROVIDERS está vacío` (con `LLM_BACKEND=api`, en cualquier entorno).
- `falta la credencial del proveedor: LLM_API_KEY_<NAME>`.
- `falta la configuración del proveedor de embeddings: <variables>`.

Un `.env` de antes de ADR-028 tampoco arranca: `LLM_BACKEND=vllm` y
`EMBEDDING_BACKEND=vllm` ya no son valores válidos (`LLM_SERVING` y las
`RERANKER_*` se ignoran). Corregir `apps/backend/.env` con
`apps/backend/.env.example` como guía y recrear los contenedores
(`docker compose up -d --force-recreate api worker beat`).

### DB Supabase lenta o caída

1. Status page Supabase.
2. Plan de fallback: solo lectura desde un read replica si está
   configurado <!-- TODO: confirmar si lo tenemos -->.
3. Si Supabase está caído > 30 min: comunicación al usuario por la
   app (banner) + waiting room.

### Memoria de un usuario corrupta

1. Snapshot inmediato: `pg_dump --table=semantic_memory --table=episodic_memory --table=procedural_memory $DATABASE_URL > snapshot.sql`.
2. Identificar el `user_id` afectado.
3. **NO** tocar las tablas sin review humano explícito (regla #3:
   1 aprobación humana además del operador). Crear ticket de
   seguridad inmediato.

### Secret expuesto en commit

1. Rotar el secret inmediatamente. Si es la credencial de un proveedor de
   IA, ver "Rotar la credencial de un proveedor de IA".
2. Forzar logout de sesiones existentes si era JWT secret. Rotar
   `JWT_SECRET` invalida **todos** los tokens de golpe por firma
   (mecanismo primario, no depende de Redis); el blocklist per-`jti`
   (`/v1/auth/logout`, desde #63) es complementario y solo alcanza tokens
   individuales.
3. Borrar el secret del histórico de git (BFG o git filter-repo).
4. Force push solo con autorización explícita (regla #1).
5. Post-mortem.

## Post-mortem template

<!-- TODO: completar template -->

- Timeline.
- Causa raíz.
- Impacto a usuarios.
- Acciones tomadas.
- Acciones preventivas (con dueño + fecha).
