import { logOut } from "./api";

export type ClearSessionOptions = {
  /** Obtiene el access token actual antes de limpiar los stores. */
  getToken: () => string | null | undefined;
  /** Resetea todos los stores con datos del usuario. */
  resetStores: () => void;
  /** Limpia la caché de consultas del cliente (p. ej. `queryClient.clear()`). */
  clearQueryCache?: () => void;
  /**
   * Si es `true` (default), revoca la sesión en el servidor de forma
   * best-effort con el token capturado antes de limpiar el estado local.
   * Pasar `false` cuando el servidor ya rechazó la sesión (p. ej. 401).
   */
  revokeRemote?: boolean;
  /** Inyectable para tests; por defecto usa `logOut`. */
  revokeFn?: (token: string) => Promise<void>;
};

/**
 * Limpia el estado local de la sesión del usuario y, de forma opcional,
 * solicita el cierre remoto best-effort sin bloquear la salida local.
 *
 * Captura el token antes de ejecutar `resetStores` para poder enviarlo al
 * servidor incluso si el store se vacía en el mismo tick.
 */
export function clearSession(options: ClearSessionOptions): void {
  const {
    getToken,
    resetStores,
    clearQueryCache,
    revokeRemote = true,
    revokeFn = logOut,
  } = options;

  const token = getToken();
  if (revokeRemote && token) {
    void Promise.resolve(revokeFn(token)).catch(() => {});
  }

  resetStores();
  clearQueryCache?.();
}
