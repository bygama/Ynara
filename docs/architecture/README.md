# docs/architecture/ — Arquitectura técnica de Ynara

Acá vive el "porqué" detrás de las decisiones técnicas del proyecto.

## Subcarpetas

- `adrs/` — Architecture Decision Records, uno por decisión
  significativa. Inmutables una vez aprobados.
- `diagrams/` — Diagramas del sistema en Mermaid + assets exportados.
- `informe-tecnico.pdf` — informe técnico fundacional del proyecto
  (Mayo 2026). Documenta el porqué de cada decisión (modelos,
  memoria, infra) y trae al final una bitácora de actualizaciones
  de versión. Las versiones puntuales se actualizan en el PDF + en
  los manifests; los cambios de decisión arquitectónica van por ADR.

## ADRs actuales

| # | Título | Estado |
|---|--------|--------|
| 001 | Monorepo vs multirepo | Aceptado |
| 002 | Gemma + Qwen dual stack | Reemplazado por ADR-028 |
| 003 | Mem0 vs Letta para memoria | Superseded by ADR-010 |
| 004 | Postgres + pgvector vs Pinecone | Aceptado — con nota de ADR-028 (embeddings por API) |
| 005 | Supabase en MVP, Postgres self-hosted en V2 | Aceptado |
| 006 | Pinear next-auth a 5.0.0-beta.31 hasta release estable | Aceptado |
| 007 | Políticas operacionales de memoria (decay, retention diferenciada, encriptación a nivel campo) | Aceptado |
| 008 | Modelo de embedding — BAAI bge-m3 (1024-dim, on-prem) | Aceptado — ratificado por ADR-028 (serving por API) |
| 009 | Topología de serving vLLM y parsers de tool-calling | Reemplazado por ADR-028 |
| 010 | Arquitectura de memoria v2 — engine in-house sobre storage cifrado propio (supersede ADR-003) | Aceptado — enmendado por ADR-028 (D2, D3, D4 y D6) |
| 011 | Auth permanece layer-split — criterio de feature-packages vs dominios ordinarios | Aceptado — refinado por ADR-015 |
| 012 | Modelo conversacional Gemma 4 12B y topología single_process co-residente en 16 GB | Reemplazado por ADR-028 |
| 013 | Config de serving LLM explícita (endpoints → served models) | Reemplazado por ADR-028 (el esquema) |
| 014 | Motor de serving local = Ollama/GGUF en 16 GB (vLLM reservado para 24 GB+) | Reemplazado por ADR-028 |
| 015 | Librería JWT = PyJWT (sale python-jose); bcrypt directo (sale passlib) | Aceptado |
| 016 | Código compartido web/mobile vía packages/core (ex-ADR-012) | Aceptado |
| 017 | App de administración interna (apps/admin): observabilidad y control plane | Aceptado — enmendado por ADR-028 (soberanía, telemetría de GPU, D7) |
| 018 | Playground de modelos en el panel admin (control plane, fase 1) | Aceptado — enmendado por ADR-028 (serving real por API) |
| 019 | Playground agente observado (tool-loop sin efectos) — refina ADR-018 | Aceptado — enmendado por ADR-028 (serving real por API, alias `agente`) |
| 020 | Circuit breaker per-proceso — cota de despliegue 1-2 workers | Aceptado — enmendado por ADR-028 (premisa de la GPU única) |
| 021 | Pasada asíncrona del agente qwen (memoria + tools por detrás de gemma) — refina ADR-019 | Propuesto — parcialmente superseded por ADR-022; enmendado por ADR-028 (alias de modelo) |
| 022 | Tools de agente síncronas en el chat de producción (calendar/task) — supersede parcial de ADR-021 | Aceptado |
| 023 | Modelo de evento de calendario (recurrencia, timezone, all-day, multi-día) | Aceptado |
| 024 | Streaming SSE cosmético — atomicidad transaccional del turno por encima del TTFB real (SCAL-01) | Aceptado |
| 025 | Recall de HNSW a escala — índice global con post-filtro user_id y ef_search sin tunear (MEM-SACRED-02) | Propuesto — diferido |
| 026 | Contrato de intake del onboarding (señales → operativo vs memoria) | Propuesto |
| 027 | Web en Vercel + backend/GPU en la PC por Tailscale Funnel, con toggle de IA | Reemplazado por ADR-028 |
| 028 | La inferencia se consume por API (proveedores compatibles con OpenAI, alias `conversacional`/`agente`) | Aceptado |
| 029 | La voz hablada se consume por API (STT/TTS en streaming, audio efímero) | Propuesto |
| 030 | El modo se decide por mensaje y la memoria es una sola (enrutador de modos; lo sensible se marca, no se aísla) | Propuesto |
| 031 | Cliente nativo de la Messages API de Anthropic (protocolo `anthropic`, razonamiento preservado en la vuelta) | Aceptado |

> Nota de numeración: el ADR-016 era un segundo ADR-012 renumerado. El modelo de
> evento de calendario —antes un ADR-018 "lógico" duplicado del file
> `ADR-018-admin-playground-modelos`— se **renumeró a ADR-023** (DOC-09); su file es
> `ADR-023-calendar-event-model.md`. Así `ADR-018` queda inequívoco (= el playground
> del panel admin). Esta tabla es la fuente de lectura.

## Cómo crear un ADR nuevo

Ver `skills/adr-create/SKILL.md`. En resumen:

1. Copiar la plantilla base (cualquiera de los ADRs existentes sirve).
2. Numerar consecutivo (ADR-006, ADR-007, ...).
3. Llenar contexto, decisión, consecuencias.
4. PR con review humano. Cuando el ADR queda en "Aceptado", no se
   modifica más: si la decisión cambia, se crea un ADR nuevo que
   marca al anterior como "Superseded by ADR-XXX".
