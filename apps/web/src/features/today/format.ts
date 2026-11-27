import type { Task } from "@ynara/shared-schemas";

/**
 * Formateo del dashboard Hoy (build-plan Fase E). Todo recibe `now`/la fecha por
 * parámetro para ser testeable sin mockear el reloj (igual que `lib/time.ts`).
 * Locale fijo `es-AR` para que las fechas salgan en español rioplatense con
 * mayúscula inicial editorial ("Martes, 7 de mayo").
 */

const LOCALE = "es-AR";

const DATE_FMT = new Intl.DateTimeFormat(LOCALE, {
  weekday: "long",
  day: "numeric",
  month: "long",
});

const CLOCK_FMT = new Intl.DateTimeFormat(LOCALE, {
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** Primera letra a mayúscula (Intl devuelve "martes, 7 de mayo"). */
function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Fecha larga del header: "Martes, 7 de mayo". */
export function formatHoyDate(now: Date): string {
  return capitalize(DATE_FMT.format(now));
}

/**
 * Saludo según la hora local (wireframe 06 / mockup): "Buen día" hasta el
 * mediodía, "Buenas tardes" hasta las 20h, "Buenas noches" después. El nombre
 * lo agrega el header (`${greet(now)}, ${nombre}.`).
 */
export function greet(now: Date): string {
  const h = now.getHours();
  if (h < 12) return "Buen día";
  if (h < 20) return "Buenas tardes";
  return "Buenas noches";
}

/** Reloj corto "15:30" (variante Hoy vacío, wireframe 07). */
export function formatClock(now: Date): string {
  return CLOCK_FMT.format(now);
}

const WEEKDAY_FMT = new Intl.DateTimeFormat(LOCALE, { weekday: "long" });
const MONTH_FMT = new Intl.DateTimeFormat(LOCALE, { month: "long" });
const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** "Mañana", "Ayer", "Viernes 27" o "Viernes 4 de diciembre"; `null` si es el día de `now`. */
export function formatTaskDay(date: Date, now: Date): string | null {
  // `round`: un día con cambio de horario no mide 24 h exactas.
  const diff = Math.round((startOfDay(date) - startOfDay(now)) / DAY_MS);
  if (diff === 0) return null;
  if (diff === 1) return "Mañana";
  if (diff === -1) return "Ayer";
  const day = `${capitalize(WEEKDAY_FMT.format(date))} ${date.getDate()}`;
  const sameMonth = date.getMonth() === now.getMonth() && date.getFullYear() === now.getFullYear();
  return sameMonth ? day : `${day} de ${MONTH_FMT.format(date)}`;
}

/**
 * Meta de una prioridad bajo el título:
 *  - hecha → "09:15 · completada" (o sólo "completada" si no tiene horario).
 *  - pendiente → "14:00 · 45 min" (horario y/o duración, lo que haya).
 *  - con horario en otro día → el día adelante: "Mañana · 09:00", "Viernes 27 ·
 *    09:00". Hoy lista todas las tareas, no solo las de hoy, y sin el día una
 *    tarea del viernes parecía de hoy.
 *
 * Devuelve `""` si no hay nada que mostrar (tarea pendiente sin horario ni
 * duración): el caller decide no renderizar la meta.
 */
export function formatTaskMeta(task: Task, now: Date): string {
  const at = task.scheduled_at ? new Date(task.scheduled_at) : null;
  const when = at ? [formatTaskDay(at, now), CLOCK_FMT.format(at)] : [];
  if (task.status === "done") {
    return [...when, "completada"].filter(Boolean).join(" · ");
  }
  const duration = task.duration_min !== null ? `${task.duration_min} min` : null;
  return [...when, duration].filter(Boolean).join(" · ");
}
