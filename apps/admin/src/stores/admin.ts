import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import { sessionClientStorage } from "@/lib/clientStorage";

/** Duración máxima de la sesión del panel admin en el cliente (4 horas). */
export const ADMIN_SESSION_TTL_MS = 4 * 60 * 60 * 1000;

// Limpieza best-effort de residuos previos en localStorage al migrar a sessionStorage.
if (typeof window !== "undefined") {
  try {
    window.localStorage.removeItem("ynara.admin");
  } catch {
    // storage bloqueado o modo privado restrictivo
  }
}

/**
 * Sesión del operador del panel admin. Deliberadamente separada de la sesión del
 * producto: persiste en `sessionStorage` bajo la key `ynara.admin` (caduca al
 * cerrar la pestaña o al vencer `ADMIN_SESSION_TTL_MS`).
 *
 * El token lo consume `lib/api.ts` (configureApi.getToken) para adjuntar
 * `Authorization: Bearer <token>` SOLO a nuestra API (reglas #2/#4).
 */
export type AdminProfile = {
  adminId: string | null;
  /** Token de sesión admin. Lo lee el cliente API vía getToken. */
  token: string | null;
  displayName: string;
  /** Timestamp epoch (ms) en el que caduca la sesión del panel. */
  expiresAt: number | null;
};

type AdminActions = {
  setAuth: (input: { adminId: string; token: string; displayName?: string }) => void;
  setToken: (token: string | null) => void;
  isSessionExpired: (now?: number) => boolean;
  reset: () => void;
};

const initialState: AdminProfile = {
  adminId: null,
  token: null,
  displayName: "",
  expiresAt: null,
};

export const useAdminStore = create<AdminProfile & AdminActions>()(
  persist(
    (set, get) => ({
      ...initialState,
      setAuth: ({ adminId, token, displayName }) =>
        set({
          adminId,
          token,
          displayName: displayName ?? "",
          expiresAt: Date.now() + ADMIN_SESSION_TTL_MS,
        }),
      setToken: (token) =>
        set({
          token,
          expiresAt: token ? Date.now() + ADMIN_SESSION_TTL_MS : null,
        }),
      isSessionExpired: (now = Date.now()) => {
        const { token, expiresAt } = get();
        if (!token) return false;
        return expiresAt === null || now >= expiresAt;
      },
      reset: () => set(initialState),
    }),
    { name: "ynara.admin", storage: createJSONStorage(() => sessionClientStorage) },
  ),
);
