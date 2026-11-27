import { pickToday } from "@ynara/core/features/today";
import { useFocusEffect } from "expo-router";
import { useCallback, useEffect, useState } from "react";
import { AppState } from "react-native";
import { startTodayWatch } from "./todayWatch";

/**
 * `now` de la pantalla Hoy: un `Date` que se mantiene fijo dentro del día (sin
 * drift en el header ni en el día de cada prioridad) y avanza cuando cambia el
 * día local. Las tabs quedan montadas, así que Hoy puede seguir viva días
 * enteros: lo refrescan la medianoche (`watchLocalDay`), la vuelta de la app a
 * primer plano (`AppState`, cubre el timer frenado en segundo plano) y el foco
 * de la tab.
 *
 * Mientras el día no cambia devuelve la misma referencia, así que un chequeo
 * no re-renderiza la pantalla. La lógica de fechas y timers vive en
 * `@ynara/core` y la conexión con `AppState` en `startTodayWatch`, las dos con
 * tests; este hook solo las monta con React.
 */
export function useToday(): Date {
  const [today, setToday] = useState(() => new Date());

  const refresh = useCallback((now: Date) => setToday((prev) => pickToday(prev, now)), []);

  useEffect(() => startTodayWatch(refresh, AppState), [refresh]);

  useFocusEffect(
    useCallback(() => {
      refresh(new Date());
    }, [refresh]),
  );

  return today;
}
