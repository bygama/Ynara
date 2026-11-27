"use client";

import { pickToday, watchLocalDay } from "@ynara/core/features/today";
import { useEffect, useState } from "react";

/**
 * `now` de la vista Hoy: un `Date` que se mantiene fijo dentro del día (sin
 * drift en el header ni en el día de cada prioridad) y avanza cuando cambia el
 * día local. Lo refrescan la medianoche (`watchLocalDay`) y la vuelta de la
 * pestaña a visible, que cubre el timer que el browser frenó en segundo plano.
 *
 * Mientras el día no cambia devuelve la misma referencia, así que un chequeo
 * no re-renderiza la vista.
 */
export function useToday(): Date {
  const [today, setToday] = useState(() => new Date());

  useEffect(() => {
    const watcher = watchLocalDay((now) => setToday((prev) => pickToday(prev, now)));
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") watcher.sync();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      document.removeEventListener("visibilitychange", onVisibilityChange);
      watcher.stop();
    };
  }, []);

  return today;
}
