import type { StateStorage } from "zustand/middleware";
import type { ChatSessionMeta, ChatUiMessage } from "./store";

/** Tope de sesiones recientes retenidas en disco por dispositivo. */
export const MAX_PERSISTED_SESSIONS = 30;

/** Tope de mensajes recientes retenidos en disco por sesión. */
export const MAX_PERSISTED_MESSAGES_PER_SESSION = 100;

/**
 * Devuelve una copia del mensaje sin `reasoning` (efímero, no se persiste).
 * Si el mensaje no tiene `reasoning`, lo devuelve tal cual sin copiar.
 */
export function stripReasoning(message: ChatUiMessage): ChatUiMessage {
  if (message.reasoning === undefined) return message;
  const copy = { ...message };
  copy.reasoning = undefined;
  return copy;
}

/**
 * Acota el historial persistido a las últimas `maxSessions` sesiones (por
 * `updatedAt` descendente) y los últimos `maxMessages` mensajes por sesión,
 * descartando `reasoning` y listas huérfanas.
 */
export function pruneChatHistory(
  sessions: Record<string, ChatSessionMeta>,
  messages: Record<string, ChatUiMessage[]>,
  maxSessions: number = MAX_PERSISTED_SESSIONS,
  maxMessages: number = MAX_PERSISTED_MESSAGES_PER_SESSION,
): { sessions: Record<string, ChatSessionMeta>; messages: Record<string, ChatUiMessage[]> } {
  const keptEntries = Object.entries(sessions)
    .sort(([, a], [, b]) => b.updatedAt - a.updatedAt)
    .slice(0, maxSessions);

  const nextSessions: Record<string, ChatSessionMeta> = {};
  const nextMessages: Record<string, ChatUiMessage[]> = {};

  for (const [sid, session] of keptEntries) {
    nextSessions[sid] = session;
    const list = messages[sid];
    if (list) {
      const sliced = list.length > maxMessages ? list.slice(-maxMessages) : list;
      nextMessages[sid] = sliced.map(stripReasoning);
    }
  }

  return { sessions: nextSessions, messages: nextMessages };
}

function swallowPromise(result: unknown): void {
  if (result && typeof (result as PromiseLike<unknown>).then === "function") {
    void Promise.resolve(result).catch(() => {});
  }
}

/**
 * Envuelve un `StateStorage` para el store de chat:
 * - Saltea escrituras mientras `isStreaming()` es `true` (evita serializar y
 *   escribir todo el historial en cada token; se persiste al cerrar el stream).
 * - Envuelve lecturas, escrituras y borrados en `try/catch` (y atrapa rechazos
 *   async) para que un disco/cuota llena nunca rompa la conversación.
 */
export function createChatStorage(base: StateStorage, isStreaming: () => boolean): StateStorage {
  return {
    getItem: (name) => {
      try {
        const res = base.getItem(name);
        if (res && typeof (res as PromiseLike<string | null>).then === "function") {
          return Promise.resolve(res).catch(() => null);
        }
        return res;
      } catch {
        return null;
      }
    },
    setItem: (name, value) => {
      if (isStreaming()) return;
      try {
        swallowPromise(base.setItem(name, value));
      } catch {
        // Cuota llena o storage no disponible: el chat sigue en memoria.
      }
    },
    removeItem: (name) => {
      try {
        swallowPromise(base.removeItem(name));
      } catch {
        // best-effort
      }
    },
  };
}
