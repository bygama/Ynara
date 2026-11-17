import { z } from "zod";

/**
 * Contrato de `GET /v1/admin/connectivity` (Conexión / Compartir).
 *
 * Espejo Zod EXACTO del Pydantic del backend (snake_case): valida la respuesta en
 * el borde igual que el resto del panel. Estado del tailnet de Tailscale + las URLs
 * para compartir las superficies (panel admin y app web) con otra máquina. Sin
 * secretos (regla #4):
 * `tailnet_ip`/`hostname` son la identidad de la propia máquina en el tailnet, no
 * datos de usuario.
 */

/** Estado del daemon de Tailscale en el host (probe read-only). */
export const TailscaleStatus = z.object({
  up: z.boolean(),
  hostname: z.string().nullable().optional(),
  tailnet_ip: z.string().nullable().optional(),
  detail: z.string(),
});
export type TailscaleStatusT = z.infer<typeof TailscaleStatus>;

/** Una superficie de Ynara compartida por el tailnet (panel admin o app web). */
export const ShareTarget = z.object({
  label: z.string(),
  url: z.string().url(),
  port: z.number(),
});
export type ShareTargetT = z.infer<typeof ShareTarget>;

/** Estado de conectividad para compartir las superficies con otra máquina. */
export const ConnectivityOut = z.object({
  tailscale: TailscaleStatus,
  targets: z.array(ShareTarget),
});
export type ConnectivityOutT = z.infer<typeof ConnectivityOut>;
