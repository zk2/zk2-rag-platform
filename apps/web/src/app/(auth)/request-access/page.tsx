"use client";

import { useState } from "react";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { api, ApiError } from "@/lib/api";

export default function RequestAccessPage() {
  const [email, setEmail] = useState("");
  const [message, setMessage] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      await api.post("/access-requests", { email, message }, { auth: false });
      setSubmitted(true);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Failed to submit request");
    } finally {
      setLoading(false);
    }
  }

  if (submitted) {
    return (
      <div>
        <h1 className="text-2xl font-bold mb-4">Request submitted</h1>
        <p className="text-slate-600">
          Thanks! We will email <strong>{email}</strong> once your access is approved.
        </p>
        <p className="mt-6 text-sm">
          <Link href="/" className="text-slate-900 underline">
            Back to home
          </Link>
        </p>
      </div>
    );
  }

  return (
    <>
      <h1 className="text-2xl font-bold mb-2">Request access</h1>
      <p className="text-sm text-slate-600 mb-6">
        ZK2 RAG Platform is invite-only. Tell us who you are and we&apos;ll get back shortly.
      </p>
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
        <div>
          <Label htmlFor="message">Message (optional)</Label>
          <Textarea
            id="message"
            maxLength={2000}
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Who you are, what you’d like to evaluate…"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        <Button type="submit" disabled={loading} className="w-full">
          {loading ? "Submitting…" : "Submit request"}
        </Button>
      </form>
    </>
  );
}
