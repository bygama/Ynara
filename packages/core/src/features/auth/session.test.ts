import { describe, expect, it, vi } from "vitest";
import { clearSession } from "./session";

describe("clearSession", () => {
  it("captura el token antes de limpiar stores, revoca en remoto y limpia la caché", async () => {
    let storedToken: string | null = "tok-123";
    const order: string[] = [];
    const revokeFn = vi.fn(async (token: string) => {
      order.push(`revoke:${token}`);
    });
    const resetStores = vi.fn(() => {
      storedToken = null;
      order.push("resetStores");
    });
    const clearQueryCache = vi.fn(() => {
      order.push("clearQueryCache");
    });

    clearSession({
      getToken: () => storedToken,
      resetStores,
      clearQueryCache,
      revokeFn,
    });

    expect(revokeFn).toHaveBeenCalledWith("tok-123");
    expect(resetStores).toHaveBeenCalledOnce();
    expect(clearQueryCache).toHaveBeenCalledOnce();
    expect(storedToken).toBeNull();
    expect(order).toEqual(["revoke:tok-123", "resetStores", "clearQueryCache"]);
  });

  it("completa la limpieza local aunque la revocación remota falle", async () => {
    const revokeFn = vi.fn().mockRejectedValue(new Error("offline"));
    const resetStores = vi.fn();
    const clearQueryCache = vi.fn();

    clearSession({
      getToken: () => "tok-456",
      resetStores,
      clearQueryCache,
      revokeFn,
    });

    // Esperamos un microtask para confirmar que el rechazo se atrapó sin unhandledRejection.
    await Promise.resolve();

    expect(revokeFn).toHaveBeenCalledWith("tok-456");
    expect(resetStores).toHaveBeenCalledOnce();
    expect(clearQueryCache).toHaveBeenCalledOnce();
  });

  it("omite la revocación remota si no hay token o si revokeRemote es false", () => {
    const revokeFn = vi.fn().mockResolvedValue(undefined);
    const resetStores = vi.fn();

    clearSession({
      getToken: () => null,
      resetStores,
      revokeFn,
    });
    clearSession({
      getToken: () => "tok-789",
      resetStores,
      revokeRemote: false,
      revokeFn,
    });

    expect(revokeFn).not.toHaveBeenCalled();
    expect(resetStores).toHaveBeenCalledTimes(2);
  });
});
