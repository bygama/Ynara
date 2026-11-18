import { describe, expect, it } from "vitest";
import { AdminAuditPage, EMPTY_AUDIT_FILTERS } from "@/features/audit/schemas";
import { AdminMoatOut } from "@/features/moat/schemas";
import { AdminModesOut } from "@/features/modes/schemas";
import { AdminOverviewOut } from "@/features/overview/schemas";
import { PlaygroundAgentOut, PlaygroundOut, ServingOut } from "@/features/playground/schemas";
import { AdminSystemOut } from "@/features/system/schemas";
import { AdminUsersOut } from "@/features/users/schemas";
import { auditPage, FIXTURE_MODEL_IDS } from "@/fixtures/audit";
import { moatFixture } from "@/fixtures/moat";
import { modesFixture } from "@/fixtures/modes";
import { overviewFixture } from "@/fixtures/overview";
import { playgroundAgentEcho, playgroundEcho, servingFixture } from "@/fixtures/playground";
import { systemFixture } from "@/fixtures/system";
import { usersFixture } from "@/fixtures/users";
import { RANGE_IDS, type RangeId } from "@/stores/range";

/**
 * Test de CONTRATO (blueprint §6 paso 8): cada fixture de `/v1/admin/*` debe
 * parsear limpio contra su Zod. Es la garantía viva de que los datos de demo y
 * el contrato de API no driftean — si un fixture deja de cumplir el shape, el
 * test falla y nombra el endpoint.
 *
 * (Los `*Fixture`/`auditPage` ya hacen `Schema.parse` internamente, así que un
 * fixture roto tira al construirse; acá lo re-parseamos explícito para que el
 * test sea el lugar canónico donde se documenta y verifica el contrato, y para
 * cubrir los 4 rangos de cada vista segmentada por tiempo.)
 */

const RANGES = RANGE_IDS as readonly RangeId[];

/**
 * Respuesta real de `GET /v1/admin/serving` con `LLM_BACKEND=api`, tal como la
 * arma el handler del backend (`admin_serving`) con los alias de
 * `ynara.config.json`. Es la forma del wire, no un fixture: si el Pydantic cambia
 * y el Zod no, este test lo marca.
 */
const SERVING_API_PAYLOAD = {
  backend: "api",
  is_real: true,
  serving_healthy: true,
  request_timeout_s: 20.0,
  low_perf_available: true,
  models: [
    {
      alias: "conversacional",
      role: "conversational",
      writes_memory: false,
      context_tokens: 8192,
      healthy: true,
      default_thinking: false,
    },
    {
      alias: "agente",
      role: "agent",
      writes_memory: true,
      context_tokens: 32768,
      healthy: true,
      default_thinking: true,
    },
  ],
  embedder: "BAAI/bge-m3",
  reranker: "passthrough",
};

/** La misma respuesta con el backend fake (default de dev): sin serving real. */
const SERVING_FAKE_PAYLOAD = {
  ...SERVING_API_PAYLOAD,
  backend: "fake",
  is_real: false,
  low_perf_available: false,
};

/** Proveedores del bloque `ai` de `GET /v1/admin/system` con `LLM_BACKEND=api`. */
const PRINCIPAL_PAYLOAD = {
  name: "principal",
  host: "api.example.com",
  models: [
    { alias: "agente", model_id: "proveedor/modelo-agente" },
    { alias: "conversacional", model_id: "proveedor/modelo-chat" },
  ],
  status: "ok",
};
const RESPALDO_PAYLOAD = {
  ...PRINCIPAL_PAYLOAD,
  name: "respaldo",
  host: "respaldo.example.com",
  status: "down",
};
const EMBEDDINGS_API_PAYLOAD = {
  backend: "api",
  host: "embeddings.example.com",
  model: "BAAI/bge-m3",
  dim: 1024,
  status: "ok",
};

/**
 * Respuesta real de `GET /v1/admin/system` con `LLM_BACKEND=api` y
 * `EMBEDDING_BACKEND=api`, tal como la arma el handler del backend
 * (`admin_system`): de cada proveedor llega solo el host, nunca la URL ni la
 * credencial. Es la forma del wire, no un fixture.
 */
