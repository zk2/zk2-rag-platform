"use client";

import { useEffect } from "react";
import { useRouter, usePathname } from "next/navigation";
import Link from "next/link";
import { useAuthHydrated, useAuthStore } from "@/lib/auth-store";
import { ErrorBanner } from "@/components/error-banner";
import { cn } from "@/lib/utils";
import {
  FolderTree,
  Bot,
  Settings as SettingsIcon,
  LogOut,
  ChevronsUpDown,
  Shield,
  Activity,
  GitBranch,
  Wrench,
  FlaskConical,
} from "lucide-react";

const NAV = [
  { href: "/sources", label: "Sources", icon: FolderTree },
  { href: "/bots", label: "Bots", icon: Bot },
  { href: "/pipelines", label: "Pipelines", icon: GitBranch },
  { href: "/evals", label: "Evals", icon: FlaskConical },
  { href: "/observability", label: "Observability", icon: Activity },
  { href: "/settings/providers", label: "Settings", icon: SettingsIcon },
  { href: "/settings/mcp", label: "Tools", icon: Wrench },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { user, memberships, currentOrgId, setCurrentOrg, clear } = useAuthStore();
  const hydrated = useAuthHydrated();

  useEffect(() => {
    // Two traps here. Before hydration, "no user" only means the persisted
    // session has not been read back yet. And on the first client render React
    // replays the server snapshot, where the store is always empty - so the
    // decision reads the store directly rather than that render's value.
    if (!hydrated) return;
    if (!useAuthStore.getState().user) router.replace("/login");
  }, [hydrated, user, router]);

  if (!hydrated || !user) return null;
  const currentOrg = memberships.find((m) => m.org_id === currentOrgId);

  return (
    <div className="min-h-screen flex bg-slate-50">
      <aside className="w-60 bg-white border-r border-slate-200 flex flex-col">
        <div className="p-6">
          <div className="font-bold text-slate-900">ZK2 RAG Platform</div>
          <OrgSwitcher
            current={currentOrg}
            memberships={memberships}
            onPick={(id) => setCurrentOrg(id)}
          />
        </div>
        <nav className="flex-1 px-3 space-y-1">
          {NAV.map((n) => {
            const Icon = n.icon;
            const active = pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                className={cn(
                  "flex items-center gap-3 px-3 py-2 rounded-md text-sm",
                  active
                    ? "bg-slate-900 text-white"
                    : "text-slate-700 hover:bg-slate-100",
                )}
              >
                <Icon className="size-4" /> {n.label}
              </Link>
            );
          })}
          {user.is_super_admin && (
            <Link
              href="/admin/access-requests"
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm",
                pathname.startsWith("/admin")
                  ? "bg-slate-900 text-white"
                  : "text-slate-700 hover:bg-slate-100",
              )}
            >
              <Shield className="size-4" /> Admin
            </Link>
          )}
        </nav>
        <div className="p-3 border-t border-slate-100">
          <div className="flex items-center justify-between px-3 py-2 text-sm">
            <div className="truncate">
              <div className="font-medium text-slate-900 truncate">
                {user.full_name ?? user.email}
              </div>
              <div className="text-xs text-slate-500 truncate">{user.email}</div>
            </div>
            <button
              onClick={() => {
                clear();
                router.push("/");
              }}
              title="Sign out"
              className="text-slate-500 hover:text-slate-900"
            >
              <LogOut className="size-4" />
            </button>
          </div>
        </div>
      </aside>
      <main className="flex-1 overflow-auto">
        <ErrorBanner />
        {children}
      </main>
    </div>
  );
}

function OrgSwitcher({
  current,
  memberships,
  onPick,
}: {
  current: { org_name: string; role: string } | undefined;
  memberships: Array<{ org_id: number; org_name: string; role: string }>;
  onPick: (id: number) => void;
}) {
  if (memberships.length === 0) {
    return <div className="mt-3 text-xs text-slate-500">No organizations</div>;
  }
  if (memberships.length === 1) {
    return (
      <div className="mt-3 text-xs text-slate-500 truncate">
        {current?.org_name} · {current?.role}
      </div>
    );
  }
  return (
    <div className="mt-3 relative">
      <select
        className="w-full text-xs bg-slate-50 border border-slate-200 rounded-md px-2 py-1.5 appearance-none pr-7"
        value={current?.org_name}
        onChange={(e) => {
          const m = memberships.find((x) => x.org_name === e.target.value);
          if (m) onPick(m.org_id);
        }}
      >
        {memberships.map((m) => (
          <option key={m.org_id} value={m.org_name}>
            {m.org_name} · {m.role}
          </option>
        ))}
      </select>
      <ChevronsUpDown className="size-3 text-slate-400 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none" />
    </div>
  );
}
