# apps/backend/docs/

Catálogos vivos del backend. Mantener actualizados es parte del PR
correspondiente.

## Archivos

- [`MODELS.md`](./MODELS.md) — modelos SQLAlchemy del proyecto.
- [`ENDPOINTS.md`](./ENDPOINTS.md) — endpoints HTTP.
- [`TOOLS.md`](./TOOLS.md) — tools que Qwen puede llamar.
- [`MIGRATIONS.md`](./MIGRATIONS.md) — política de migraciones
  Alembic.

## Arquitectura y planes

Estos catálogos son el **qué**; el **cómo** vive en otro lado:

- Mapa del código, capa LLM (`app/llm/`) y gates: [`../AGENTS.md`](../AGENTS.md).
- Decisiones: [ADRs](../../../docs/architecture/adrs/) — ADR-001..ADR-015 (15 ADRs lógicos en 16 archivos: hay dos ADR-012, uno de modelo conversacional 12B y otro de código compartido en mobile). Los más relevantes para el backend:
  - ADR-002 (dual stack Gemma/Qwen), ADR-005 (Supabase MVP), ADR-007 (memory decay/retention/encryption), ADR-008 (embedding bge-m3), ADR-010 (memory architecture v2 in-house, supersede ADR-003/Mem0), ADR-011 (auth layering).
  - **Inferencia**: ADR-028 (inferencia por API: proveedores, alias de modelo y respaldo), que reemplaza el serving local de ADR-009, ADR-013 y ADR-014.
  - ADR-015 (auth deps: PyJWT + bcrypt directo).
- Plan de la capa de inferencia: [`LLM-INFERENCE-INTEGRATION.md`](../../../docs/planning/LLM-INFERENCE-INTEGRATION.md).

## Regla

Si agregás un modelo, endpoint, tool o migración, **actualizás el
catálogo correspondiente** en el mismo PR. CI no lo verifica todavía
(TODO), pero la review humana sí.
