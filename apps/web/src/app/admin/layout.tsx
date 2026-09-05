"use client";

import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuthHydrated, useAuthStore } from "@/lib/auth-store";
import { ErrorBanner } from "@/components/error-banner";

export default function AdminLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const hydrated = useAuthHydrated();

  useEffect(() => {
    // Same reason as the app shell: before hydration "no user" only means the
    // persisted session has not been read back yet
    if (!hydrated) return;
    const current = useAuthStore.getState().user;
    if (!current) router.replace("/login");
    else if (!current.is_super_admin) router.replace("/dashboard");
  }, [hydrated, user, router]);

  if (!hydrated || !user || !user.is_super_admin) return null;

  return (
    <div className="min-h-screen flex bg-slate-50">
      <aside className="w-60 bg-white border-r border-slate-200 p-6">
        <div className="font-bold text-slate-900 mb-1">ZK2 RAG Platform</div>
        <div className="text-xs text-slate-500 mb-8">Super-admin</div>
        <nav className="flex flex-col gap-2 text-sm">
          <Link href="/admin/access-requests" className="text-slate-700 hover:text-slate-900">
            Access requests
          </Link>
          <Link href="/admin/invites" className="text-slate-700 hover:text-slate-900">
            Invites
          </Link>
          {/* Signing in as a super-admin lands here, and the admin pages are
              only about who gets in - the documents, bots and pipelines live in
              the workspace. Without this the trip is one-way. */}
          <Link
            href="/dashboard"
            className="mt-4 pt-4 border-t border-slate-100 flex items-center gap-2 text-slate-500 hover:text-slate-900"
          >
            <ArrowLeft className="size-4" /> Back to workspace
          </Link>
        </nav>
      </aside>
      <main className="flex-1">
        <ErrorBanner />
        <div className="p-8">{children}</div>
      </main>
    </div>
  );
}
