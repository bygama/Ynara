import { describe, expect, it, vi } from "vitest";
import type { StateStorage } from "zustand/middleware";
import {
  createChatStorage,
  MAX_PERSISTED_MESSAGES_PER_SESSION,
  MAX_PERSISTED_SESSIONS,
  pruneChatHistory,
} from "./persistence";
import { type ChatSessionMeta, type ChatUiMessage, createChatStore } from "./store";

function makeSession(id: string, updatedAt: number): ChatSessionMeta {
  return {
    id,
    mode: "vida",
    createdAt: updatedAt - 1000,
    updatedAt,
  };
}

function makeMessage(id: string, reasoning?: string): ChatUiMessage {
  return {
    id,
    role: "assistant",
    text: `texto-${id}`,
    status: "done",
    ...(reasoning !== undefined ? { reasoning } : {}),
  };
}

describe("pruneChatHistory", () => {
  it("retiene solo las sesiones más recientes hasta el tope y descarta huérfanas", () => {
    const sessions: Record<string, ChatSessionMeta> = {};
    const messages: Record<string, ChatUiMessage[]> = {
      orphan: [makeMessage("o1")],
    };

    for (let i = 0; i < MAX_PERSISTED_SESSIONS + 5; i += 1) {
      const id = `s-${i}`;
      sessions[id] = makeSession(id, i * 1000);
      messages[id] = [makeMessage(`m-${i}`, "cadena interna")];
    }

    const pruned = pruneChatHistory(sessions, messages);

    expect(Object.keys(pruned.sessions)).toHaveLength(MAX_PERSISTED_SESSIONS);
    expect(pruned.sessions["s-0"]).toBeUndefined();
    expect(pruned.sessions["s-4"]).toBeUndefined();
    expect(pruned.sessions[`s-${MAX_PERSISTED_SESSIONS + 4}`]).toBeDefined();
    expect(pruned.messages.orphan).toBeUndefined();
    expect(pruned.messages[`s-${MAX_PERSISTED_SESSIONS + 4}`]?.[0]?.reasoning).toBeUndefined();
  });

  it("acota mensajes por sesión conservando los más recientes", () => {
    const sessions = { s1: makeSession("s1", 1000) };
    const list: ChatUiMessage[] = [];
    for (let i = 0; i < MAX_PERSISTED_MESSAGES_PER_SESSION + 20; i += 1) {
      list.push(makeMessage(`m-${i}`));
    }

    const pruned = pruneChatHistory(sessions, { s1: list });
    const kept = pruned.messages.s1 ?? [];

    expect(kept).toHaveLength(MAX_PERSISTED_MESSAGES_PER_SESSION);
    expect(kept[0]?.id).toBe("m-20");
    expect(kept[kept.length - 1]?.id).toBe(`m-${MAX_PERSISTED_MESSAGES_PER_SESSION + 19}`);
  });
});

describe("createChatStorage", () => {
  it("omite escrituras mientras isStreaming() es true y persiste al pasar a false", () => {
    const base: StateStorage = {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };
    let streaming = true;
    const wrapped = createChatStorage(base, () => streaming);

    wrapped.setItem("ynara.chat", "parcial-1");
    wrapped.setItem("ynara.chat", "parcial-2");
    expect(base.setItem).not.toHaveBeenCalled();

    streaming = false;
    wrapped.setItem("ynara.chat", "final");
    expect(base.setItem).toHaveBeenCalledOnce();
    expect(base.setItem).toHaveBeenCalledWith("ynara.chat", "final");
  });

  it("tolera QuotaExceededError sincrónico y rechazos async sin romper el chat", async () => {
    const syncFailing: StateStorage = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("QuotaExceededError");
      },
      removeItem: () => {
        throw new Error("denied");
      },
    };
    const wrappedSync = createChatStorage(syncFailing, () => false);
    expect(wrappedSync.getItem("k")).toBeNull();
    expect(() => wrappedSync.setItem("k", "v")).not.toThrow();
    expect(() => wrappedSync.removeItem("k")).not.toThrow();

    const asyncFailing: StateStorage = {
      getItem: () => Promise.reject(new Error("io")),
      setItem: () => Promise.reject(new Error("disk full")),
      removeItem: () => Promise.reject(new Error("io")),
    };
    const wrappedAsync = createChatStorage(asyncFailing, () => false);
    await expect(wrappedAsync.getItem("k")).resolves.toBeNull();
    wrappedAsync.setItem("k", "v");
    wrappedAsync.removeItem("k");
    await Promise.resolve();
  });
});

describe("createChatStore — persistencia en streaming", () => {
  it("no escribe a disco en cada token y persiste al finalizar el stream", () => {
    const setItem = vi.fn();
    const memory: StateStorage = {
      getItem: () => null,
      setItem,
      removeItem: vi.fn(),
    };
    const useStore = createChatStore(memory);
    const sid = useStore.getState().createSession("vida");
    const uid = useStore.getState().appendUserMessage(sid, "hola");

    setItem.mockClear();

    const aid = useStore.getState().startAssistantStream(sid, uid);
    for (let i = 0; i < 50; i += 1) {
      useStore.getState().appendStreamDelta(sid, aid, `tok${i} `);
    }
    expect(setItem).not.toHaveBeenCalled();

    useStore.getState().finishAssistantStream(sid, aid);
    expect(setItem).toHaveBeenCalledOnce();
    const persisted = String(setItem.mock.calls[0]?.[1] ?? "");
    expect(persisted).toContain("tok0 ");
    expect(persisted).toContain("tok49 ");
  });
});
