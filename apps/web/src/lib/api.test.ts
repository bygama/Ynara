import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUserStore } from "@/stores/user";
import { api } from "./api";

// Mock de fetch que devuelve un JSON 200 y captura los headers del request.
function mockFetch() {
  return vi.fn(async (..._args: unknown[]) => ({
    ok: true,
    status: 200,
    headers: {
      get: (k: string) => (k.toLowerCase() === "content-type" ? "application/json" : null),
    },
    json: async () => ({ ok: true }),
    text: async () => "",
  }));
}

function authHeaderOf(fetchMock: ReturnType<typeof mockFetch>): string | null {
  const init = fetchMock.mock.calls[0]?.[1] as { headers: Headers } | undefined;
  return init?.headers.get("Authorization") ?? null;
}

function jsonRes(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("api — inyección de auth (Bearer)", () => {
  let fetchMock: ReturnType<typeof mockFetch>;

  beforeEach(() => {
    useUserStore.getState().reset();
    fetchMock = mockFetch();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    useUserStore.getState().reset();
  });

  it("adjunta Authorization: Bearer cuando hay token en el store", async () => {
    useUserStore.getState().setAuth({ userId: "u1", token: "tok-123" });
    await api.get("/v1/sessions");
    expect(authHeaderOf(fetchMock)).toBe("Bearer tok-123");
  });

  it("no adjunta Authorization si no hay token", async () => {
    await api.get("/v1/health");
    expect(authHeaderOf(fetchMock)).toBeNull();
  });

  it("respeta skipAuth aunque haya token (endpoints públicos)", async () => {
    useUserStore.getState().setAuth({ userId: "u1", token: "tok-123" });
    await api.post("/v1/auth/token", { email: "a@b.com" }, { skipAuth: true });
    expect(authHeaderOf(fetchMock)).toBeNull();
  });

  it("NO manda el Bearer a un host ajeno (perímetro)", async () => {
    useUserStore.getState().setAuth({ userId: "u1", token: "tok-123" });
    await api.get("https://evil.example.com/steal");
    expect(authHeaderOf(fetchMock)).toBeNull();
  });

  it("rota el refresh token en 401, actualiza el store y reintenta el request", async () => {
    useUserStore.getState().setAuth({
      userId: "u1",
      token: "tok-viejo",
      refreshToken: "rt-1",
    });

    const rotatingFetch = vi.fn(async (url: string, init?: { headers?: Headers }) => {
      if (url.endsWith("/v1/auth/refresh")) {
        return jsonRes(200, {
          access_token: "tok-nuevo",
          token_type: "bearer",
          refresh_token: "rt-2",
        });
      }
      const auth = init?.headers?.get("Authorization");
      if (auth === "Bearer tok-nuevo") {
        return jsonRes(200, { items: [] });
      }
      return jsonRes(401, { detail: "credenciales invalidas" });
    });
    vi.stubGlobal("fetch", rotatingFetch);

    const data = await api.get<{ items: unknown[] }>("/v1/tasks");
    expect(data).toEqual({ items: [] });
    expect(useUserStore.getState().token).toBe("tok-nuevo");
    expect(useUserStore.getState().refreshToken).toBe("rt-2");
  });

  it("limpia la sesión local ante 401 sin refresh token", async () => {
    useUserStore.getState().setAuth({ userId: "u1", token: "tok-muerto" });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonRes(401, { detail: "credenciales invalidas" })),
    );

    await expect(api.get("/v1/tasks")).rejects.toThrow();
    expect(useUserStore.getState().token).toBeNull();
  });
});
