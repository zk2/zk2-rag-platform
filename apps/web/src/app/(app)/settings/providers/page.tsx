"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useCatalog } from "@/lib/catalog";

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
      <AllowanceBanner />
      <Card>
        <CardHeader><CardTitle>Configure</CardTitle></CardHeader>
        <CardContent className="space-y-6">
          {PROVIDERS.map((p) => (
            <ProviderRow key={p} provider={p} />
          ))}
        </CardContent>
      </Card>
      <EmbeddingCard />
    </div>
  );
}

type Allowance = {
  enabled: boolean;
  limit_tokens: number;
  used_tokens: number;
  remaining_tokens: number;
  exhausted: boolean;
  window_days: number;
  own_keys: string[];
};

function AllowanceBanner() {
  const allowance = useQuery({
    queryKey: ["key-allowance"],
    queryFn: () => api.get<Allowance>("/observability/allowance"),
  });
  const data = allowance.data;
  if (!data) return null;

  const share = data.limit_tokens > 0 ? (data.used_tokens / data.limit_tokens) * 100 : 100;
  const tone = data.exhausted
    ? "border-red-300 bg-red-50"
    : share > 80
      ? "border-amber-300 bg-amber-50"
      : "border-slate-200 bg-slate-50";

  return (
    <div className={`rounded border p-4 space-y-2 ${tone}`}>
      <div className="text-sm text-slate-900">
        {data.exhausted ? (
          <strong>The shared keys have served this organization their allowance.</strong>
        ) : (
          <>
            Shared keys: <strong>{data.used_tokens.toLocaleString()}</strong> of{" "}
            {data.limit_tokens.toLocaleString()} tokens used
            {data.window_days > 0 && ` in the last ${data.window_days} days`}.
          </>
        )}
      </div>
      <div className="h-1.5 rounded bg-white/70">
        <div
          className={data.exhausted ? "h-1.5 rounded-r bg-red-600" : "h-1.5 rounded-r bg-slate-800"}
          style={{ width: `${Math.min(Math.max(share, 1), 100)}%` }}
        />
      </div>
      <p className="text-xs text-slate-600">
        Keys you add below are used instead of the shared ones and have no allowance - the limit
        exists only because the shared keys are the deployment&apos;s, not yours.
        {data.own_keys.length > 0 && ` Your own keys: ${data.own_keys.join(", ")}.`}
      </p>
    </div>
  );
}

type EmbeddingSettings = {
  embedding_provider: string;
  embedding_model: string;
  dimensions: number;
  stale_sources: number;
  indexed_sources: number;
};

function EmbeddingCard() {
  const qc = useQueryClient();
  const catalog = useCatalog();
  const settings = useQuery({
    queryKey: ["embedding-settings"],
    queryFn: () => api.get<EmbeddingSettings>("/settings/embedding"),
    // While a reindex runs, sources move back to ready one by one
    refetchInterval: (q) => (q.state.data && q.state.data.stale_sources > 0 ? 5000 : false),
  });
  const [model, setModel] = useState<string | null>(null);
  const current = settings.data;
  const chosen = model ?? current?.embedding_model ?? "";
  const chosenSpec = catalog.data?.embedding.find((m) => m.id === chosen);

  const save = useMutation({
    mutationFn: () =>
      api.put<EmbeddingSettings>("/settings/embedding", {
        embedding_provider: chosenSpec?.provider ?? current?.embedding_provider ?? "openai",
        embedding_model: chosen,
      }),
    onSuccess: () => {
      setModel(null);
      qc.invalidateQueries({ queryKey: ["embedding-settings"] });
    },
  });

  const reindex = useMutation({
    mutationFn: (onlyStale: boolean) =>
      api.post<{ queued: number }>(`/settings/embedding/reindex?only_stale=${onlyStale}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["embedding-settings"] });
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });

  const dirty = chosen !== current?.embedding_model;
  const error = (save.error ?? reindex.error) as Error | null;

  return (
    <Card>
      <CardHeader><CardTitle>Embeddings</CardTitle></CardHeader>
      <CardContent className="space-y-4">
        <p className="text-sm text-slate-500">
          Retrieval only sees vectors built with the model configured here. Changing it does not
          rewrite the existing index - reindexing does, and it costs one embedding call per chunk.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
          <div className="md:col-span-2">
            <Label htmlFor="embedding-model">Model</Label>
            <select
              id="embedding-model"
              value={chosen}
              onChange={(e) => setModel(e.target.value)}
              className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
            >
              {catalog.data?.embedding.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name} ({m.native_dimensions}d)
                </option>
              ))}
              {!catalog.data && chosen && <option value={chosen}>{chosen}</option>}
            </select>
          </div>
          <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
            {save.isPending ? "Saving…" : "Save model"}
          </Button>
        </div>

        {current && (
          <div className="rounded border border-slate-200 bg-slate-50 p-3 text-sm space-y-2">
            <div className="text-slate-700">
              {current.indexed_sources} indexed source{current.indexed_sources === 1 ? "" : "s"}
              {current.stale_sources > 0 ? (
                <>
                  , <strong className="text-amber-700">{current.stale_sources}</strong> built with a
                  different model and invisible to search until reindexed.
                </>
              ) : (
                <> - all built with the current model.</>
              )}
            </div>
            <div className="flex gap-2">
              <Button
                onClick={() => reindex.mutate(true)}
                disabled={reindex.isPending || current.stale_sources === 0}
              >
                {reindex.isPending ? "Queueing…" : `Reindex ${current.stale_sources} stale`}
              </Button>
              <Button
                onClick={() => reindex.mutate(false)}
                disabled={reindex.isPending || current.indexed_sources === 0}
                className="bg-white text-slate-700 border border-slate-300 hover:bg-slate-50"
              >
                Reindex everything
              </Button>
            </div>
            {reindex.data && (
              <p className="text-xs text-slate-500">
                Queued {reindex.data.queued} source{reindex.data.queued === 1 ? "" : "s"}. Progress
                shows on the Sources page.
              </p>
            )}
          </div>
        )}

        {error && <p className="text-xs text-red-600">{error.message}</p>}
      </CardContent>
    </Card>
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
