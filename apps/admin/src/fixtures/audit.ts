import type { ModeIdT } from "@/features/_shared/schemas";
import {
  AdminAuditPage,
  type AdminAuditPageT,
  type AdminAuditRowT,
  type AuditFilterState,
} from "@/features/audit/schemas";
import { FIXTURE_NOW, minutesBack, mulberry32, pick, seededUuid } from "./seed";

/**
 * Fixture de `GET /v1/admin/audit` (blueprint §4.7).
 *
 * Genera ~200 filas deterministas (mezcla de operations/layers/modes/ids de
 * modelo, ~18% sensibles) y expone `auditPage(filters, limit, offset)` que filtra y
 * pagina EN MEMORIA — el mismo contrato que el backend hará con WHERE + LIMIT/
 * OFFSET, pero del lado del fixture para que la pantalla de audit sea navegable
 * sin backend.
 *
 * PRIVACIDAD: las filas se construyen contra `AdminAuditRow` (sin `record_hash`,
 * sin `target_id`). Esos campos NO existen ni siquiera en el fixture.
 */

const ROW_COUNT = 200;

const OPERATIONS = ["read", "write", "update", "delete"] as const;
const LAYERS = ["semantic", "episodic", "procedural"] as const;
const MODES: ModeIdT[] = ["productividad", "estudio", "bienestar", "vida", "memoria"];
/** Ids de modelo como los informa un proveedor (ADR-028); `agente` = cliente sin id. */
export const FIXTURE_MODEL_IDS = [
  "org/agent-2026-06-01",
  "org/agent-2026-03-15",
  "respaldo/agent-v2",
  "agente",
] as const;
/** Valores de antes de ADR-028: solo en las filas viejas, sin `origin_model_id`. */
const LEGACY_MODELS = ["gemma", "qwen"] as const;
const TOOLS = [
  "memory.search",
  "memory.write",
  "memory.consolidate",
  "agenda.read",
  "agenda.write",
  null,
] as const;

/**
 * Las ~200 filas, ordenadas `createdAt` DESC (la fila 0 es la más reciente).
 * Se generan una sola vez (módulo cargado = dataset estable durante la sesión).
 */
function buildRows(now: Date): AdminAuditRowT[] {
  const rand = mulberry32(9001);
  const rows: AdminAuditRowT[] = [];
  for (let i = 0; i < ROW_COUNT; i++) {
    const created_at = minutesBack(i * 11 + Math.floor(rand() * 7), now);
    const operation = pick(OPERATIONS, Math.floor(rand() * OPERATIONS.length));
    const target_layer = pick(LAYERS, Math.floor(rand() * LAYERS.length));
    const origin_mode = rand() < 0.15 ? null : pick(MODES, Math.floor(rand() * MODES.length));
    // ~10% sin modelo (acción por HTTP o seed), ~10% filas viejas (solo el enum de
    // antes) y el resto con el id que informó el proveedor.
    const modelRoll = rand();
    const modelIndex = Math.floor(rand() * FIXTURE_MODEL_IDS.length);
    const isLegacy = modelRoll >= 0.1 && modelRoll < 0.2;
    rows.push({
      id: seededUuid(9001 + i),
      // i creciente = más viejo → minutos hacia atrás crecientes → DESC por created_at
      created_at,
      operation,
      target_layer,
      origin_mode,
      origin_model: isLegacy ? pick(LEGACY_MODELS, modelIndex % LEGACY_MODELS.length) : null,
      origin_model_id: modelRoll < 0.2 ? null : pick(FIXTURE_MODEL_IDS, modelIndex),
      origin_tool: pick(TOOLS, Math.floor(rand() * TOOLS.length)),
      sensitive: rand() < 0.18,
    });
  }
  return rows;
}

const ALL_ROWS: AdminAuditRowT[] = buildRows(FIXTURE_NOW);

/** Aplica los filtros del cliente (campo `null` = sin filtrar por ese campo). */
function matchesFilters(row: AdminAuditRowT, f: AuditFilterState): boolean {
  if (f.operation && row.operation !== f.operation) return false;
  if (f.targetLayer && row.target_layer !== f.targetLayer) return false;
  if (f.originMode && row.origin_mode !== f.originMode) return false;
  // Id exacto, como el backend: las filas viejas (solo enum) no matchean.
  if (f.originModelId && row.origin_model_id !== f.originModelId) return false;
  if (f.sensitive !== null && row.sensitive !== f.sensitive) return false;
  return true;
}

/**
 * Filtra + pagina en memoria y devuelve una `AdminAuditPage` parseada por su
 * Zod. `total` es el conteo de la query CON filtros (no la tabla entera), igual
 * que haría el backend; `sensitive_pct` se calcula sobre ese conjunto filtrado.
 */
export function auditPage(
  filters: AuditFilterState,
  limit: number,
  offset: number,
): AdminAuditPageT {
  const filtered = ALL_ROWS.filter((row) => matchesFilters(row, filters));
  const total = filtered.length;
  const sensitiveCount = filtered.filter((r) => r.sensitive).length;
  const sensitive_pct = total === 0 ? 0 : Number(((sensitiveCount / total) * 100).toFixed(1));
  const items = filtered.slice(offset, offset + limit);

  return AdminAuditPage.parse({ items, total, sensitive_pct });
}

/** Dataset completo (read-only) por si una preview lo necesita sin paginar. */
export const allAuditRows: readonly AdminAuditRowT[] = ALL_ROWS;
