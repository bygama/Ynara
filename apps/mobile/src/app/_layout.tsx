import { useFonts } from "expo-font";
import { Stack } from "expo-router";
import * as SplashScreen from "expo-splash-screen";
import { StatusBar } from "expo-status-bar";
import { colorScheme } from "nativewind";
import { useEffect, useSyncExternalStore } from "react";
import "../global.css";
// Side-effect: polyfill de crypto.randomUUID (RN no lo trae) — lo usa el chat
// store de @ynara/core. Va primero, antes de tocar cualquier store.
import "@/lib/polyfills";
// Side-effect: configura el cliente API (baseUrl + token) una vez al boot, antes
// de que cualquier pantalla haga un request. Ver apps/mobile/src/lib/api.ts.
import "@/lib/api";
import { FONT_MAP } from "@/lib/fonts";
import { isAppReady } from "@/lib/hydration";
import { useUserStore } from "@/stores/user";
import { Providers } from "./providers";

// Mantiene el splash hasta que las fuentes de marca y la hidratación del store
// seguro estén listas (evita tanto el flash de fuente como redirects prematuros).
SplashScreen.preventAutoHideAsync().catch(() => {});

// Tema oscuro por default (el mockup arranca oscuro). Las vars de `:root` ya son
// oscuras; esto alinea el color scheme de NativeWind/RN (StatusBar, componentes
// nativos que respetan Appearance, y los `dark:` variants si se usaran).
colorScheme.set("dark");

const subscribeUserHydration = (onChange: () => void) =>
  useUserStore.persist.onFinishHydration(onChange);
const getUserHydratedSnapshot = () => useUserStore.persist.hasHydrated();

export default function RootLayout() {
  const [fontsLoaded, fontError] = useFonts(FONT_MAP);
  const userHydrated = useSyncExternalStore(
    subscribeUserHydration,
    getUserHydratedSnapshot,
    getUserHydratedSnapshot,
  );
  const ready = isAppReady({ fontsLoaded, fontError, userHydrated });

  useEffect(() => {
    if (ready) SplashScreen.hideAsync().catch(() => {});
  }, [ready]);

  // Espera a las fuentes y a la hidratación de sesión antes de montar las rutas
  // (con el splash todavía visible).
  if (!ready) return null;

  return (
    <Providers>
      <StatusBar style="light" />
      <Stack screenOptions={{ headerShown: false }} />
    </Providers>
  );
}
