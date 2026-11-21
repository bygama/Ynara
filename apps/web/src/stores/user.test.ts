import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useUserStore } from "./user";

describe("useUserStore (web)", () => {
  beforeEach(() => {
    useUserStore.getState().reset();
    sessionStorage.clear();
    localStorage.clear();
  });

  afterEach(() => {
    useUserStore.getState().reset();
    sessionStorage.clear();
    localStorage.clear();
  });

  it("persiste la sesión en sessionStorage y no escribe en localStorage", () => {
    useUserStore.getState().setAuth({ userId: "user-1", token: "tok-test-123" });

    const inSession = sessionStorage.getItem("ynara.user");
    expect(inSession).toContain("tok-test-123");
    expect(localStorage.getItem("ynara.user")).toBeNull();
  });
});
