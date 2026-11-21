import type { NextConfig } from "next";

function buildLandingSecurityHeaders() {
  const isProd = process.env.NODE_ENV === "production";
  const scriptSrc = isProd ? "'self' 'unsafe-inline'" : "'self' 'unsafe-inline' 'unsafe-eval'";
  const connectSrc = isProd ? "'self'" : "'self' ws: wss:";

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
    `connect-src ${connectSrc}`,
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
  // El indicador de dev de Next cae por defecto en bottom-left, justo sobre la
  // firma de marca del deck (logo + "Ynara"). Lo movemos a top-right para no
  // pisarla (abajo-derecha ya lo ocupa el contador). Solo afecta a `next dev`.
  devIndicators: {
    position: "top-right",
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: buildLandingSecurityHeaders(),
      },
    ];
  },
  experimental: {
    optimizePackageImports: ["three", "gsap", "lenis"],
  },
};

export default nextConfig;
