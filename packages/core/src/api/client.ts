/**
 * Cliente HTTP compartido web + mobile (ADR-012). El token y la base URL se
 * inyectan vía `configureApi` para no acoplar el cliente a ningún store ni
 * runtime: la web cablea su token desde su user store, mobile desde el suyo
 * (que lee de SecureStore). Perímetro (reglas #2/#4 de AGENTS): el Bearer
 * SOLO viaja a nuestra API.
 */

type ApiConfig = {
  baseUrl: string;
  getToken: () => string | null;
  /**
   * Renueva la sesión (p. ej. vía `POST /v1/auth/refresh`) y devuelve el nuevo
   * access token, o `null` si no hay refresh token o falló la renovación. El
   * cliente deduplica llamadas concurrentes (single-flight).
   */
  refreshSession?: () => Promise<string | null>;
  /**
   * Callback disparado cuando una request autenticada contra nuestra API recibe
   * 401 y no se pudo recuperar la sesión. Cada app limpia sus stores y navega
   * al login/onboarding.
   */
  onUnauthorized?: () => void;
  /**
   * Transporte HTTP. Default: el `fetch` global, resuelto en cada llamada (no la
   * referencia capturada) para que un parche posterior —MSW sobre el fetch global
   * en web— tome efecto. Inyectable para mock-first sin tocar los hooks: mobile
   * cablea un mock-fetch del dominio "Hoy" cuando `EXPO_PUBLIC_ENABLE_MOCKS` está
   * prendido y delega el resto (auth incluido) al fetch real. Core siempre pasa
   * una URL string ya resuelta.
   */
  fetchImpl: (input: string, init?: RequestInit) => Promise<Response>;
};

let config: ApiConfig = {
  baseUrl: "http://localhost:8080",
  getToken: () => null,
  fetchImpl: (input, init) => fetch(input, init),
};

let inflightRefresh: Promise<string | null> | null = null;

/** Cablea base URL y token provider. Cada app lo llama una vez al cargar. */
export function configureApi(next: Partial<ApiConfig>): void {
  config = { ...config, ...next };
}

/**
 * Intenta renovar la sesión con deduplicación single-flight: varias requests
 * que caen en 401 al mismo tiempo comparten una única rotación de refresh token.
 */
export function tryRefreshSession(): Promise<string | null> {
  if (!config.refreshSession) return Promise.resolve(null);
  if (!inflightRefresh) {
    inflightRefresh = Promise.resolve()
      .then(() => config.refreshSession?.() ?? null)
      .catch(() => null)
      .finally(() => {
        inflightRefresh = null;
      });
  }
  return inflightRefresh;
}

/** Notifica a la app que la sesión ya no es válida (p. ej. 401 en streaming). */
export function notifyUnauthorized(): void {
  config.onUnauthorized?.();
}

/** Base URL configurada. Útil para el cliente de streaming (SSE). */
export function getBaseUrl(): string {
  return config.baseUrl;
}

/**
 * Adjunta `Authorization: Bearer <token>` a `headers` si hay sesión y el
 * `url` apunta a NUESTRA API (perímetro reglas #2/#4: el token nunca viaja a
 * un host ajeno). SSR-safe: si `getToken` devuelve null, es no-op.
 *
 * Se exporta para que el cliente de streaming arme el mismo header con
 * `fetch` crudo sin reimplementar el guard de perímetro.
 */
export function applyAuthHeader(headers: Headers, url: string): void {
  if (headers.has("Authorization")) return;
  if (!url.startsWith(config.baseUrl)) return;
  const token = config.getToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
}

export class ApiError extends Error {
  readonly status: number;
  readonly body: unknown;

  constructor(status: number, body: unknown, message?: string) {
    super(message ?? `API error ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

type FetchInit = Omit<RequestInit, "body"> & {
  /** El body se serializa a JSON automáticamente. */
  body?: unknown;
  /**
   * Por default todo request adjunta `Authorization: Bearer <token>` si hay
   * sesión. Los endpoints públicos (login/register) pueden pasar `skipAuth`
   * para no mandarlo (igual sería no-op sin token, pero deja la intención
   * explícita).
   */
  skipAuth?: boolean;
};

async function request<T>(
  path: string,
  init: FetchInit = {},
  retried = false,
  overrideToken?: string,
): Promise<T> {
  const url = path.startsWith("http") ? path : `${config.baseUrl}${path}`;
  const headers = new Headers(init.headers);
  const hadExplicitAuth = headers.has("Authorization");
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (!headers.has("Accept")) {
    headers.set("Accept", "application/json");
  }
  // Inyección de auth: el header se arma una sola vez en `applyAuthHeader`
  // (compartido con el cliente de streaming). Perímetro (reglas #2/#4): el
  // Bearer SOLO viaja a nuestra API. `skipAuth` lo omite explícitamente en
  // endpoints públicos (login/register).
  if (!init.skipAuth) {
    if (overrideToken && url.startsWith(config.baseUrl)) {
      headers.set("Authorization", `Bearer ${overrideToken}`);
    } else {
      applyAuthHeader(headers, url);
    }
  }

  const response = await config.fetchImpl(url, {
    ...init,
    headers,
    body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
  });

  if (
    response.status === 401 &&
    !init.skipAuth &&
    !hadExplicitAuth &&
    url.startsWith(config.baseUrl)
  ) {
    if (!retried) {
      const refreshedToken = await tryRefreshSession();
      if (refreshedToken) {
        return request<T>(path, init, true, refreshedToken);
      }
    }
    config.onUnauthorized?.();
  }

  const isJson = response.headers.get("Content-Type")?.toLowerCase().includes("application/json");
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    throw new ApiError(response.status, payload);
  }
  return payload as T;
}

export const api = {
  get: <T>(path: string, init?: Omit<FetchInit, "body" | "method">) =>
    request<T>(path, { ...init, method: "GET" }),
  post: <T>(path: string, body?: unknown, init?: Omit<FetchInit, "body" | "method">) =>
    request<T>(path, { ...init, method: "POST", body }),
  put: <T>(path: string, body?: unknown, init?: Omit<FetchInit, "body" | "method">) =>
    request<T>(path, { ...init, method: "PUT", body }),
  patch: <T>(path: string, body?: unknown, init?: Omit<FetchInit, "body" | "method">) =>
    request<T>(path, { ...init, method: "PATCH", body }),
  delete: <T>(path: string, init?: Omit<FetchInit, "body" | "method">) =>
    request<T>(path, { ...init, method: "DELETE" }),
};
