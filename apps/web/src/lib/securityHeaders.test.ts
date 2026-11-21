import { describe, expect, it } from "vitest";
import { buildContentSecurityPolicy, buildSecurityHeaders } from "./securityHeaders";

describe("securityHeaders", () => {
  it("genera CSP estricta en producción limitada al origin de la API", () => {
    const csp = buildContentSecurityPolicy({
      apiUrl: "https://api.ynara.app/v1",
      isProduction: true,
    });

    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("connect-src 'self' https://api.ynara.app");
    expect(csp).toContain("img-src 'self' data: blob:");
    expect(csp).toContain("upgrade-insecure-requests");
    expect(csp).not.toContain("'unsafe-eval'");
    expect(csp).not.toContain("ws:");
  });

  it("permite unsafe-eval y websockets solo fuera de producción", () => {
    const csp = buildContentSecurityPolicy({
      apiUrl: "http://localhost:8080",
      isProduction: false,
    });

    expect(csp).toContain("'unsafe-eval'");
    expect(csp).toContain("connect-src 'self' http://localhost:8080 ws: wss:");
    expect(csp).not.toContain("upgrade-insecure-requests");
  });

  it("ignora API URLs inválidas o con esquemas no HTTP", () => {
    const csp = buildContentSecurityPolicy({
      apiUrl: "javascript:alert(1)",
      isProduction: true,
    });

    expect(csp).toContain("connect-src 'self'");
    expect(csp).not.toContain("javascript:");
  });

  it("incluye HSTS y cabeceras estándar en producción", () => {
    const headers = buildSecurityHeaders({
      apiUrl: "https://api.ynara.app",
      isProduction: true,
    });
    const byKey = Object.fromEntries(headers.map((h) => [h.key, h.value]));

    expect(byKey["X-Frame-Options"]).toBe("DENY");
    expect(byKey["X-Content-Type-Options"]).toBe("nosniff");
    expect(byKey["Referrer-Policy"]).toBe("strict-origin-when-cross-origin");
    expect(byKey["Permissions-Policy"]).toContain("camera=()");
    expect(byKey["Strict-Transport-Security"]).toBe(
      "max-age=63072000; includeSubDomains; preload",
    );
  });

  it("omite HSTS en desarrollo para no bloquear http://localhost", () => {
    const headers = buildSecurityHeaders({
      apiUrl: "http://localhost:8080",
      isProduction: false,
    });

    expect(headers.some((h) => h.key === "Strict-Transport-Security")).toBe(false);
  });
});
