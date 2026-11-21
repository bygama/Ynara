import type { StateStorage } from "zustand/middleware";

/**
 * Storage SSR-safe para los stores persistidos de @ynara/core: en el server
 * de Next no existe `localStorage`, así que las operaciones son no-op y la
 * persistencia recién corre en el cliente tras hidratar. Envuelve los accesos
 * en `try/catch` para tolerar cuota llena o modo privado restrictivo.
 */
export const clientStorage: StateStorage = {
  getItem: (name) => {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(name, value);
    } catch {
      // Cuota llena o storage bloqueado: degradar sin romper la app.
    }
  },
  removeItem: (name) => {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.removeItem(name);
    } catch {
      // best-effort
    }
  },
};

/**
 * Variante efímera sobre `sessionStorage` para el draft del onboarding: si el
 * user cierra el tab y vuelve, está OK perderlo; solo sobrevive el refresh.
 */
export const sessionClientStorage: StateStorage = {
  getItem: (name) => {
    if (typeof window === "undefined") return null;
    try {
      return window.sessionStorage.getItem(name);
    } catch {
      return null;
    }
  },
  setItem: (name, value) => {
    if (typeof window === "undefined") return;
    try {
      window.sessionStorage.setItem(name, value);
    } catch {
      // best-effort
    }
  },
  removeItem: (name) => {
    if (typeof window === "undefined") return;
    try {
      window.sessionStorage.removeItem(name);
    } catch {
      // best-effort
    }
  },
};
