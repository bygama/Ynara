import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ADMIN_SESSION_TTL_MS, useAdminStore } from "@/stores/admin";

describe("useAdminStore", () => {
  beforeEach(() => {
    useAdminStore.getState().reset();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    useAdminStore.getState().reset();
    sessionStorage.clear();
    localStorage.clear();
  });

  it("persiste la sesión admin en sessionStorage y no en localStorage", () => {
    useAdminStore.getState().setAuth({
      adminId: "admin-1",
      token: "admin-tok-1",
      displayName: "Operador",
    });

    expect(sessionStorage.getItem("ynara.admin")).toContain("admin-tok-1");
    expect(localStorage.getItem("ynara.admin")).toBeNull();
  });

  it("marca la sesión como expirada pasado ADMIN_SESSION_TTL_MS", () => {
    const start = Date.now();
    useAdminStore.getState().setAuth({ adminId: "admin-1", token: "admin-tok-2" });

    expect(useAdminStore.getState().isSessionExpired(start + 1000)).toBe(false);
    expect(useAdminStore.getState().isSessionExpired(start + ADMIN_SESSION_TTL_MS + 1000)).toBe(
      true,
    );
  });
});
