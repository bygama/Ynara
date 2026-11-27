/**
 * Reloj de día local de la pantalla Hoy. El header (`formatHoyDate`) y el día
 * de cada prioridad (`formatTaskDay`: «Mañana», «Ayer») se calculan contra un
 * `now`; si la pantalla queda abierta pasada la medianoche, ese `now` tiene que
 * avanzar al día nuevo. Dentro del mismo día no cambia, así los textos no se
 * recalculan ni se mueven sin motivo.
 *
 * Todo es hora local y sin `Intl` (Hermes en mobile). Lo de React (estado,
 * eventos de visibilidad, `AppState`, foco de la tab) vive en el `useToday` de
 * cada app; acá queda la parte pura, testeable con timers falsos.
 */

/**
 * Tope de espera entre chequeos. La medianoche se programa exacta, pero un
 * timer largo puede dormirse con la máquina suspendida o la app en segundo
 * plano y disparar tarde; con el tope, el día se corrige a los pocos minutos
 * aunque no llegue ningún evento de visibilidad. Un chequeo es comparar dos
 * fechas: no re-renderiza si el día sigue igual.
 */
export const MAX_DAY_CHECK_MS = 5 * 60 * 1000;

/** `true` si `a` y `b` caen en el mismo día del calendario local. */
export function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/**
 * Milisegundos hasta la próxima medianoche local. Se arma con el constructor
 * local (no sumando 24 h) para que los días de 23 o 25 h del cambio de horario
 * den la medianoche real.
 */
export function msUntilNextLocalMidnight(now: Date): number {
  const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
  return next.getTime() - now.getTime();
}

/**
 * El `now` que corresponde mostrar: `prev` (misma referencia) si `fresh` es del
 * mismo día, `fresh` si el día cambió. Pensado para `setState(prev => ...)`:
 * devolver la misma referencia hace que React no re-renderice.
 */
export function pickToday(prev: Date, fresh: Date): Date {
  return isSameLocalDay(prev, fresh) ? prev : fresh;
}

export interface LocalDayWatcher {
  /** Chequea ya (vuelta a primer plano, foco) y reprograma el próximo chequeo. */
  sync: () => void;
  /** Cancela el timer pendiente. */
  stop: () => void;
}

/**
 * Llama a `onTick` con la hora actual en cada medianoche local y, como mucho,
 * cada `MAX_DAY_CHECK_MS`. Quien lo usa decide con `pickToday` si el día cambió.
 *
 * El timer se reprograma dentro del propio tick: si dispara unos ms antes de la
 * medianoche, el próximo queda a esos ms y el cambio de día no se pierde.
 */
export function watchLocalDay(
  onTick: (now: Date) => void,
  getNow: () => Date = () => new Date(),
): LocalDayWatcher {
  let timer: ReturnType<typeof setTimeout> | undefined;

  const arm = () => {
    clearTimeout(timer);
    const wait = Math.min(msUntilNextLocalMidnight(getNow()), MAX_DAY_CHECK_MS);
    timer = setTimeout(sync, wait);
  };

  function sync() {
    onTick(getNow());
    arm();
  }

  arm();
  return {
    sync,
    stop: () => {
      clearTimeout(timer);
      timer = undefined;
    },
  };
}