const SYSTEM_API_PAYLOAD = {
  guard: { active: true, db_target: "localhost", is_prod_in_dev: false },
  services: {
    postgres: {
      up: true,
      latency_ms: 1.42,
      detail: "SELECT 1",
      checked_at: "2026-11-17T12:00:00Z",
    },
    redis: { up: true, latency_ms: 0.31, detail: "PING", checked_at: "2026-11-17T12:00:00Z" },
  },
  runtime: {
    models: ["agente", "conversacional"],
    modes: ["productividad", "estudio", "bienestar", "vida", "memoria"],
    schema_head: "20260615_0200",
    build_version: "0.1.0",
  },
  ai: {
    llm: { backend: "api", providers: [PRINCIPAL_PAYLOAD, RESPALDO_PAYLOAD] },
    embeddings: EMBEDDINGS_API_PAYLOAD,
  },
};

/** La misma respuesta con los dos backends fake (default de dev): nada se sondea. */
const SYSTEM_FAKE_PAYLOAD = {
  ...SYSTEM_API_PAYLOAD,
  ai: {
    llm: { backend: "fake", providers: [] },
    embeddings: { ...EMBEDDINGS_API_PAYLOAD, backend: "fake", host: null, status: "unconfigured" },
  },
};

/** Fila de `GET /v1/admin/audit` escrita después de ADR-028: solo el id de modelo. */
const AUDIT_ROW_PAYLOAD = {
  id: "3f2b8c1e-9a4d-4e7b-8c2a-1d5e6f7a8b9c",
  created_at: "2026-11-17T12:00:00Z",
  operation: "write",
  target_layer: "semantic",
  origin_mode: "memoria",
  origin_model: null,
  origin_model_id: "org/agent-2026-06-01",
  origin_tool: "memory.consolidate",
  sensitive: false,
};

/**
 * Respuesta real de `GET /v1/admin/audit`, tal como la arma el backend
 * (`AdminMetricsService.audit`): una fila con el id que informó el proveedor, una
 * fila vieja con solo el enum de antes y una acción por HTTP sin modelo. Es la
 * forma del wire, no un fixture.
 */
const AUDIT_API_PAYLOAD = {
  items: [
    AUDIT_ROW_PAYLOAD,
    {
      ...AUDIT_ROW_PAYLOAD,
      id: "7c9e6679-7425-40de-944b-e07fc1f90ae7",
      created_at: "2026-06-01T09:30:00Z",
      operation: "update",
      target_layer: "episodic",
      origin_model: "qwen",
      origin_model_id: null,
      sensitive: true,
    },
    {
      ...AUDIT_ROW_PAYLOAD,
      id: "9b2d4f6a-1c3e-4a5b-8d7f-0e2c4a6b8d0f",
      operation: "delete",
      origin_mode: null,
      origin_model_id: null,
      origin_tool: null,
    },
  ],
  total: 3,
  sensitive_pct: 33.3,
};

