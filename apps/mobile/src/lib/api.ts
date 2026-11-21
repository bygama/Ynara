import { configureApi } from "@ynara/core/api";
import { useUserStore } from "@/stores/user";
import { env, shouldEnableMocks } from "./env";

type FetchFn = (input: string, init?: RequestInit) => Promise<Response>;

let mockFetchPromise: Promise<FetchFn> | null = null;

// Carga diferida de los handlers de desarrollo: solo se importan cuando
// `shouldEnableMocks` está activo en dev.
function loadMockFetch(): Promise<FetchFn> {
  if (!mockFetchPromise) {
    mockFetchPromise = Promise.all([
      import("@/features/agenda/mocks"),
      import("@/features/memory/mocks"),
      import("@/features/profile/mocks"),
      import("@/features/today/mocks"),
    ]).then(([agenda, memory, profile, today]) => (input, init) => {
      const mem = memory.memoryMockResponse(input, init);
      if (mem) return Promise.resolve(mem);
      const ag = agenda.agendaMockResponse(input, init);
      if (ag) return Promise.resolve(ag);
      const prof = profile.profileMockResponse(input, init);
      if (prof) return Promise.resolve(prof);
      return today.todayMockFetch(input, init);
    });
  }
  return mockFetchPromise;
}

const lazyMockFetch: FetchFn = async (input, init) => {
  const impl = await loadMockFetch();
  return impl(input, init);
};

// Wiring de plataforma (ADR-012): el cliente HTTP vive en @ynara/core; mobile
// le inyecta su base URL (EXPO_PUBLIC_API_URL) y su token (del user store, que
// persiste en SecureStore). El side-effect corre al importar este módulo.
configureApi({
  baseUrl: env.EXPO_PUBLIC_API_URL,
  getToken: () => useUserStore.getState().token,
  ...(shouldEnableMocks ? { fetchImpl: lazyMockFetch } : {}),
});

export { ApiError, api } from "@ynara/core/api";
