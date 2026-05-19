"use client";

import Link from "next/link";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAuthStore } from "@/lib/auth-store";

export default function DashboardPage() {
  const user = useAuthStore((s) => s.user);
  if (!user) return null;
  return (
    <div className="p-8 space-y-6 max-w-5xl">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">
          Welcome, {user.full_name ?? user.email}
        </h1>
        <p className="text-slate-500 text-sm mt-1">
          Start by uploading sources, then build a bot that knows your content.
        </p>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <Card>
          <CardHeader><CardTitle>1. Configure provider</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-slate-600">Add your OpenAI API key (encrypted at rest).</p>
            <Link href="/settings/providers"><Button size="sm">Settings → Providers</Button></Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>2. Add sources</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-slate-600">Upload PDFs/DOCX/etc. or add URLs to ingest.</p>
            <Link href="/sources"><Button size="sm">Open Sources</Button></Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>3. Create a bot</CardTitle></CardHeader>
          <CardContent className="space-y-3">
            <p className="text-sm text-slate-600">Pick a model, write a system prompt, attach sources.</p>
            <Link href="/bots"><Button size="sm">Open Bots</Button></Link>
          </CardContent>
        </Card>
        <Card>
          <CardHeader><CardTitle>4. Chat</CardTitle></CardHeader>
          <CardContent>
            <p className="text-sm text-slate-600">Each bot has its own playground page.</p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
