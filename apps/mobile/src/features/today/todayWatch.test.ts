import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type AppStateLike, startTodayWatch } from "./todayWatch";

// Fechas con el constructor local y aserciones sobre getDate(): no depende de
// la TZ del runner. Jueves 7 de mayo de 2026.
const at = (d: number, h: number, min = 0, s = 0) => new Date(2026, 4, d, h, min, s);

function fakeAppState() {
  const listeners = new Set<(state: string) => void>();
  const appState: AppStateLike = {
    addEventListener: (_type, listener) => {
      listeners.add(listener);
      return { remove: () => listeners.delete(listener) };
    },
  };
  const emit = (state: string) => {
    for (const listener of listeners) listener(state);
  };
  return { appState, emit, listeners };
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("startTodayWatch", () => {
  it("refresca al cruzar la medianoche", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const { appState } = fakeAppState();
    const days: number[] = [];
    const stop = startTodayWatch((now) => days.push(now.getDate()), appState);

    vi.advanceTimersByTime(30_000);
    expect(days).toEqual([8]);
    stop();
  });

  it("al volver a primer plano refresca con la hora real aunque el timer no haya corrido", () => {
    vi.setSystemTime(at(7, 22));
    const { appState, emit } = fakeAppState();
    const days: number[] = [];
    const stop = startTodayWatch((now) => days.push(now.getDate()), appState);

    // App en segundo plano toda la noche: el timer no corrió.
    emit("background");
    vi.setSystemTime(at(8, 9));
    expect(days).toEqual([]);

    emit("active");
    expect(days).toEqual([8]);
    stop();
  });

  it("ignora los estados que no son active", () => {
    vi.setSystemTime(at(7, 10));
    const { appState, emit } = fakeAppState();
    const refresh = vi.fn();
    const stop = startTodayWatch(refresh, appState);

    emit("background");
    emit("inactive");
    expect(refresh).not.toHaveBeenCalled();
    stop();
  });

  it("al cortar no deja timers ni listeners", () => {
    vi.setSystemTime(at(7, 10));
    const { appState, listeners } = fakeAppState();
    const stop = startTodayWatch(vi.fn(), appState);
    expect(vi.getTimerCount()).toBe(1);
    expect(listeners.size).toBe(1);

    stop();
    expect(vi.getTimerCount()).toBe(0);
    expect(listeners.size).toBe(0);
  });
});
