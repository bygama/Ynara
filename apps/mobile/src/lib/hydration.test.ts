import { describe, expect, it } from "vitest";
import { isAppReady } from "./hydration";

describe("isAppReady", () => {
  it("espera mientras el store de usuario no hidrató aunque las fuentes ya cargaron", () => {
    expect(isAppReady({ fontsLoaded: true, fontError: null, userHydrated: false })).toBe(false);
    expect(
      isAppReady({ fontsLoaded: false, fontError: new Error("font"), userHydrated: false }),
    ).toBe(false);
  });

  it("espera mientras las fuentes no terminaron aunque el store ya hidrató", () => {
    expect(isAppReady({ fontsLoaded: false, fontError: null, userHydrated: true })).toBe(false);
  });

  it("habilita el montaje cuando fuentes y store están listos", () => {
    expect(isAppReady({ fontsLoaded: true, fontError: null, userHydrated: true })).toBe(true);
    expect(
      isAppReady({ fontsLoaded: false, fontError: new Error("font"), userHydrated: true }),
    ).toBe(true);
  });
});
