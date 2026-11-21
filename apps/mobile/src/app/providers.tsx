import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { SafeAreaProvider } from "react-native-safe-area-context";
import { ApiError } from "@/lib/api";
import { clearMobileSession, registerMobileQueryClient } from "@/lib/session";
import { useUserStore } from "@/stores/user";

// Providers raíz de la app mobile. Misma config de TanStack Query que web
// (apps/web/src/app/providers.tsx); el cliente vive una sola vez vía useState.
function makeQueryClient() {
  let client: QueryClient;
  const onAuthError = (error: unknown): void => {
    if (!(error instanceof ApiError) || error.status !== 401) return;
    if (useUserStore.getState().token === null) return;
    clearMobileSession(client, { revokeRemote: false });
  };
  client = new QueryClient({
    queryCache: new QueryCache({ onError: onAuthError }),
    mutationCache: new MutationCache({ onError: onAuthError }),
    defaultOptions: {
      queries: {
        staleTime: 30 * 1000,
        refetchOnWindowFocus: false,
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status === 401) && failureCount < 1,
      },
    },
  });
  registerMobileQueryClient(client);
  return client;
}

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(makeQueryClient);

  return (
    <SafeAreaProvider>
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    </SafeAreaProvider>
  );
}
