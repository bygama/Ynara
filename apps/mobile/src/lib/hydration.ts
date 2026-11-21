export type AppReadiness = {
  fontsLoaded: boolean;
  fontError: Error | null;
  userHydrated: boolean;
};

/**
 * Indica si el layout raíz puede ocultar el splash y montar la navegación.
 * Espera a que terminen las fuentes (cargadas o con error) y a que el store
 * de usuario termine de hidratar desde `SecureStore` para que los guards de
 * ruta evalúen el estado real de sesión.
 */
export function isAppReady({ fontsLoaded, fontError, userHydrated }: AppReadiness): boolean {
  return (fontsLoaded || Boolean(fontError)) && userHydrated;
}
