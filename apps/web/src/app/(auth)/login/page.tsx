"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

type TokenResponse = {
  access_token: string;
  refresh_token: string;
  expires_in: number;
};

type MeResponse = {
  user: {
    id: number;
    email: string;
    full_name: string | null;
    is_super_admin: boolean;
  };
  memberships: Array<{
    org_id: number;
    org_slug: string;
    org_name: string;
    role: string;
  }>;
};

export default function LoginPage() {
  const router = useRouter();
  const setTokens = useAuthStore((s) => s.setTokens);
  const setMe = useAuthStore((s) => s.setMe);

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const tokens = await api.post<TokenResponse>(
        "/auth/login",
        { email, password },
        { auth: false },
      );
      setTokens(tokens.access_token, tokens.refresh_token);
      const me = await api.get<MeResponse>("/auth/me");
      setMe(me.user, me.memberships);
      router.push(me.user.is_super_admin ? "/admin/access-requests" : "/dashboard");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <>
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Sign in</h1>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div>
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit" disabled={loading} className="w-full">
          {loading ? "Signing in..." : "Sign in"}
        </Button>
      </form>
      <div className="mt-6 text-sm text-slate-600 space-y-2 text-center">
        <p>
          <Link href="/magic" className="text-slate-900 underline">
            Email me a magic link instead
          </Link>
        </p>
        <p>
          No account?{" "}
          <Link href="/request-access" className="text-slate-900 underline">
            Request access
          </Link>
        </p>
      </div>
    </>
  );
}
