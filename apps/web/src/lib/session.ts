import type { QueryClient } from "@tanstack/react-query";
import { clearSession } from "@ynara/core/features/auth";
import { useChatStore } from "@/features/chat/store";
import { useOnboardingStore } from "@/features/onboarding/store";
import { useAvisosStore } from "@/features/today/avisosStore";
import { useActiveModeStore } from "@/stores/mode";
import { useShowReasoningStore } from "@/stores/showReasoning";
import { useUserStore } from "@/stores/user";

export type ClearWebSessionOptions = {
  /** Si es `true` (default), revoca la sesión en el servidor best-effort. */
  revokeRemote?: boolean;
};

/**
 * Limpieza centralizada de sesión en web.
 *
 * Revoca la sesión en el servidor (best-effort) y borra todos los stores con
 * datos del usuario más la caché de TanStack Query. Mantiene `useA11yStore` y
 * `useThemeStore` porque guardan preferencias del dispositivo.
 */
export function clearWebSession(
  queryClient?: Pick<QueryClient, "clear">,
  options?: ClearWebSessionOptions,
): void {
  clearSession({
    getToken: () => useUserStore.getState().token,
    revokeRemote: options?.revokeRemote ?? true,
    resetStores: () => {
      useUserStore.getState().reset();
      useChatStore.getState().reset();
      useActiveModeStore.getState().reset();
      useAvisosStore.getState().reset();
      useOnboardingStore.getState().reset();
      useShowReasoningStore.getState().reset();
    },
    clearQueryCache: () => {
      queryClient?.clear();
    },
  });
}

/**
 * Maneja una sesión rechazada por el servidor (401 sin recuperación posible):
 * limpia el estado local sin intentar revocación remota y redirige a
 * `/onboarding`. Idempotente cuando el token ya fue vaciado por otro 401.
 */
export function handleWebUnauthorized(queryClient?: Pick<QueryClient, "clear">): void {
  if (useUserStore.getState().token === null) return;
  clearWebSession(queryClient, { revokeRemote: false });
  if (typeof window !== "undefined" && window.location.pathname !== "/onboarding") {
    window.location.assign("/onboarding");
  }
}
