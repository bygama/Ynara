import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { greet } from "./format";
import { useToday } from "./useToday";

// Fechas con el constructor local y aserciones sobre getDate(): no depende de
// la TZ del runner. Jueves 7 de mayo de 2026.
const at = (d: number, h: number, min = 0, s = 0) => new Date(2026, 4, d, h, min, s);

let visibility: DocumentVisibilityState = "visible";

function setVisibility(state: DocumentVisibilityState) {
  visibility = state;
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  visibility = "visible";
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => visibility,
  });
});

afterEach(() => {
  vi.useRealTimers();
  // Vuelve al getter de jsdom (vive en el prototipo).
  Reflect.deleteProperty(document, "visibilityState");
});

describe("useToday", () => {
  it("arranca en la hora actual y no cambia dentro del mismo día", () => {
    vi.setSystemTime(at(7, 10));
    const { result } = renderHook(() => useToday());
    const first = result.current;
    expect(first.getDate()).toBe(7);

    act(() => {
      vi.advanceTimersByTime(60 * 60 * 1000);
    });
    setVisibility("hidden");
    setVisibility("visible");
    expect(result.current).toBe(first);
  });

  it("avanza al día siguiente al cruzar la medianoche, y otra vez a la siguiente", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const { result } = renderHook(() => useToday());

    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(result.current.getDate()).toBe(8);

    act(() => {
      vi.advanceTimersByTime(24 * 60 * 60 * 1000);
    });
    expect(result.current.getDate()).toBe(9);
  });

  it("refresca el saludo a las 12 y a las 20 sin esperar a la medianoche", () => {
    vi.setSystemTime(at(7, 11, 59, 30));
    const { result } = renderHook(() => useToday());
    expect(greet(result.current)).toBe("Buen día");

    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(greet(result.current)).toBe("Buenas tardes");

    act(() => {
      vi.advanceTimersByTime(8 * 60 * 60 * 1000);
    });
    expect(greet(result.current)).toBe("Buenas noches");
  });

  it("al volver la pestaña visible toma el día nuevo aunque el timer no haya corrido", () => {
    vi.setSystemTime(at(7, 22));
    const { result } = renderHook(() => useToday());

    // Pestaña en segundo plano toda la noche: el browser frena los timers.
    setVisibility("hidden");
    vi.setSystemTime(at(8, 9));
    expect(result.current.getDate()).toBe(7);

    setVisibility("visible");
    expect(result.current.getDate()).toBe(8);
  });

  it("ignora el paso a oculta", () => {
    vi.setSystemTime(at(7, 22));
    const { result } = renderHook(() => useToday());

    vi.setSystemTime(at(8, 9));
    setVisibility("hidden");
    expect(result.current.getDate()).toBe(7);
  });

  it("al desmontar no deja timers ni listeners", () => {
    vi.setSystemTime(at(7, 10));
    const removeSpy = vi.spyOn(document, "removeEventListener");
    const { unmount } = renderHook(() => useToday());
    expect(vi.getTimerCount()).toBe(1);

    unmount();
    expect(vi.getTimerCount()).toBe(0);
    expect(removeSpy).toHaveBeenCalledWith("visibilitychange", expect.any(Function));
  });
});
