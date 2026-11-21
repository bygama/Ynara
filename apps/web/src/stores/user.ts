import { createUserStore } from "@ynara/core/stores";
import { sessionClientStorage } from "@/lib/clientStorage";

// Limpieza best-effort de residuos previos en localStorage al migrar a sessionStorage.
if (typeof window !== "undefined") {
  try {
    window.localStorage.removeItem("ynara.user");
  } catch {
    // storage bloqueado o modo privado restrictivo
  }
}

// Instancia web del store de usuario (ADR-012): la lógica vive en @ynara/core;
// acá se inyecta el storage SSR-safe sobre sessionStorage para que la sesión
// caduque al cerrar la pestaña. Se mantiene el import `@/stores/user` estable.
export const useUserStore = createUserStore(sessionClientStorage);

export type { UserProfile } from "@ynara/core/stores";
