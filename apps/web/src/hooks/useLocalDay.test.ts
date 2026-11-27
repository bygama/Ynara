import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useLocalDay } from "./useLocalDay";

// Fechas con el constructor local y aserciones sobre getDate(): no depende de
// la TZ del runner. Jueves 7 de mayo de 2026. La vuelta de la pestaña a
// visible y el chequeo al montar se prueban en `useToday.test.ts`, que usa este
// hook con los cortes del saludo.
const at = (d: number, h: number, min = 0, s = 0) => new Date(2026, 4, d, h, min, s);

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useLocalDay", () => {
  it("sin cortes no cambia en todo el día, ni a las 12 ni a las 20", () => {
    vi.setSystemTime(at(7, 9));
    const { result } = renderHook(() => useLocalDay());
    const first = result.current;

    act(() => {
      vi.advanceTimersByTime(14 * 60 * 60 * 1000);
    });
    expect(result.current).toBe(first);
  });

  it("avanza al día siguiente al cruzar la medianoche", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const { result } = renderHook(() => useLocalDay());

    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    expect(result.current.getDate()).toBe(8);
  });

  it("re-renderizar no rearma el reloj", () => {
    vi.setSystemTime(at(7, 10));
    const clearSpy = vi.spyOn(globalThis, "clearTimeout");
    const { rerender } = renderHook(() => useLocalDay());
    const clearsAfterMount = clearSpy.mock.calls.length;

    rerender();
    rerender();
    expect(clearSpy.mock.calls.length).toBe(clearsAfterMount);
    expect(vi.getTimerCount()).toBe(1);
  });
});
