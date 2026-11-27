import type { Task } from "@ynara/shared-schemas";
import { describe, expect, it } from "vitest";
import { formatTaskDay, formatTaskMeta } from "./format";

// Fechas en hora local (new Date(y,m,d,h,m)) para que el round-trip ISO dé la
// misma hora sin importar la TZ del runner. NOW: jueves 7 de mayo de 2026.
const NOW = new Date(2026, 4, 7, 10, 0);
const onDay = (d: number, h: number, m: number) => new Date(2026, 4, d, h, m).toISOString();

const baseTask: Task = {
  id: "0193c001-0000-4000-8000-000000000002",
  title: "Llamada con equipo de diseño",
  status: "pending",
  scheduled_at: onDay(7, 14, 0),
  duration_min: 45,
};

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
});

describe("formatTaskMeta", () => {
  it("hoy → solo horario y duración", () => {
    expect(formatTaskMeta(baseTask, NOW)).toBe("14:00 · 45 min");
  });

  it("otro día → el día adelante", () => {
    expect(formatTaskMeta({ ...baseTask, scheduled_at: onDay(8, 9, 0) }, NOW)).toBe(
      "Mañana · 09:00 · 45 min",
    );
  });

  it("hecha de ayer → día, horario y 'completada'", () => {
    expect(
      formatTaskMeta({ ...baseTask, status: "done", scheduled_at: onDay(6, 9, 15) }, NOW),
    ).toBe("Ayer · 09:15 · completada");
  });

  it("sin horario → sin día", () => {
    expect(formatTaskMeta({ ...baseTask, scheduled_at: null }, NOW)).toBe("45 min");
    expect(formatTaskMeta({ ...baseTask, scheduled_at: null, status: "done" }, NOW)).toBe(
      "completada",
    );
  });
});
