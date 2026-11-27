import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  isSameLocalDay,
  MAX_DAY_CHECK_MS,
  msUntilNextLocalCut,
  msUntilNextLocalMidnight,
  pickToday,
  watchLocalDay,
} from "./dayClock";

// Fechas armadas con el constructor local (new Date(y, m, d, h, min)) y
// aserciones sobre getDate()/getHours(): el resultado no depende de la TZ del
// runner. Jueves 7 de mayo de 2026, sin cambio de horario cerca en ninguna TZ.
const at = (d: number, h: number, min = 0, s = 0) => new Date(2026, 4, d, h, min, s);

// Zonas con cambio de horario. CI corre en UTC, que no tiene: sin fijar la
// zona, sumar 24 h fijas a la medianoche pasaría los tests igual. Node toma
// `process.env.TZ` en caliente.
const DST_ZONES = ["America/Santiago", "America/New_York", "Europe/London", "Pacific/Auckland"];

function withTimeZone(tz: string, fn: () => void) {
  const prev = process.env.TZ;
  const runnerZone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  process.env.TZ = tz;
  try {
    // Si la zona no se aplicó, el test no probaría nada: que falle.
    expect(new Date(2026, 0, 1).getTimezoneOffset()).not.toBe(
      new Date(2026, 6, 1).getTimezoneOffset(),
    );
    fn();
  } finally {
    process.env.TZ = prev ?? runnerZone;
  }
}

// Recorre 2026 al mediodía: el próximo corte de cada día tiene que caer en el
// primer instante del día siguiente (o de la hora de corte), aunque ese día
// dure 23 o 25 h. No se asume que el día arranque a las 00:00: en Chile el
// cambio es a medianoche y ese día empieza a la 01:00.
function expectMidnightAllYear() {
  for (let day = 0; day < 366; day++) {
    const noon = new Date(2026, 0, 1 + day, 12, 0);
    const next = new Date(noon.getTime() + msUntilNextLocalMidnight(noon));
    const tomorrow = new Date(2026, 0, 2 + day, 12, 0);
    expect(isSameLocalDay(next, tomorrow)).toBe(true);
    expect(isSameLocalDay(new Date(next.getTime() - 1), noon)).toBe(true);
  }
}

function expectCutsAllYear() {
  for (let day = 0; day < 366; day++) {
    const morning = new Date(2026, 0, 1 + day, 11, 0);
    const cut = new Date(morning.getTime() + msUntilNextLocalCut(morning, [12, 20]));
    expect(isSameLocalDay(cut, morning)).toBe(true);
    expect(cut.getHours()).toBe(12);
    expect(cut.getMinutes()).toBe(0);
  }
}

describe("isSameLocalDay / pickToday", () => {
  it("mismo día de punta a punta; la medianoche ya es otro", () => {
    expect(isSameLocalDay(at(7, 0, 0), at(7, 23, 59, 59))).toBe(true);
    expect(isSameLocalDay(at(7, 23, 59, 59), at(8, 0, 0))).toBe(false);
    // Mismo número de día en otro mes u otro año no es el mismo día.
    expect(isSameLocalDay(at(7, 10), new Date(2026, 5, 7, 10))).toBe(false);
    expect(isSameLocalDay(at(7, 10), new Date(2027, 4, 7, 10))).toBe(false);
  });

  it("pickToday devuelve la misma referencia en el mismo día y la nueva si cambió", () => {
    const prev = at(7, 9);
    expect(pickToday(prev, at(7, 22))).toBe(prev);
    const fresh = at(8, 0, 0, 1);
    expect(pickToday(prev, fresh)).toBe(fresh);
  });

  it("con horas de corte, cambia al cruzar un corte y no dentro del tramo", () => {
    const cuts = [12, 20];
    const morning = at(7, 9);
    expect(pickToday(morning, at(7, 11, 59, 59), cuts)).toBe(morning);
    const noon = at(7, 12);
    expect(pickToday(morning, noon, cuts)).toBe(noon);
    expect(pickToday(noon, at(7, 19, 59, 59), cuts)).toBe(noon);
    const evening = at(7, 20);
    expect(pickToday(noon, evening, cuts)).toBe(evening);
    // Mismo tramo pero otro día: cambia igual.
    const nextMorning = at(8, 9);
    expect(pickToday(morning, nextMorning, cuts)).toBe(nextMorning);
  });
});

describe("msUntilNextLocalMidnight", () => {
  it("cuenta lo que falta para las 00:00 del día siguiente", () => {
    expect(msUntilNextLocalMidnight(at(7, 23, 59, 30))).toBe(30_000);
    expect(msUntilNextLocalMidnight(at(7, 23, 0))).toBe(60 * 60 * 1000);
  });

  it("cae en el primer instante del día siguiente todos los días del año", () => {
    expectMidnightAllYear();
  });

  it.each(DST_ZONES)("también con cambio de horario (%s)", (tz) => {
    withTimeZone(tz, expectMidnightAllYear);
  });
});

