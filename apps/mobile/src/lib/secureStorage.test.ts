import { beforeEach, describe, expect, it, vi } from "vitest";

const getItemAsync = vi.fn();
const setItemAsync = vi.fn();
const deleteItemAsync = vi.fn();

vi.mock("expo-secure-store", () => ({
  getItemAsync,
  setItemAsync,
  deleteItemAsync,
}));

const { secureStorage } = await import("./secureStorage");

beforeEach(() => {
  getItemAsync.mockReset();
  setItemAsync.mockReset();
  deleteItemAsync.mockReset();
});

describe("secureStorage", () => {
  it("devuelve el valor almacenado cuando SecureStore resuelve", async () => {
    getItemAsync.mockResolvedValue('{"token":"abc"}');

    await expect(secureStorage.getItem("ynara.user")).resolves.toBe('{"token":"abc"}');
    expect(getItemAsync).toHaveBeenCalledWith("ynara.user");
  });

  it("devuelve null cuando SecureStore rechaza al leer", async () => {
    getItemAsync.mockRejectedValue(new Error("keystore unavailable"));

    await expect(secureStorage.getItem("ynara.user")).resolves.toBeNull();
  });
});
