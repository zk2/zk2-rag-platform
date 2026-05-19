"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/lib/auth-store";
import { Button } from "@/components/ui/button";

export default function DashboardPage() {
  const router = useRouter();
  const { user, memberships, currentOrgId, clear } = useAuthStore();

  useEffect(() => {
    if (!user) router.replace("/login");
  }, [user, router]);

  if (!user) return null;
  const currentOrg = memberships.find((m) => m.org_id === currentOrgId);

  return (
    <div className="min-h-screen px-8 py-12 bg-slate-50">
      <div className="max-w-3xl mx-auto bg-white border border-slate-200 rounded-xl p-8">
        <div className="flex items-start justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-slate-900">
              Welcome, {user.full_name ?? user.email}
            </h1>
            {currentOrg && (
              <p className="text-sm text-slate-500 mt-1">
                Active org: <strong>{currentOrg.org_name}</strong> ({currentOrg.role})
              </p>
            )}
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              clear();
              router.push("/");
            }}
          >
            Sign out
          </Button>
        </div>
        <p className="text-slate-600">
          Bots, sources, chat playground and other modules will appear here as Week 2+ slices land.
          See <code className="text-xs bg-slate-100 px-1 py-0.5 rounded">PLAN.md</code> for what
          ships when.
        </p>
      </div>
    </div>
  );
}
