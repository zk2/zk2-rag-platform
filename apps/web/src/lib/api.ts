/**
 * Thin API client. Talks to `/api/*` which Next rewrites to API_BASE_URL.
 * Bearer token is taken from the auth store (client-side).
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

async function request<T>(
  path: string,
  init: RequestInit = {},
  { auth = true }: { auth?: boolean } = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  if (!headers.has("Content-Type") && init.body) {
    headers.set("Content-Type", "application/json");
  }
  if (auth) {
    const token = useAuthStore.getState().accessToken;
    if (token) headers.set("Authorization", `Bearer ${token}`);
  }
  const orgId = useAuthStore.getState().currentOrgId;
  if (orgId) headers.set("X-Org-Id", String(orgId));

  const res = await fetch(`${BASE}${path}`, { ...init, headers });
  const text = await res.text();
  const json = text ? JSON.parse(text) : null;
  if (!res.ok) {
    const err = json?.error;
    throw new ApiError(res.status, err?.code ?? "unknown", err?.message ?? res.statusText);
  }
  return json as T;
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
    const headers = new Headers();
    const token = useAuthStore.getState().accessToken;
    if (token) headers.set("Authorization", `Bearer ${token}`);
    const orgId = useAuthStore.getState().currentOrgId;
    if (orgId) headers.set("X-Org-Id", String(orgId));
    const fd = new FormData();
    fd.append("file", file);
    const qs = new URLSearchParams(
      Object.fromEntries(Object.entries(query).map(([k, v]) => [k, String(v)])),
    );
    const qsStr = qs.toString();
    const res = await fetch(`${BASE}${path}${qsStr ? `?${qsStr}` : ""}`, {
      method: "POST",
      headers,
      body: fd,
    });
    const text = await res.text();
    const json = text ? JSON.parse(text) : null;
    if (!res.ok) {
      const err = json?.error;
      throw new ApiError(res.status, err?.code ?? "unknown", err?.message ?? res.statusText);
    }
    return json as T;
  },
};
