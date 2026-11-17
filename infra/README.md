# infra/

Infraestructura: Docker y prod. El backend corre en una VPS sin GPU y
consume la IA por API de proveedores externos
([ADR-028](../docs/architecture/adrs/ADR-028-inferencia-por-api.md)):
acá no hay serving de modelos.

## Subcarpetas

- `docker/` — docker-compose para dev y prod, Dockerfile del backend.
- `prod/` — notas y configs específicas de producción (broker Redis con
  auth, proveedores de IA, Cloudflare Tunnel, backups, etc.).

## Notas

- En fase MVP, Postgres vive en Supabase, así que el
  `docker-compose.dev.yml` **no** levanta Postgres. Solo Redis.
- En V2, agregar Postgres + pgvector al compose de prod.
