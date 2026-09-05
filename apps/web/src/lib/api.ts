/**
 * Thin API client. Talks to `/api/*` which Next rewrites to API_BASE_URL.
 * Bearer token is taken from the auth store (client-side).
 *
 * Access tokens are deliberately short-lived, so any request can come back 401
 * on a session that is otherwise perfectly valid. When that happens the client
 * spends the refresh token once and replays the request; only a refresh that
 * itself fails ends the session.
 */
import { useAuthStore } from "@/lib/auth-store";

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

type TokenResponse = { access_token: string; refresh_token: string };

/**
 * A new access token, or why there is none.
 *
 * `over` separates the two failures that look alike from the outside: the API
 * refused the refresh token (the session really is finished) versus the request
 * never arrived (offline, API restarting) - which is no reason to throw a valid
 * session away.
 */
export type Refresh = { token: string | null; over: boolean };

/**
 * The refresh in flight, if any.
 *
 * Every page mounts several queries at once, so an expired token produces a
 * burst of 401s. Refresh tokens rotate: the first call invalidates the token
 * the others are holding, and letting them all refresh would log the user out
 * on a session that is perfectly valid. They wait on one call instead.
 */
let refreshing: Promise<Refresh> | null = null;

async function requestNewToken(): Promise<Refresh> {
  const refreshToken = useAuthStore.getState().refreshToken;
  if (!refreshToken) return { token: null, over: true };
  try {
    const res = await fetch(`${BASE}/auth/refresh`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
    });
    if (!res.ok) return { token: null, over: true };
    const tokens = (await res.json()) as TokenResponse;
    useAuthStore.getState().setTokens(tokens.access_token, tokens.refresh_token);
    return { token: tokens.access_token, over: false };
  } catch {
    return { token: null, over: false };
  }
}

/** Spend the refresh token for a new access token. Exported for the chat
 * socket, which authenticates with the same token outside this client. */
export function refreshSession(): Promise<Refresh> {
  refreshing ??= requestNewToken().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

function endSession(): void {
  // Clearing the user is what the app shell watches; it sends us to /login
  useAuthStore.getState().clear();
}

function authHeaders(headers: Headers, { auth }: { auth: boolean }): Headers {
  if (auth) {
    const token = useAuthStore.getState().accessToken;
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  const orgId = useAuthStore.getState().currentOrgId;
  if (orgId) headers.set("X-Org-Id", String(orgId));
  return headers;
}

/** Send, and on a 401 refresh once and send again. */
async function send(
  url: string,
  init: RequestInit,
  headers: Headers,
  auth: boolean,
): Promise<Response> {
  const res = await fetch(url, { ...init, headers: authHeaders(headers, { auth }) });
  if (res.status !== 401 || !auth) return res;

  const { token, over } = await refreshSession();
  if (!token) {
    if (over) endSession();
    return res;
  }
  headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

async function parse<T>(res: Response): Promise<T> {
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = json?.error;
    throw new ApiError(res.status, err?.code ?? "unknown", err?.message ?? res.statusText);
  }
  return json as T;
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  { auth = true }: { auth?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  return parse<T>(await send(`${BASE}${path}`, init, headers, auth));
}

export const api = {
  get: <T>(path: string, opts?: { auth?: boolean }) =>
    request<T>(path, { method: "GET" }, opts),
  post: <T>(path: string, body?: unknown, opts?: { auth?: boolean }) =>
    request<T>(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }, opts),
  patch: <T>(path: string, body?: unknown, opts?: { auth?: boolean }) =>
    request<T>(path, { method: "PATCH", body: body ? JSON.stringify(body) : undefined }, opts),
  put: <T>(path: string, body?: unknown, opts?: { auth?: boolean }) =>
    request<T>(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }, opts),
  delete: <T>(path: string, opts?: { auth?: boolean }) =>
    request<T>(path, { method: "DELETE" }, opts),
  upload: async <T>(path: string, file: File, query: Record<string, string | number> = {}) => {
    const fd = new FormData();
    fd.append("file", file);
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(query).map(([k, v]) => [k, String(v)])),
    );
    const qsStr = qs.toString();
    // No Content-Type: fetch sets the multipart boundary itself
    const res = await send(
      `${BASE}${path}${qsStr ? `?${qsStr}` : ""}`,
      { method: "POST", body: fd },
      new Headers(),
      true,
    );
    return parse<T>(res);
  },
};
