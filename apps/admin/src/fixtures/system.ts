import { AdminSystemOut, type AdminSystemOutT } from "@/features/system/schemas";
import { FIXTURE_NOW, minutesBack } from "./seed";

/**
 * Fixture de `GET /v1/admin/system` (blueprint §4.7).
 *
 * guard active=true / isProdInDev=false (estado sano: dev apunta a la DB de dev),
 * Postgres up 3.2ms, Redis up 0.8ms, runtime con los alias agente/conversacional, 5 modos,
 * schema head Alembic y buildVersion.
 *
 * Los proveedores de IA cubren los tres estados para ver la pantalla entera en dev:
 * LLM por API con el principal sano y el respaldo caído, y embeddings con el backend
 * fake (sin configurar). Hosts y modelos genéricos, de ejemplo.
 *
 * No lleva `range`: es runtime/config, una sola foto.
 */

export function systemFixture(now: Date = FIXTURE_NOW): AdminSystemOutT {
  const data: AdminSystemOutT = {
    guard: {
      active: true,
      db_target: "ynara_dev@localhost:5432",
      is_prod_in_dev: false,
    },
    services: {
      postgres: {
        up: true,
        latency_ms: 3.2,
        detail: "SELECT 1 · pgvector OK",
        checked_at: minutesBack(0, now),
      },
      redis: {
        up: true,
        latency_ms: 0.8,
        detail: "PING → PONG",
        checked_at: minutesBack(0, now),
      },
    },
    runtime: {
      models: ["agente", "conversacional"],
      modes: ["productividad", "estudio", "bienestar", "vida", "memoria"],
      schema_head: "20260615_0200",
      build_version: "admin@0.1.0",
    },
    ai: {
      llm: {
        backend: "api",
        providers: [
          {
            name: "principal",
            host: "api.example.com",
            models: [
              { alias: "agente", model_id: "proveedor/modelo-agente" },
              { alias: "conversacional", model_id: "proveedor/modelo-chat" },
            ],
            status: "ok",
          },
          {
            name: "respaldo",
            host: "respaldo.example.com",
            models: [
              { alias: "agente", model_id: "proveedor-b/modelo-agente" },
              { alias: "conversacional", model_id: "proveedor-b/modelo-chat" },
            ],
            status: "down",
          },
        ],
      },
      embeddings: {
        backend: "fake",
        host: null,
        model: "BAAI/bge-m3",
        dim: 1024,
        status: "unconfigured",
      },
    },
  };

  return AdminSystemOut.parse(data);
}
