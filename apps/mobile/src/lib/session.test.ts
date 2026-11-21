import { beforeEach, describe, expect, it, vi } from "vitest";

const clearSessionMock = vi.fn();
const resetUser = vi.fn();
const resetChat = vi.fn();
const resetBackendSessions = vi.fn();
const resetChatSession = vi.fn();
const resetMode = vi.fn();
const resetOnboarding = vi.fn();
const resetOnboardingStep = vi.fn();

let currentToken: string | null = "tok-mobile-1";

vi.mock("@ynara/core/features/auth", () => ({
  clearSession: (opts: {
    getToken: () => string | null | undefined;
    resetStores: () => void;
    clearQueryCache?: () => void;
    revokeRemote?: boolean;
  }) => {
    clearSessionMock(opts);
    opts.getToken();
    opts.resetStores();
    opts.clearQueryCache?.();
  },
}));

vi.mock("../stores/user", () => ({
  useUserStore: { getState: () => ({ token: currentToken, reset: resetUser }) },
}));
vi.mock("../stores/chat", () => ({
  useChatStore: { getState: () => ({ reset: resetChat }) },
}));
vi.mock("../stores/backendSessions", () => ({
  useBackendSessionStore: { getState: () => ({ reset: resetBackendSessions }) },
}));
vi.mock("../stores/chatSession", () => ({
  useChatSessionStore: { getState: () => ({ reset: resetChatSession }) },
}));
vi.mock("../stores/mode", () => ({
  useActiveModeStore: { getState: () => ({ reset: resetMode }) },
}));
vi.mock("../stores/onboarding", () => ({
  useOnboardingStore: { getState: () => ({ reset: resetOnboarding }) },
}));
vi.mock("../stores/onboardingStep", () => ({
  useOnboardingStepStore: { getState: () => ({ reset: resetOnboardingStep }) },
}));

const { clearMobileSession } = await import("./session");

beforeEach(() => {
  currentToken = "tok-mobile-1";
  clearSessionMock.mockClear();
  resetUser.mockClear();
  resetChat.mockClear();
  resetBackendSessions.mockClear();
  resetChatSession.mockClear();
  resetMode.mockClear();
  resetOnboarding.mockClear();
  resetOnboardingStep.mockClear();
});

describe("clearMobileSession", () => {
  it("limpia todos los stores de usuario y la caché de consultas, con revocación remota por defecto", () => {
    const queryClient = { clear: vi.fn() };

    clearMobileSession(queryClient);

    expect(clearSessionMock).toHaveBeenCalledOnce();
    const opts = clearSessionMock.mock.calls[0]?.[0];
    expect(opts.getToken()).toBe("tok-mobile-1");
    expect(opts.revokeRemote).toBe(true);
    expect(resetUser).toHaveBeenCalledOnce();
    expect(resetChat).toHaveBeenCalledOnce();
    expect(resetBackendSessions).toHaveBeenCalledOnce();
    expect(resetChatSession).toHaveBeenCalledOnce();
    expect(resetMode).toHaveBeenCalledOnce();
    expect(resetOnboarding).toHaveBeenCalledOnce();
    expect(resetOnboardingStep).toHaveBeenCalledOnce();
    expect(queryClient.clear).toHaveBeenCalledOnce();
  });

  it("permite omitir la revocación remota cuando la sesión ya expiró", () => {
    clearMobileSession(undefined, { revokeRemote: false });

    const opts = clearSessionMock.mock.calls[0]?.[0];
    expect(opts.revokeRemote).toBe(false);
    expect(resetUser).toHaveBeenCalledOnce();
  });
});
