import { configureApi } from "@ynara/core/api";
import { refreshAuthToken } from "@ynara/core/features/auth";
import { useUserStore } from "@/stores/user";
import { env } from "./env";
import { handleWebUnauthorized } from "./session";

function readPersistedSessionField(field: "token" | "refreshToken"): string | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.sessionStorage.getItem("ynara.user");
    if (!raw) return null;
    const parsed = JSON.parse(raw) as {
      state?: { token?: string | null; refreshToken?: string | null };
    };
    return parsed?.state?.[field] ?? null;
  } catch {
    return null;
  }
}

// Wiring de plataforma (ADR-012): el cliente HTTP vive en @ynara/core; la web
// le inyecta su base URL (env público), su token provider (del user store),
// el flujo de refresh token y el manejo de 401. El side-effect corre al importar
// este módulo, antes de cualquier request.
configureApi({
  baseUrl: env.NEXT_PUBLIC_API_URL,
  getToken: () => {
    const fromStore = useUserStore.getState().token;
    if (fromStore) return fromStore;
    // Fallback anti-race de hidratación: cubre únicamente la ventana entre el
    // primer render y la rehidratación desde sessionStorage.
    return readPersistedSessionField("token");
  },
  refreshSession: async () => {
    const rt = useUserStore.getState().refreshToken ?? readPersistedSessionField("refreshToken");
    if (!rt) return null;
    try {
      const next = await refreshAuthToken(rt);
      useUserStore.getState().setTokens({
        token: next.access_token,
        refreshToken: next.refresh_token,
      });
      return next.access_token;
    } catch {
      return null;
    }
  },
  onUnauthorized: () => {
    handleWebUnauthorized();
  },
});

export {
  ApiError,
  api,
  applyAuthHeader,
  notifyUnauthorized,
  tryRefreshSession,
} from "@ynara/core/api";
