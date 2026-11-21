import AsyncStorage from "@react-native-async-storage/async-storage";
import type { StateStorage } from "zustand/middleware";

/**
 * Adapter de `StateStorage` (Zustand persist) sobre AsyncStorage. Para el
 * historial del chat (sesiones + mensajes): NO es secreto — el token/perfil van
 * en SecureStore (regla #5) — y persiste entre recargas/reinicios, a diferencia
 * del draft del onboarding (memoryStorage, efímero). AsyncStorage es async;
 * `persist` rehidrata al cargar el store. Tolera fallos de disco/cuota sin
 * romper el runtime.
 */
export const asyncStorage: StateStorage = {
  getItem: (name) => AsyncStorage.getItem(name).catch(() => null),
  setItem: (name, value) => AsyncStorage.setItem(name, value).catch(() => {}),
  removeItem: (name) => AsyncStorage.removeItem(name).catch(() => {}),
};
