"use client";

import { pickToday, watchLocalDay } from "@ynara/core/features/today";
import { useEffect, useState } from "react";

/** Sin cortes dentro del día. Constante de módulo: un `[]` por render rearmaría el reloj. */
const NO_CUTS: readonly number[] = [];

/**
 * `now` de una vista que muestra «hoy»: un `Date` que se mantiene fijo dentro
 * de cada tramo del día (sin drift) y avanza cuando cambia el día local o se
 * cruza una de las `cutHours`. Lo refrescan esos cortes (`watchLocalDay`) y la
 * vuelta de la pestaña a visible, que cubre el timer que el browser frenó en
 * segundo plano.
 *
 * Mientras no se cruza un corte devuelve la misma referencia, así que un
 * chequeo no re-renderiza la vista. Al montar chequea una vez: si entre el
 * render y el efecto se cruzó un corte, el estado lo toma en el momento.
 * `cutHours` tiene que ser estable (una constante): si cambia, el reloj se rearma.
 */
export function useLocalDay(cutHours: readonly number[] = NO_CUTS): Date {
  const [today, setToday] = useState(() => new Date());

  useEffect(() => {
    const watcher = watchLocalDay(
      (now) => setToday((prev) => pickToday(prev, now, cutHours)),
      undefined,
      cutHours,
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
  }, [cutHours]);

  return today;
}