describe("contrato fixtures ↔ Zod (blueprint §4)", () => {
  it("overview parsea en todos los rangos", () => {
    for (const range of RANGES) {
      expect(() => AdminOverviewOut.parse(overviewFixture(range))).not.toThrow();
    }
  });

  it("users parsea en todos los rangos", () => {
    for (const range of RANGES) {
      expect(() => AdminUsersOut.parse(usersFixture(range))).not.toThrow();
    }
  });

  it("modes parsea en todos los rangos", () => {
    for (const range of RANGES) {
      expect(() => AdminModesOut.parse(modesFixture(range))).not.toThrow();
    }
  });

  it("moat parsea en todos los rangos", () => {
    for (const range of RANGES) {
      expect(() => AdminMoatOut.parse(moatFixture(range))).not.toThrow();
    }
  });

  it("system parsea (sin rango)", () => {
    expect(() => AdminSystemOut.parse(systemFixture())).not.toThrow();
  });

  it("system: el fixture cubre los tres estados de proveedor", () => {
    const { ai } = systemFixture();
    const statuses = new Set([...ai.llm.providers.map((p) => p.status), ai.embeddings.status]);
    expect(statuses).toEqual(new Set(["ok", "down", "unconfigured"]));
  });

  it("system: la respuesta real del backend (api y fake) parsea contra AdminSystemOut", () => {
    const api = AdminSystemOut.parse(SYSTEM_API_PAYLOAD);
    expect(api.ai.llm.providers.map((p) => [p.name, p.host, p.status])).toEqual([
      ["principal", "api.example.com", "ok"],
      ["respaldo", "respaldo.example.com", "down"],
    ]);
    const fake = AdminSystemOut.parse(SYSTEM_FAKE_PAYLOAD);
    expect(fake.ai.llm.providers).toEqual([]);
    expect(fake.ai.embeddings).toMatchObject({
      backend: "fake",
      host: null,
      status: "unconfigured",
    });
  });

  it("system: el parse descarta lo que el contrato no declara (ni URL ni credencial)", () => {
    const leaky = {
      ...SYSTEM_API_PAYLOAD,
      ai: {
        llm: {
          backend: "api",
          providers: [
            {
              ...PRINCIPAL_PAYLOAD,
              base_url: "https://api.example.com/v1",
              api_key: "sk-test-filtrada",
            },
            RESPALDO_PAYLOAD,
          ],
        },
        embeddings: { ...EMBEDDINGS_API_PAYLOAD, api_key: "sk-test-filtrada" },
      },
    };
    const parsed = AdminSystemOut.parse(leaky);
    const serialized = JSON.stringify(parsed);
    expect(serialized).not.toContain("sk-test-filtrada");
    expect(serialized).not.toContain("://");
    expect(parsed.ai.llm.providers[0]).not.toHaveProperty("api_key");
    expect(parsed.ai.embeddings).not.toHaveProperty("api_key");
  });

  it("system rechaza un estado de proveedor fuera del contrato", () => {
    const unknownStatus = {
      ...SYSTEM_API_PAYLOAD,
      ai: {
        ...SYSTEM_API_PAYLOAD.ai,
        llm: { backend: "api", providers: [{ ...PRINCIPAL_PAYLOAD, status: "healthy" }] },
      },
    };
    expect(AdminSystemOut.safeParse(unknownStatus).success).toBe(false);
  });

  it("audit page parsea sin filtros y paginada", () => {
    expect(() => AdminAuditPage.parse(auditPage(EMPTY_AUDIT_FILTERS, 50, 0))).not.toThrow();
    expect(() => AdminAuditPage.parse(auditPage(EMPTY_AUDIT_FILTERS, 25, 100))).not.toThrow();
  });

  it("audit page respeta filtros y paginación en memoria", () => {
    const all = auditPage(EMPTY_AUDIT_FILTERS, 1000, 0);
    expect(all.total).toBe(200);
    expect(all.items.length).toBe(200);

    // Filtro por operación: cada fila devuelta matchea, total ≤ total global.
    const writes = auditPage({ ...EMPTY_AUDIT_FILTERS, operation: "write" }, 1000, 0);
    expect(writes.items.every((r) => r.operation === "write")).toBe(true);
    expect(writes.total).toBeLessThanOrEqual(all.total);
    expect(writes.items.length).toBe(writes.total);

    // Filtro por sensible.
    const sensitive = auditPage({ ...EMPTY_AUDIT_FILTERS, sensitive: true }, 1000, 0);
    expect(sensitive.items.every((r) => r.sensitive)).toBe(true);

    // Paginación: limit recorta, offset desplaza.
    const firstPage = auditPage(EMPTY_AUDIT_FILTERS, 50, 0);
    const secondPage = auditPage(EMPTY_AUDIT_FILTERS, 50, 50);
    expect(firstPage.items.length).toBe(50);
    expect(secondPage.items.length).toBe(50);
    expect(firstPage.items[0]?.id).not.toBe(secondPage.items[0]?.id);
  });

  it("audit NUNCA expone record_hash ni target_id (privacidad soberana)", () => {
    const page = auditPage(EMPTY_AUDIT_FILTERS, 1000, 0);
    for (const row of page.items) {
      expect(row).not.toHaveProperty("record_hash");
      expect(row).not.toHaveProperty("target_id");
    }
  });

  it("audit: la respuesta real del backend parsea con id de modelo, enum viejo o sin modelo", () => {
    const page = AdminAuditPage.parse(AUDIT_API_PAYLOAD);
    expect(page.items.map((r) => [r.origin_model_id, r.origin_model])).toEqual([
      ["org/agent-2026-06-01", null],
      [null, "qwen"],
      [null, null],
    ]);
  });

  it("audit rechaza una fila sin origin_model_id (la forma previa a ADR-028)", () => {
    const withoutModelId = { ...AUDIT_ROW_PAYLOAD, origin_model_id: undefined };
    expect(
      AdminAuditPage.safeParse({ ...AUDIT_API_PAYLOAD, items: [withoutModelId] }).success,
    ).toBe(false);
  });

  it("audit: el fixture cubre filas con id de modelo, filas viejas y filas sin modelo", () => {
    const { items } = auditPage(EMPTY_AUDIT_FILTERS, 1000, 0);
    const withId = items.filter((r) => r.origin_model_id !== null);
    const legacy = items.filter((r) => r.origin_model_id === null && r.origin_model !== null);
    const withoutModel = items.filter((r) => r.origin_model_id === null && r.origin_model === null);
    expect(withId.length).toBeGreaterThan(0);
    expect(legacy.length).toBeGreaterThan(0);
    expect(withoutModel.length).toBeGreaterThan(0);
    // Desde ADR-028 se escribe solo el id: ninguna fila trae los dos.
    expect(withId.every((r) => r.origin_model === null)).toBe(true);
  });

  it("audit filtra por el id exacto del modelo y deja afuera las filas viejas", () => {
    const all = auditPage(EMPTY_AUDIT_FILTERS, 1000, 0);
    const [modelId] = FIXTURE_MODEL_IDS;
    const byId = auditPage({ ...EMPTY_AUDIT_FILTERS, originModelId: modelId }, 1000, 0);
    expect(byId.total).toBeGreaterThan(0);
    expect(byId.items.length).toBe(byId.total);
    expect(byId.items.every((r) => r.origin_model_id === modelId)).toBe(true);
    expect(byId.total).toBe(all.items.filter((r) => r.origin_model_id === modelId).length);

    // Igual que el backend: ni prefijos ni otra capitalización.
    const prefix = auditPage({ ...EMPTY_AUDIT_FILTERS, originModelId: "org/agent" }, 1000, 0);
    expect(prefix.total).toBe(0);
    const upper = auditPage(
      { ...EMPTY_AUDIT_FILTERS, originModelId: modelId.toUpperCase() },
      1000,
      0,
    );
    expect(upper.total).toBe(0);
    // El enum de antes no es un id: las filas viejas no matchean.
    const legacy = auditPage({ ...EMPTY_AUDIT_FILTERS, originModelId: "qwen" }, 1000, 0);
    expect(legacy.total).toBe(0);
  });

  // ── Playground (Fase A probe + Fase B agente) ───────────────────────────

  it("serving fixture parsea contra ServingOut", () => {
    expect(() => ServingOut.parse(servingFixture())).not.toThrow();
  });

  it("serving: la respuesta real del backend (api y fake) parsea contra ServingOut", () => {
    for (const payload of [SERVING_API_PAYLOAD, SERVING_FAKE_PAYLOAD]) {
      const parsed = ServingOut.parse(payload);
      expect(parsed.models.map((m) => m.alias)).toEqual(["conversacional", "agente"]);
    }
  });

  it("serving rechaza la forma previa a los alias (served_name, backend vllm)", () => {
    const legacyModel = {
      key: "qwen-3.5-9b",
      served_name: "qwen",
      role: "agent",
      writes_memory: true,
      context_window: 262144,
      max_model_len: 32768,
      quantization: "awq_marlin",
      tool_parser: "hermes",
      healthy: true,
      default_thinking: true,
    };
    expect(ServingOut.safeParse({ ...SERVING_API_PAYLOAD, backend: "vllm" }).success).toBe(false);
    expect(ServingOut.safeParse({ ...SERVING_API_PAYLOAD, models: [legacyModel] }).success).toBe(
      false,
    );
  });

  it("playground probe echo parsea contra PlaygroundOut", () => {
    const body = {
      model: "agente",
      message: "Hola test",
      params: { max_tokens: 256, temperature: 0.7, low_perf: false },
      thinking: null,
    };
    expect(() => PlaygroundOut.parse(playgroundEcho(body))).not.toThrow();
  });

  it("playground probe echo con low_perf y thinking off parsea contra PlaygroundOut", () => {
    const body = {
      model: "conversacional",
      message: "Test low perf",
      params: { max_tokens: 256, temperature: 0.2, low_perf: true },
      thinking: false,
    };
    expect(() => PlaygroundOut.parse(playgroundEcho(body))).not.toThrow();
  });

  it("playground agent echo parsea contra PlaygroundAgentOut", () => {
    const body = {
      model: "agente",
      message: "Creá un evento para mañana",
      params: { max_tokens: 512, temperature: 0.7, low_perf: false },
      thinking: null,
    };
    const result = playgroundAgentEcho(body);
    expect(() => PlaygroundAgentOut.parse(result)).not.toThrow();
    // Debe tener las 2 tool-calls de ejemplo (calendar + reminder).
    expect(result.actions).toHaveLength(2);
    expect(result.actions[0]?.name).toBe("calendar.create_event");
    expect(result.actions[1]?.name).toBe("reminder.set");
    // Ambas devuelven not_wired (stub, cero efecto real).
    expect(result.actions[0]?.result).toBe("not_wired");
    expect(result.actions[1]?.result).toBe("not_wired");
  });
});
