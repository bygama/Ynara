import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, configureApi } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api client — refresh single-flight y 401", () => {
  afterEach(() => {
    configureApi({
      baseUrl: "http://localhost:8080",
      getToken: () => null,
      refreshSession: undefined,
      onUnauthorized: undefined,
      fetchImpl: (input, init) => fetch(input, init),
    });
  });

  it("deduplica refresh concurrente ante múltiples 401 y reintenta con el token nuevo", async () => {
    let currentToken = "expired-tok";
    const refreshSession = vi.fn(async () => {
      currentToken = "fresh-tok";
      return currentToken;
    });
    const onUnauthorized = vi.fn();
    const fetchImpl = vi.fn(async (_url: string, init?: RequestInit) => {
      const auth = new Headers(init?.headers).get("Authorization");
      if (auth === "Bearer fresh-tok") {
        return jsonResponse(200, { ok: true });
      }
      return jsonResponse(401, { detail: "credenciales invalidas" });
    });

    configureApi({
      baseUrl: "http://api.test",
      getToken: () => currentToken,
      refreshSession,
      onUnauthorized,
      fetchImpl,
    });

    const [a, b] = await Promise.all([
      api.get<{ ok: boolean }>("/v1/a"),
      api.get<{ ok: boolean }>("/v1/b"),
    ]);

    expect(a).toEqual({ ok: true });
    expect(b).toEqual({ ok: true });
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).not.toHaveBeenCalled();
  });

  it("dispara onUnauthorized cuando falla el refresh o el reintento sigue dando 401", async () => {
    const refreshSession = vi.fn(async () => null);
    const onUnauthorized = vi.fn();
    const fetchImpl = vi.fn(async () => jsonResponse(401, { detail: "credenciales invalidas" }));

    configureApi({
      baseUrl: "http://api.test",
      getToken: () => "expired-tok",
      refreshSession,
      onUnauthorized,
      fetchImpl,
    });

    await expect(api.get("/v1/tasks")).rejects.toBeInstanceOf(ApiError);
    expect(refreshSession).toHaveBeenCalledTimes(1);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("omite refresh y onUnauthorized en skipAuth o con Authorization explícito", async () => {
    const refreshSession = vi.fn(async () => "new-tok");
    const onUnauthorized = vi.fn();
    const fetchImpl = vi.fn(async () => jsonResponse(401, { detail: "credenciales invalidas" }));

    configureApi({
      baseUrl: "http://api.test",
      getToken: () => "tok",
      refreshSession,
      onUnauthorized,
      fetchImpl,
    });

    await expect(api.post("/v1/auth/token", {}, { skipAuth: true })).rejects.toBeInstanceOf(
      ApiError,
    );
    await expect(
      api.post("/v1/auth/logout", {}, { headers: { Authorization: "Bearer manual" } }),
    ).rejects.toBeInstanceOf(ApiError);

    expect(refreshSession).not.toHaveBeenCalled();
    expect(onUnauthorized).not.toHaveBeenCalled();
  });
});
