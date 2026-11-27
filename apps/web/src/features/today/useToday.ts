"use client";

import { pickToday, watchLocalDay } from "@ynara/core/features/today";
import { useEffect, useState } from "react";
import { GREETING_HOURS } from "./format";

/**
 * `now` de la vista Hoy: un `Date` que se mantiene fijo dentro de cada tramo
 * del día (sin drift en el header ni en el día de cada prioridad) y avanza
 * cuando cambia el día local o el saludo (`GREETING_HOURS`). Lo refrescan esos
 * cortes (`watchLocalDay`) y la vuelta de la pestaña a visible, que cubre el
 * timer que el browser frenó en segundo plano.
 *
 * Mientras no se cruza un corte devuelve la misma referencia, así que un
 * chequeo no re-renderiza la vista. Al montar chequea una vez: si entre el
 * render y el efecto se cruzó un corte, el estado lo toma en el momento.
 */
export function useToday(): Date {
  const [today, setToday] = useState(() => new Date());

  useEffect(() => {
    const watcher = watchLocalDay(
      (now) => setToday((prev) => pickToday(prev, now, GREETING_HOURS)),
      undefined,
      GREETING_HOURS,
    );
    watcher.sync();
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