describe("msUntilNextLocalCut", () => {
  it("sin horas de corte es la medianoche", () => {
    expect(msUntilNextLocalCut(at(7, 10))).toBe(msUntilNextLocalMidnight(at(7, 10)));
  });

  it("toma el corte más cercano que todavía no pasó", () => {
    const cuts = [12, 20];
    expect(msUntilNextLocalCut(at(7, 11, 59, 30), cuts)).toBe(30_000);
    expect(msUntilNextLocalCut(at(7, 12), cuts)).toBe(8 * 60 * 60 * 1000);
    expect(msUntilNextLocalCut(at(7, 23, 59, 30), cuts)).toBe(30_000);
  });

  it.each(DST_ZONES)("cae a la hora del corte todo el año (%s)", (tz) => {
    withTimeZone(tz, expectCutsAllYear);
  });
});

describe("watchLocalDay", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("avisa justo al cruzar la medianoche, no antes", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const onTick = vi.fn();
    const watcher = watchLocalDay(onTick);

    vi.advanceTimersByTime(29_999);
    expect(onTick).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(onTick).toHaveBeenCalledTimes(1);
    const now = onTick.mock.calls[0]?.[0] as Date;
    expect(now.getDate()).toBe(8);
    expect(now.getHours()).toBe(0);
    watcher.stop();
  });

  it("se reprograma solo: también cruza la medianoche siguiente", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const days: number[] = [];
    const watcher = watchLocalDay((now) => days.push(now.getDate()));

    vi.advanceTimersByTime(30_000 + 24 * 60 * 60 * 1000);
    expect(days[0]).toBe(8);
    expect(days.at(-1)).toBe(9);
    watcher.stop();
  });

  it("lejos de la medianoche chequea cada MAX_DAY_CHECK_MS", () => {
    vi.setSystemTime(at(7, 10));
    const onTick = vi.fn();
    const watcher = watchLocalDay(onTick);

    vi.advanceTimersByTime(MAX_DAY_CHECK_MS - 1);
    expect(onTick).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onTick).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(MAX_DAY_CHECK_MS);
    expect(onTick).toHaveBeenCalledTimes(2);
    watcher.stop();
  });

  it("si el timer dispara antes de la medianoche, reprograma lo que falta", () => {
    // El timer se programa con el reloj en hora y después el reloj se atrasa
    // 5 ms: cuando el timer llega a la medianoche, `getNow` todavía dice
    // 23:59:59.995.
    vi.setSystemTime(at(7, 23, 59, 30));
    let lagMs = 0;
    const days: number[] = [];
    const watcher = watchLocalDay(
      (now) => days.push(now.getDate()),
      () => new Date(Date.now() - lagMs),
    );
    lagMs = 5;

    vi.advanceTimersByTime(30_000);
    expect(days).toEqual([7]);
    vi.advanceTimersByTime(5);
    expect(days).toEqual([7, 8]);
    watcher.stop();
  });

  it("lee la hora una sola vez por chequeo", () => {
    // El primer chequeo lee 23:59:59.999 y el siguiente ya es del día 8. Leer
    // la hora dos veces avisaba con el día 7 y reprogramaba desde el 8, a
    // MAX_DAY_CHECK_MS: el cambio de día llegaba 5 minutos tarde.
    vi.setSystemTime(at(7, 23, 59, 59));
    const getNow = vi
      .fn<() => Date>()
      .mockReturnValueOnce(at(7, 23, 59, 59))
      .mockReturnValueOnce(new Date(2026, 4, 7, 23, 59, 59, 999))
      .mockReturnValue(new Date(2026, 4, 8, 0, 0, 0, 1));
    const days: number[] = [];
    const watcher = watchLocalDay((now) => days.push(now.getDate()), getNow);

    vi.advanceTimersByTime(1000);
    expect(days).toEqual([7]);
    expect(getNow).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1);
    expect(days).toEqual([7, 8]);
    expect(getNow).toHaveBeenCalledTimes(3);
    watcher.stop();
  });

  it("con horas de corte, avisa justo en cada corte", () => {
    vi.setSystemTime(at(7, 11, 59, 30));
    const hours: number[] = [];
    const watcher = watchLocalDay((now) => hours.push(now.getHours()), undefined, [12]);

    vi.advanceTimersByTime(29_999);
    expect(hours).toEqual([]);
    vi.advanceTimersByTime(1);
    expect(hours).toEqual([12]);
    watcher.stop();
  });

  it("sync avisa en el momento y reprograma desde la hora real", () => {
    vi.setSystemTime(at(7, 10));
    const onTick = vi.fn();
    const watcher = watchLocalDay(onTick);

    // El reloj saltó (la máquina estuvo suspendida) sin que corrieran timers.
    vi.setSystemTime(at(8, 7));
    watcher.sync();
    expect(onTick).toHaveBeenCalledTimes(1);
    expect((onTick.mock.calls[0]?.[0] as Date).getDate()).toBe(8);
    expect(vi.getTimerCount()).toBe(1);
    watcher.stop();
  });

  it("stop cancela el timer pendiente", () => {
    vi.setSystemTime(at(7, 23, 59, 30));
    const onTick = vi.fn();
    const watcher = watchLocalDay(onTick);
    expect(vi.getTimerCount()).toBe(1);

    watcher.stop();
    expect(vi.getTimerCount()).toBe(0);
    vi.advanceTimersByTime(60_000);
    expect(onTick).not.toHaveBeenCalled();
  });
});
