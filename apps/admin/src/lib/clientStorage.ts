import type { StateStorage } from "zustand/middleware";

/**
 * Storage SSR-safe para los stores persistidos: en el server de Next no existe
 * `localStorage`, así que las operaciones son no-op y la persistencia recién
 * corre en el cliente tras hidratar.
 */
export const clientStorage: StateStorage = {
  getItem: (name) => (typeof window === "undefined" ? null : window.localStorage.getItem(name)),
  setItem: (name, value) => {
    if (typeof window !== "undefined") window.localStorage.setItem(name, value);
  },
  removeItem: (name) => {
    if (typeof window !== "undefined") window.localStorage.removeItem(name);
  },
};

/**
 * Variante efímera sobre `sessionStorage`: solo sobrevive el refresh, se pierde
 * al cerrar el tab.
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
      // storage bloqueado o cuota llena
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
