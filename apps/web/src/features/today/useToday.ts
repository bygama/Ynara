"use client";

import { useLocalDay } from "@/hooks/useLocalDay";
import { GREETING_HOURS } from "./format";

/**
 * `now` de la vista Hoy: el de `useLocalDay`, que además avanza en las horas
 * del saludo (`GREETING_HOURS`), así «Buen día» pasa a «Buenas tardes» con la
 * vista abierta.
 */
export function useToday(): Date {
  return useLocalDay(GREETING_HOURS);
}
