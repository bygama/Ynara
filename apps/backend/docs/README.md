# apps/backend/docs/

Catálogos vivos del backend. Mantener actualizados es parte del PR
correspondiente.

## Archivos

- [`MODELS.md`](./MODELS.md) — modelos SQLAlchemy del proyecto.
- [`ENDPOINTS.md`](./ENDPOINTS.md) — endpoints HTTP.
- [`TOOLS.md`](./TOOLS.md) — tools que puede llamar el alias `agente`.
- [`MIGRATIONS.md`](./MIGRATIONS.md) — política de migraciones
  Alembic.

## Arquitectura y planes

Estos catálogos son el **qué**; el **cómo** vive en otro lado:

- Mapa del código, capa LLM (`app/llm/`) y gates: [`../AGENTS.md`](../AGENTS.md).
- Decisiones: [ADRs](../../../docs/architecture/adrs/) — ADR-001..ADR-028, con el estado de cada uno en el índice de [`docs/architecture/README.md`](../../../docs/architecture/README.md). Los más relevantes para el backend:
  - ADR-005 (Supabase MVP), ADR-007 (memory decay/retention/encryption), ADR-008 (embedding bge-m3, ratificado por ADR-028), ADR-010 (memory architecture v2 in-house, supersede ADR-003/Mem0), ADR-011 (auth layering).
  - **Inferencia**: ADR-028 (inferencia por API: proveedores, alias de modelo y respaldo). Reemplaza ADR-002 (sus roles siguen como los alias `conversacional` y `agente`), ADR-009, ADR-012, ADR-014 y el esquema de ADR-013.
  - ADR-015 (auth deps: PyJWT + bcrypt directo).
- Plan histórico de la capa de inferencia, archivado con ADR-028: [`LLM-INFERENCE-INTEGRATION.md`](../../../docs/planning/archive/LLM-INFERENCE-INTEGRATION.md).

## Regla

Si agregás un modelo, endpoint, tool o migración, **actualizás el
catálogo correspondiente** en el mismo PR. CI no lo verifica todavía
(TODO), pero la review humana sí.
