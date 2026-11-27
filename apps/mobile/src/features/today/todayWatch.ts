import { watchLocalDay } from "@ynara/core/features/today";

/**
 * Lo que `startTodayWatch` usa de `AppState` de react-native. Tiparlo acá deja
 * este módulo sin importar RN, así se testea en el entorno `node` de vitest.
 */
export type AppStateLike = {
  addEventListener(type: "change", listener: (state: string) => void): { remove(): void };
};

/**
 * Conecta `useToday` con el reloj: llama a `refresh` en cada medianoche local
 * (`watchLocalDay`) y cuando la app vuelve a primer plano, que cubre el timer
 * frenado en segundo plano. Devuelve la función que corta todo.
 */
export function startTodayWatch(refresh: (now: Date) => void, appState: AppStateLike): () => void {
  const watcher = watchLocalDay(refresh);
  const subscription = appState.addEventListener("change", (state) => {
    if (state === "active") watcher.sync();
  });
  return () => {
    subscription.remove();
    watcher.stop();
  };
}
