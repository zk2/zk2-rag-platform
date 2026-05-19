"use client";

import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";

export default function MagicLinkPage() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token");
  const setTokens = useAuthStore((s) => s.setTokens);
  const setMe = useAuthStore((s) => s.setMe);

  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!token) return;
    (async () => {
      setLoading(true);
      try {
        const tokens = await api.post<{
          access_token: string;
          refresh_token: string;
        }>("/auth/magic/verify", { token }, { auth: false });
        setTokens(tokens.access_token, tokens.refresh_token);
        const me = await api.get<{
          user: { is_super_admin: boolean; id: number; email: string; full_name: string | null };
          memberships: Array<{ org_id: number; org_slug: string; org_name: string; role: string }>;
        }>("/auth/me");
        setMe(me.user, me.memberships);
        router.push(me.user.is_super_admin ? "/admin/access-requests" : "/dashboard");
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Invalid link");
      } finally {
        setLoading(false);
      }
    })();
  }, [token, router, setTokens, setMe]);

  if (token) {
    return (
      <div>
        <h1 className="text-2xl font-bold mb-4">Signing you in…</h1>
        {loading && <p className="text-slate-600">Verifying your link…</p>}
        {error && <p className="text-red-600">{error}</p>}
      </div>
    );
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.post("/auth/magic/request", { email }, { auth: false });
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div>
        <h1 className="text-2xl font-bold mb-4">Check your email</h1>
        <p className="text-slate-600">
          If <strong>{email}</strong> is registered, we just sent you a sign-in link.
          It expires shortly.
        </p>
      </div>
    );
  }

  return (
    <>
      <h1 className="text-2xl font-bold mb-6">Magic link sign-in</h1>
      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <Label htmlFor="email">Email</Label>
          <Input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit" disabled={loading} className="w-full">
          {loading ? "Sending…" : "Send link"}
        </Button>
      </form>
    </>
  );
}
