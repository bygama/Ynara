import type { NextConfig } from "next";

function buildAdminSecurityHeaders() {
  const isProd = process.env.NODE_ENV === "production";
  const rawApiUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8080";
  let apiOrigin: string | null = null;
  try {
    const parsed = new URL(rawApiUrl.trim());
    if (parsed.protocol === "http:" || parsed.protocol === "https:") {
      apiOrigin = parsed.origin;
    }
  } catch {
    apiOrigin = null;
  }

  const connectSources = ["'self'"];
  if (apiOrigin) connectSources.push(apiOrigin);
  if (!isProd) connectSources.push("ws:", "wss:");

  const scriptSrc = isProd ? "'self' 'unsafe-inline'" : "'self' 'unsafe-inline' 'unsafe-eval'";
  const cspDirectives = [
    "default-src 'self'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    "object-src 'none'",
    `script-src ${scriptSrc}`,
    "style-src 'self' 'unsafe-inline'",
    "img-src 'self' data: blob:",
    "font-src 'self' data:",
    `connect-src ${connectSources.join(" ")}`,
  ];
  if (isProd) cspDirectives.push("upgrade-insecure-requests");

  const headers = [
    { key: "Content-Security-Policy", value: cspDirectives.join("; ") },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    {
      key: "Permissions-Policy",
      value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
    },
    { key: "X-DNS-Prefetch-Control", value: "off" },
  ];

  if (isProd) {
    headers.push({
      key: "Strict-Transport-Security",
      value: "max-age=63072000; includeSubDomains; preload",
    });
  }

  return headers;
}

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // TODO: configurar imágenes externas cuando sepamos qué dominios
  // (R2, avatars, etc.) van a aparecer.
  images: {
    remotePatterns: [],
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: buildAdminSecurityHeaders(),
      },
    ];
  },
  experimental: {
    // TODO: revisar flags experimentales de Next 16 que nos sirvan.
  },
};

export default nextConfig;
