import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { applyAuthHeader } from "@/lib/api";
import { sessionClientStorage } from "@/lib/clientStorage";
import { ADMIN_SESSION_TTL_MS, useAdminStore } from "@/stores/admin";

describe("useAdminStore", () => {
  beforeEach(() => {
    useAdminStore.getState().reset();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
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

  it("considera expirada una sesión con token pero sin expiresAt", () => {
    useAdminStore.setState({ token: "admin-tok-legacy", expiresAt: null });

    expect(useAdminStore.getState().isSessionExpired()).toBe(true);
  });

  it("getToken en api.ts resetea el store y omite Authorization si la sesión expiró", () => {
    useAdminStore.setState({
      adminId: "admin-1",
      token: "admin-tok-expired",
      expiresAt: Date.now() - 1000,
    });

    const headers = new Headers();
    applyAuthHeader(headers, "http://localhost:8080/v1/admin/overview");

    expect(headers.get("Authorization")).toBeNull();
    expect(useAdminStore.getState().token).toBeNull();
  });

  it("sessionClientStorage no rompe si sessionStorage lanza error", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("blocked");
    });

    expect(sessionClientStorage.getItem("ynara.admin")).toBeNull();
    expect(() => sessionClientStorage.setItem("ynara.admin", "x")).not.toThrow();
    expect(() => sessionClientStorage.removeItem("ynara.admin")).not.toThrow();
  });
});
