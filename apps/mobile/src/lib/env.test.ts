import { describe, expect, it } from "vitest";
import { resolveMocksEnabled } from "./env";

describe("resolveMocksEnabled", () => {
  it("habilita mocks solo cuando está en dev y el flag está activo", () => {
    expect(resolveMocksEnabled(true, true)).toBe(true);
  });

  it("deshabilita mocks en release aunque el flag esté en true", () => {
    expect(resolveMocksEnabled(false, true)).toBe(false);
  });

  it("deshabilita mocks cuando el flag está en false", () => {
    expect(resolveMocksEnabled(true, false)).toBe(false);
    expect(resolveMocksEnabled(false, false)).toBe(false);
  });
});
