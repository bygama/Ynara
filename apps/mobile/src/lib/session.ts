import type { QueryClient } from "@tanstack/react-query";
import { clearSession } from "@ynara/core/features/auth";
import { useBackendSessionStore } from "../stores/backendSessions";
import { useChatStore } from "../stores/chat";
import { useChatSessionStore } from "../stores/chatSession";
import { useActiveModeStore } from "../stores/mode";
import { useOnboardingStore } from "../stores/onboarding";
import { useOnboardingStepStore } from "../stores/onboardingStep";
import { useUserStore } from "../stores/user";

type QueryClientLike = Pick<QueryClient, "clear">;

let activeQueryClient: QueryClientLike | undefined;

/** Registra el `QueryClient` activo para limpiarlo ante 401 fuera de React. */
export function registerMobileQueryClient(client: QueryClientLike): void {
  activeQueryClient = client;
}

export type ClearMobileSessionOptions = {
  /** Si es `true` (default), revoca la sesión en el servidor best-effort. */
  revokeRemote?: boolean;
};

/**
 * Limpieza centralizada de sesión en mobile.
 *
 * Revoca el token en el servidor (best-effort) y borra todos los stores con
 * datos del usuario más la caché de TanStack Query. Mantiene `useA11yStore`
 * porque guarda preferencias del dispositivo, no datos de cuenta.
 */
export function clearMobileSession(
  queryClient?: QueryClientLike,
  options?: ClearMobileSessionOptions,
): void {
  const client = queryClient ?? activeQueryClient;
  clearSession({
    getToken: () => useUserStore.getState().token,
    revokeRemote: options?.revokeRemote ?? true,
    resetStores: () => {
      useUserStore.getState().reset();
      useChatStore.getState().reset();
      useBackendSessionStore.getState().reset();
      useChatSessionStore.getState().reset();
      useActiveModeStore.getState().reset();
      useOnboardingStore.getState().reset();
      useOnboardingStepStore.getState().reset();
    },
    clearQueryCache: () => {
      client?.clear();
    },
  });
}
