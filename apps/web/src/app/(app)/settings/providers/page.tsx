"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

type Provider = {
  id: number;
  provider: string;
  has_key: boolean;
  custom_base_url: string | null;
  updated_at: string;
};

const PROVIDERS = ["openai", "anthropic", "gemini", "ollama"] as const;

export default function ProvidersPage() {
  return (
    <div className="p-8 max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">LLM providers</h1>
        <p className="text-slate-500 text-sm mt-1">
          Keys are encrypted at rest with Fernet. They are write-only — never returned by the API.
        </p>
      </div>
      <Card>
        <CardHeader><CardTitle>Configure</CardTitle></CardHeader>
        <CardContent className="space-y-6">
          {PROVIDERS.map((p) => (
            <ProviderRow key={p} provider={p} />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function ProviderRow({ provider }: { provider: string }) {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["providers"],
    queryFn: () => api.get<Provider[]>("/settings/providers"),
  });
  const current = list.data?.find((x) => x.provider === provider);
  const [key, setKey] = useState("");
  const [url, setUrl] = useState(current?.custom_base_url ?? "");
  const save = useMutation({
    mutationFn: () =>
      api.put<Provider>(`/settings/providers/${provider}`, {
        api_key: key || undefined,
        custom_base_url: url || null,
      }),
    onSuccess: () => {
      setKey("");
      qc.invalidateQueries({ queryKey: ["providers"] });
    },
  });

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end pb-4 border-b border-slate-100 last:border-0">
      <div>
        <div className="font-medium text-slate-900 capitalize">{provider}</div>
        <div className="text-xs text-slate-500">
          {current?.has_key ? "Key configured ✓" : "No key"}
          {current?.updated_at && current.has_key && (
            <> · {new Date(current.updated_at).toLocaleDateString()}</>
          )}
        </div>
      </div>
      <div>
        <Label htmlFor={`${provider}-key`}>
          {current?.has_key ? "Replace key" : "API key"}
        </Label>
        <Input
          id={`${provider}-key`}
          type="password"
          placeholder="sk-..."
          value={key}
          onChange={(e) => setKey(e.target.value)}
        />
      </div>
      <div className="flex gap-2">
        {provider === "ollama" && (
          <Input
            placeholder="Base URL"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
        )}
        <Button onClick={() => save.mutate()} disabled={save.isPending || !key}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
