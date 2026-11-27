import type { Task } from "@ynara/shared-schemas";

/**
 * Formateo del dashboard Hoy (mobile) — espejo de
 * `apps/web/src/features/today/format.ts`, pero **Hermes-safe**: sin `Intl`
 * (como `lib/relativeTime.ts`), con los nombres en español rioplatense por
 * arrays. Todo recibe la fecha por parámetro para ser testeable sin mockear el
 * reloj. La hora se muestra en zona local (igual que web).
 */

const WEEKDAYS = [
  "Domingo",
  "Lunes",
  "Martes",
  "Miércoles",
  "Jueves",
  "Viernes",
  "Sábado",
] as const;

const MONTHS = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
] as const;

/** Dos dígitos con cero a la izquierda ("9" → "09"). */
function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Fecha larga del header: "Martes, 7 de mayo". */
export function formatHoyDate(now: Date): string {
  return `${WEEKDAYS[now.getDay()]}, ${now.getDate()} de ${MONTHS[now.getMonth()]}`;
}

/** Reloj corto 24h "15:30" (hora local). */
export function formatClock(date: Date): string {
  return `${pad2(date.getHours())}:${pad2(date.getMinutes())}`;
}

const DAY_MS = 86_400_000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/**
 * "Mañana", "Ayer", "Viernes 27", "Viernes 4 de diciembre" o, de otro año,
 * "Viernes 31 de diciembre de 2027"; `null` si es el día de `now`.
 */
export function formatTaskDay(date: Date, now: Date): string | null {
  // `round`: un día con cambio de horario no mide 24 h exactas.
  const diff = Math.round((startOfDay(date) - startOfDay(now)) / DAY_MS);
  if (diff === 0) return null;
  if (diff === 1) return "Mañana";
  if (diff === -1) return "Ayer";
  const day = `${WEEKDAYS[date.getDay()]} ${date.getDate()}`;
  if (date.getFullYear() !== now.getFullYear()) {
    return `${day} de ${MONTHS[date.getMonth()]} de ${date.getFullYear()}`;
  }
  return date.getMonth() === now.getMonth() ? day : `${day} de ${MONTHS[date.getMonth()]}`;
}

/**
 * Meta de una prioridad bajo el título:
 *  - hecha → "09:15 · completada" (o sólo "completada" si no tiene horario).
 *  - pendiente → "14:00 · 45 min" (horario y/o duración, lo que haya).
 *  - con horario en otro día → el día adelante: "Mañana · 09:00", "Viernes 27 ·
 *    09:00". Hoy lista todas las tareas, no solo las de hoy, y sin el día una
 *    tarea del viernes parecía de hoy.
 *
 * Devuelve `""` si no hay nada que mostrar (pendiente sin horario ni duración):
 * el caller decide no renderizar la meta.
 */
export function formatTaskMeta(task: Task, now: Date): string {
  const at = task.scheduled_at ? new Date(task.scheduled_at) : null;
  const when = at ? [formatTaskDay(at, now), formatClock(at)] : [];
  if (task.status === "done") {
    return [...when, "completada"].filter(Boolean).join(" · ");
  }
  const duration = task.duration_min !== null ? `${task.duration_min} min` : null;
  return [...when, duration].filter(Boolean).join(" · ");
}
