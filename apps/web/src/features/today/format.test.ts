import type { Task } from "@ynara/shared-schemas";
import { describe, expect, it } from "vitest";
import { formatClock, formatHoyDate, formatTaskDay, formatTaskMeta } from "./format";

// Construimos las fechas en hora local (new Date(y,m,d,h,m)) para que el
// round-trip ISO → formatClock devuelva la misma hora sin importar la TZ del
// runner.
const at = (h: number, m: number) => new Date(2026, 4, 7, h, m).toISOString();
// Jueves 7 de mayo de 2026, a media mañana: el día de las tareas de `at`.
// Los casos de `formatTaskDay` son espejo de los de mobile: los dos tienen que dar el mismo texto.
const NOW = new Date(2026, 4, 7, 10, 0);
const onDay = (d: number, h: number, m: number) => new Date(2026, 4, d, h, m).toISOString();

const baseTask: Task = {
  id: "0193c001-0000-4000-8000-000000000002",
  title: "Llamada con equipo de diseño",
  status: "pending",
  scheduled_at: at(14, 0),
  duration_min: 45,
};

describe("formatHoyDate", () => {
  it("capitaliza la primera letra (Intl la devuelve en minúscula)", () => {
    const out = formatHoyDate(new Date(2026, 4, 7, 10, 0));
    expect(out[0]).toBe(out[0]?.toUpperCase());
    expect(out).toContain("7");
    expect(out).toContain("mayo");
  });
});

describe("formatClock", () => {
  it("formatea 24h como HH:MM", () => {
    expect(formatClock(new Date(2026, 4, 7, 15, 30))).toBe("15:30");
    expect(formatClock(new Date(2026, 4, 7, 9, 5))).toBe("09:05");
  });
});

describe("formatTaskMeta", () => {
  it("pendiente con horario + duración → 'HH:MM · N min'", () => {
    expect(formatTaskMeta(baseTask, NOW)).toBe("14:00 · 45 min");
  });

  it("pendiente sólo con horario", () => {
    expect(formatTaskMeta({ ...baseTask, duration_min: null }, NOW)).toBe("14:00");
  });

  it("pendiente sólo con duración", () => {
    expect(formatTaskMeta({ ...baseTask, scheduled_at: null }, NOW)).toBe("45 min");
  });

  it("pendiente sin horario ni duración → vacío", () => {
    expect(formatTaskMeta({ ...baseTask, scheduled_at: null, duration_min: null }, NOW)).toBe("");
  });

  it("hecha con horario → 'HH:MM · completada'", () => {
    expect(formatTaskMeta({ ...baseTask, status: "done", scheduled_at: at(9, 15) }, NOW)).toBe(
      "09:15 · completada",
    );
  });

  it("hecha sin horario → 'completada'", () => {
    expect(formatTaskMeta({ ...baseTask, status: "done", scheduled_at: null }, NOW)).toBe(
      "completada",
    );
  });
});

describe("formatTaskDay", () => {
  it("el mismo día → null; el siguiente y el anterior por nombre", () => {
    expect(formatTaskDay(new Date(2026, 4, 7, 23, 59), NOW)).toBeNull();
    expect(formatTaskDay(new Date(2026, 4, 8, 0, 0), NOW)).toBe("Mañana");
    expect(formatTaskDay(new Date(2026, 4, 6, 23, 0), NOW)).toBe("Ayer");
  });

  it("otro día del mes → día de la semana y número; otro mes suma el mes", () => {
    expect(formatTaskDay(new Date(2026, 4, 9, 9, 0), NOW)).toBe("Sábado 9");
    expect(formatTaskDay(new Date(2026, 5, 2, 9, 0), NOW)).toBe("Martes 2 de junio");
  });

  it("cruza el año: el 1/1 desde el 31/12 es mañana; otro año suma el año", () => {
    expect(formatTaskDay(new Date(2027, 0, 1, 9, 0), new Date(2026, 11, 31, 22, 0))).toBe("Mañana");
    expect(formatTaskDay(new Date(2027, 11, 31, 9, 0), NOW)).toBe(
      "Viernes 31 de diciembre de 2027",
    );
  });
});

describe("formatTaskMeta en otro día", () => {
  it("antepone el día al horario", () => {
    expect(formatTaskMeta({ ...baseTask, scheduled_at: onDay(8, 9, 0) }, NOW)).toBe(
      "Mañana · 09:00 · 45 min",
    );
    expect(
      formatTaskMeta({ ...baseTask, scheduled_at: onDay(9, 9, 0), duration_min: null }, NOW),
    ).toBe("Sábado 9 · 09:00");
  });

  it("una hecha de ayer dice el día", () => {
    expect(
      formatTaskMeta({ ...baseTask, status: "done", scheduled_at: onDay(6, 9, 15) }, NOW),
    ).toBe("Ayer · 09:15 · completada");
  });
});
