import type { NextConfig } from "next";
import { buildSecurityHeaders } from "./src/lib/securityHeaders";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Orígenes de dev permitidos para los assets de `/_next` (Next 16 bloquea con 403
  // los requests cross-origin al dev server desde un host no listado). Se setea por
  // env `NEXT_DEV_ALLOWED_ORIGINS` (CSV) SOLO al compartir el dev server por la LAN
  // (ej. `192.168.0.10`); vacío en local normal.
  // No afecta producción (allowedDevOrigins es solo del dev server).
  allowedDevOrigins: process.env.NEXT_DEV_ALLOWED_ORIGINS?.split(",")
    .map((origin) => origin.trim())
    .filter(Boolean),
  // TODO: configurar imágenes externas cuando sepamos qué dominios
  // (R2, avatars, etc.) van a aparecer.
  images: {
    remotePatterns: [],
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: buildSecurityHeaders({
          apiUrl: process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080",
        }),
      },
    ];
  },
  experimental: {
    // TODO: revisar flags experimentales de Next 16 que nos sirvan.
  },
};

export default nextConfig;
