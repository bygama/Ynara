import { afterEach, describe, expect, it, vi } from "vitest";
import TestDsPage from "./test-ds/page";
import TestMockPage from "./test-mock/page";

const { notFoundMock } = vi.hoisted(() => ({
  notFoundMock: vi.fn(() => {
    throw new Error("NEXT_NOT_FOUND");
  }),
}));

vi.mock("next/navigation", () => ({
  notFound: () => notFoundMock(),
}));

describe("rutas internas de sandbox en apps/web", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    notFoundMock.mockClear();
  });

  it("devuelven notFound() en producción", () => {
    vi.stubEnv("NODE_ENV", "production");

    expect(() => TestDsPage()).toThrow("NEXT_NOT_FOUND");
    expect(() => TestMockPage()).toThrow("NEXT_NOT_FOUND");
    expect(notFoundMock).toHaveBeenCalledTimes(2);
  });

  it("renderizan en desarrollo/test sin llamar a notFound()", () => {
    vi.stubEnv("NODE_ENV", "development");

    expect(TestDsPage()).toBeTruthy();
    expect(TestMockPage()).toBeTruthy();
    expect(notFoundMock).not.toHaveBeenCalled();
  });
});
