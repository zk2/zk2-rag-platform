import Link from "next/link";
import { Button } from "@/components/ui/button";

export default function Home() {
  return (
    <main className="min-h-screen flex items-center justify-center px-6">
      <div className="max-w-3xl text-center">
        <h1 className="text-5xl font-bold tracking-tight text-slate-900">
          ZK2 RAG Platform
        </h1>
        <p className="mt-6 text-lg text-slate-600">
          Production-grade multi-tenant RAG platform with agents, visual pipeline builder,
          A/B experimentation, evals and full observability.
        </p>
        <div className="mt-10 flex justify-center gap-3">
          <Link href="/login">
            <Button>Sign in</Button>
          </Link>
          <Link href="/request-access">
            <Button variant="outline">Request access</Button>
          </Link>
        </div>
        <p className="mt-12 text-sm text-slate-500">
          Access is invite-only. Demo by Zeka.
        </p>
      </div>
    </main>
  );
}
