import { z } from "zod";

/**
 * Contrato de `GET /v1/admin/system` (blueprint §4.6).
 *
 * Pantalla F1.6: System Health. NO lleva `range` (es runtime/config, no
 * negocio): guard anti-prod-en-dev + estado de servicios (Postgres/Redis) +
 * inventario de runtime (modelos, modos, schema head, build) + proveedores de IA
 * (ADR-028: modo, host, alias → id de modelo y estado de cada uno).
 *
 * Sin queries de negocio ni datos de usuario: `SELECT 1`, PING Redis, el health
 * de cada proveedor y lectura de config. El `guard` es lo primero que ve el
 * operador (banner): si una DB de prod está apuntada en dev, `isProdInDev=true` y
 * el banner se pone en rojo.
 *
 * De cada proveedor llega solo el host de su URL base, nunca la URL entera ni la
 * credencial. El parse es además el gate: `z.object` descarta cualquier campo que
 * no esté declarado acá, así que una clave de más del backend no llega al render.
 */

/** Salud de un servicio de infra (DB / cache). OK se pinta azul plano, no verde. */
const ServiceStatus = z.object({
  up: z.boolean(),
  latency_ms: z.number(),
  detail: z.string(),
  checked_at: z.string(),
});

/** Modo de un cliente de IA: `fake` (determinista, sin red) o `api` (proveedor real). */
export const AiBackend = z.enum(["fake", "api"]);
export type AiBackendT = z.infer<typeof AiBackend>;

/**
 * Estado de un proveedor de IA: `ok` (respondió el health), `down` (no respondió)
 * o `unconfigured` (con el backend `fake` no se sondea: el proveedor no está en uso).
 */
export const AiStatus = z.enum(["ok", "down", "unconfigured"]);
export type AiStatusT = z.infer<typeof AiStatus>;

/** Un proveedor de `LLM_PROVIDERS`: nombre, host, sus alias → id de modelo y estado. */
export const SystemLlmProvider = z.object({
  name: z.string(),
  host: z.string(),
  models: z.array(z.object({ alias: z.string(), model_id: z.string() })),
  status: AiStatus,
});
export type SystemLlmProviderT = z.infer<typeof SystemLlmProvider>;

/** Cliente de embeddings: modo, host (`null` sin URL), modelo, dimensión y estado. */
export const SystemEmbeddings = z.object({
  backend: AiBackend,
  host: z.string().nullable(),
  model: z.string(),
  dim: z.number().int(),
  status: AiStatus,
});
export type SystemEmbeddingsT = z.infer<typeof SystemEmbeddings>;

/** Proveedores de IA: el LLM (en el orden de `LLM_PROVIDERS`) y los embeddings. */
export const SystemAi = z.object({
  llm: z.object({
    backend: AiBackend,
    providers: z.array(SystemLlmProvider),
  }),
  embeddings: SystemEmbeddings,
});
export type SystemAiT = z.infer<typeof SystemAi>;

export const AdminSystemOut = z.object({
  guard: z.object({
    active: z.boolean(),
    db_target: z.string(),
    is_prod_in_dev: z.boolean(),
  }),
  services: z.object({
    postgres: ServiceStatus,
    redis: ServiceStatus,
  }),
  runtime: z.object({
    models: z.array(z.string()),
    modes: z.array(z.string()),
    schema_head: z.string(),
    build_version: z.string(),
  }),
  ai: SystemAi,
});

export type AdminSystemOutT = z.infer<typeof AdminSystemOut>;
