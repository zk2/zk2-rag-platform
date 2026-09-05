/**
 * The API client's session handling. Access tokens expire after minutes, so
 * this is the difference between a working app and one that silently empties
 * itself while you are looking at it.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

function unauthorized(): Response {
  return new Response(JSON.stringify({ error: { code: "unauthorized", message: "Expired" } }), {
    status: 401,
    headers: { "Content-Type": "application/json" },
  });
}

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

function tokens(suffix: string): Response {
  return ok({ access_token: `access-${suffix}`, refresh_token: `refresh-${suffix}` });
}

function signedIn(): void {
  useAuthStore.setState({
    accessToken: "access-old",
    refreshToken: "refresh-old",
    user: { id: 1, email: "owner@example.com", full_name: null, is_super_admin: false },
    memberships: [],
    currentOrgId: 1,
  });
}

function authHeader(call: [string, RequestInit]): string | null {
  return new Headers(call[1].headers).get("Authorization");
}

describe("the API client", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
    signedIn();
  });

  it("refreshes an expired token and replays the request", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(tokens("new"))
      .mockResolvedValueOnce(ok([{ id: 1 }]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get("/bots")).resolves.toEqual([{ id: 1 }]);

    const calls = fetchMock.mock.calls as Array<[string, RequestInit]>;
    expect(calls.map((c) => c[0])).toEqual(["/api/bots", "/api/auth/refresh", "/api/bots"]);
    expect(authHeader(calls[2])).toBe("Bearer access-new");
    expect(useAuthStore.getState().accessToken).toBe("access-new");
    expect(useAuthStore.getState().refreshToken).toBe("refresh-new");
    expect(useAuthStore.getState().user).not.toBeNull();
  });

  it("refreshes once for a burst of parallel 401s", async () => {
    // Refresh tokens rotate: a second refresh would spend an already-spent
    // token and log the user out on a session that is perfectly valid
    const fetchMock = vi.fn(async (url: string) =>
      url === "/api/auth/refresh" ? tokens("new") : unauthorized(),
    );
    vi.stubGlobal("fetch", fetchMock);

    await Promise.all([
      api.get("/bots").catch(() => null),
      api.get("/sources/tree").catch(() => null),
      api.get("/settings/models").catch(() => null),
    ]);

    const refreshes = (fetchMock.mock.calls as Array<[string]>).filter(
      (c) => c[0] === "/api/auth/refresh",
    );
    expect(refreshes).toHaveLength(1);
  });

  it("ends the session when the refresh token is rejected too", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorized())
      .mockResolvedValueOnce(new Response("", { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get("/bots")).rejects.toBeInstanceOf(ApiError);
    expect(useAuthStore.getState().user).toBeNull();
    expect(useAuthStore.getState().accessToken).toBeNull();
  });

  it("keeps the session when the API is unreachable", async () => {
    // A dropped connection is not a revoked session: signing the user out here
    // would lose their place every time the dev server restarts
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(unauthorized())
      .mockRejectedValueOnce(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.get("/bots")).rejects.toBeInstanceOf(ApiError);
    expect(useAuthStore.getState().user).not.toBeNull();
    expect(useAuthStore.getState().refreshToken).toBe("refresh-old");
  });

  it("does not try to refresh an anonymous request", async () => {
    const fetchMock = vi.fn().mockResolvedValue(unauthorized());
    vi.stubGlobal("fetch", fetchMock);

    await expect(api.post("/auth/login", { email: "x@y.z" }, { auth: false })).rejects.toThrow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("sends the organization header with every call", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok({}));
    vi.stubGlobal("fetch", fetchMock);

    await api.get("/bots");
    const calls = fetchMock.mock.calls as Array<[string, RequestInit]>;
    expect(new Headers(calls[0][1].headers).get("X-Org-Id")).toBe("1");
  });
});
