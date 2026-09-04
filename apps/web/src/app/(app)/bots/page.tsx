"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { formatPrice, useCatalog } from "@/lib/catalog";
import { Trash2, MessageSquare } from "lucide-react";

type Bot = {
  id: number;
  name: string;
  system_prompt: string | null;
  llm_provider: string;
  llm_model: string;
  temperature: number;
  num_k: number;
  source_ids: number[];
};

type SourceNode = {
  id: number;
  type: "directory" | "file" | "web";
  name: string;
  status: string;
  children: SourceNode[];
};

export default function BotsPage() {
  return (
    <div className="p-8 max-w-5xl space-y-6">
      <h1 className="text-2xl font-bold text-slate-900">Bots</h1>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2"><BotsList /></div>
        <CreateBotCard />
      </div>
    </div>
  );
}

function BotsList() {
  const qc = useQueryClient();
  const list = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Bot[]>("/bots") });
  const del = useMutation({
    mutationFn: (id: number) => api.delete(`/bots/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["bots"] }),
  });
  return (
    <Card>
      <CardHeader><CardTitle>Your bots</CardTitle></CardHeader>
      <CardContent>
        {list.isLoading && <p className="text-slate-500 text-sm">Loading…</p>}
        {list.data && list.data.length === 0 && (
          <p className="text-slate-500 text-sm">No bots yet. Create one →</p>
        )}
        <ul className="divide-y divide-slate-100">
          {list.data?.map((b) => (
            <li key={b.id} className="py-3 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="font-medium text-slate-900">{b.name}</div>
                <div className="text-xs text-slate-500 mt-0.5">
                  {b.llm_provider}/{b.llm_model} · k={b.num_k} · sources={b.source_ids.length}
                </div>
              </div>
              <div className="flex gap-2">
                <Link href={`/bots/${b.id}/chat`}>
                  <Button size="sm" variant="outline">
                    <MessageSquare className="size-4 mr-1" /> Chat
                  </Button>
                </Link>
                <button
                  className="text-slate-400 hover:text-red-600"
                  onClick={() => confirm(`Delete bot "${b.name}"?`) && del.mutate(b.id)}
                >
                  <Trash2 className="size-4" />
                </button>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function CreateBotCard() {
  const qc = useQueryClient();
  const tree = useQuery({
    queryKey: ["sources", "tree"],
    queryFn: () => api.get<SourceNode[]>("/sources/tree"),
  });
  const [name, setName] = useState("");
  const [systemPrompt, setSystemPrompt] = useState(
    "You are a helpful assistant grounded in the attached sources.",
  );
  const catalog = useCatalog();
  const [model, setModel] = useState("gpt-4o-mini");
  const selectedModel = catalog.data?.chat.find((m) => m.id === model);
  const [numK, setNumK] = useState(5);
  const [selected, setSelected] = useState<number[]>([]);

  const create = useMutation({
    mutationFn: () =>
      api.post<Bot>("/bots", {
        name,
        system_prompt: systemPrompt,
        llm_provider: selectedModel?.provider ?? "openai",
        llm_model: model,
        temperature: 0,
        num_k: numK,
        source_ids: selected,
      }),
    onSuccess: () => {
      setName("");
      setSelected([]);
      qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });

  return (
    <Card>
      <CardHeader><CardTitle>Create bot</CardTitle></CardHeader>
      <CardContent className="space-y-3">
        <div>
          <Label htmlFor="bot-name">Name</Label>
          <Input id="bot-name" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="bot-prompt">System prompt</Label>
          <Textarea
            id="bot-prompt"
            rows={4}
            value={systemPrompt}
            onChange={(e) => setSystemPrompt(e.target.value)}
          />
        </div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <Label htmlFor="bot-model">Model</Label>
            <select
              id="bot-model"
              value={model}
              onChange={(e) => setModel(e.target.value)}
              className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
            >
              {catalog.data?.chat.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name}
                </option>
              ))}
              {!catalog.data && <option value={model}>{model}</option>}
            </select>
            {selectedModel && (
              <p className="mt-1 text-[10px] text-slate-500">
                {selectedModel.provider} · {(selectedModel.context_window / 1000).toFixed(0)}k
                context · {formatPrice(selectedModel)}
              </p>
            )}
          </div>
          <div>
            <Label htmlFor="bot-k">k</Label>
            <Input
              id="bot-k"
              type="number"
              value={numK}
              min={1}
              max={20}
              onChange={(e) => setNumK(Number(e.target.value))}
            />
          </div>
        </div>
        <div>
          <Label>Sources</Label>
          <div className="max-h-40 overflow-auto border border-slate-200 rounded p-2 text-sm space-y-1">
            {tree.data?.length ? (
              <SourcePicker
                nodes={tree.data}
                selected={selected}
                onToggle={(id) =>
                  setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]))
                }
                depth={0}
              />
            ) : (
              <span className="text-slate-500">No sources yet</span>
            )}
          </div>
        </div>
        <Button
          onClick={() => create.mutate()}
          disabled={!name || create.isPending}
          className="w-full"
        >
          {create.isPending ? "Creating…" : "Create bot"}
        </Button>
        {create.error && (
          <p className="text-xs text-red-600">{(create.error as Error).message}</p>
        )}
      </CardContent>
    </Card>
  );
}

function SourcePicker({
  nodes,
  selected,
  onToggle,
  depth,
}: {
  nodes: SourceNode[];
  selected: number[];
  onToggle: (id: number) => void;
  depth: number;
}) {
  return (
    <>
      {nodes.map((n) => (
        <div key={n.id}>
          <label
            className="flex items-center gap-2 cursor-pointer hover:bg-slate-50 py-0.5 px-1 rounded"
            style={{ paddingLeft: depth * 12 + 4 }}
          >
            <input
              type="checkbox"
              checked={selected.includes(n.id)}
              onChange={() => onToggle(n.id)}
            />
            <span className={n.type === "directory" ? "font-medium" : ""}>{n.name}</span>
            <span className="text-[10px] uppercase text-slate-400">{n.type}</span>
          </label>
          {n.children.length > 0 && (
            <SourcePicker
              nodes={n.children}
              selected={selected}
              onToggle={onToggle}
              depth={depth + 1}
            />
          )}
        </div>
      ))}
    </>
  );
}
