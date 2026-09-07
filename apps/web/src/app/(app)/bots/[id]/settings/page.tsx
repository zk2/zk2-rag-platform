"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LabelWithHelp } from "@/components/ui/help-tip";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SourcePicker, type SourceNode } from "@/components/source-picker";
import { api } from "@/lib/api";
import { formatPrice, useCatalog } from "@/lib/catalog";
import { ArrowLeft, MessageSquare } from "lucide-react";

type Bot = {
  id: number;
  name: string;
  system_prompt: string | null;
  llm_provider: string;
  llm_model: string;
  temperature: number;
  num_k: number;
  source_ids: number[];
  current_version_id: number | null;
};

export default function BotSettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const botId = Number(id);
  const qc = useQueryClient();
  const catalog = useCatalog();

  const bot = useQuery({
    queryKey: ["bot", botId],
    queryFn: () => api.get<Bot>(`/bots/${botId}`),
  });
  const tree = useQuery({
    queryKey: ["sources", "tree"],
    queryFn: () => api.get<SourceNode[]>("/sources/tree"),
  });

  const [form, setForm] = useState<Bot | null>(null);
  useEffect(() => {
    if (bot.data && !form) setForm(bot.data);
  }, [bot.data, form]);

  const model = catalog.data?.chat.find((m) => m.id === form?.llm_model);

  const save = useMutation({
    mutationFn: () =>
      api.patch<Bot>(`/bots/${botId}`, {
        name: form?.name,
        system_prompt: form?.system_prompt,
        llm_provider: model?.provider ?? form?.llm_provider,
        llm_model: form?.llm_model,
        temperature: model && !model.supports_temperature ? 0 : form?.temperature,
        num_k: form?.num_k,
        source_ids: form?.source_ids,
      }),
    onSuccess: (updated) => {
      setForm(updated);
      qc.invalidateQueries({ queryKey: ["bot", botId] });
      qc.invalidateQueries({ queryKey: ["bots"] });
    },
  });

  if (bot.isLoading || !form) {
    return <div className="p-8 text-slate-500 text-sm">Loading…</div>;
  }
  if (bot.error) {
    return <div className="p-8 text-red-600 text-sm">{(bot.error as Error).message}</div>;
  }

  const patch = (fields: Partial<Bot>) => setForm({ ...form, ...fields });
  const toggleSource = (sourceId: number) =>
    patch({
      source_ids: form.source_ids.includes(sourceId)
        ? form.source_ids.filter((x) => x !== sourceId)
        : [...form.source_ids, sourceId],
    });

  return (
    <div className="p-8 max-w-3xl space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Link href="/bots" className="text-sm text-slate-500 hover:text-slate-900 inline-flex items-center gap-1">
            <ArrowLeft className="size-3.5" /> Bots
          </Link>
          <h1 className="text-2xl font-bold text-slate-900 mt-1">{form.name}</h1>
          <p className="text-slate-500 text-sm mt-1">
            Saving creates a new version; the previous one stays in history and can be restored.
          </p>
        </div>
        <Link href={`/bots/${botId}/chat`}>
          <Button variant="outline">
            <MessageSquare className="size-4 mr-1" /> Chat
          </Button>
        </Link>
      </div>

      <Card>
        <CardHeader><CardTitle>Behaviour</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div>
            <Label htmlFor="bot-name">Name</Label>
            <Input
              id="bot-name"
              value={form.name}
              onChange={(e) => patch({ name: e.target.value })}
            />
          </div>
          <div>
            <LabelWithHelp
              htmlFor="bot-prompt"
              label="System prompt"
              help="Who the bot is and how it should answer - role, tone, what to do when the sources disagree. Do not repeat the grounding and citation instructions here: they are appended automatically, and saying them twice measures the duplication rather than the prompt."
            />
            <Textarea
              id="bot-prompt"
              rows={6}
              value={form.system_prompt ?? ""}
              onChange={(e) => patch({ system_prompt: e.target.value })}
            />
            <p className="mt-1 text-[10px] text-slate-500">
              Retrieved passages and the citation instruction are appended automatically.
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Model</CardTitle></CardHeader>
        <CardContent className="space-y-4">
          <div>
            <LabelWithHelp
              htmlFor="bot-model"
              label="Model"
              help="The model that writes the answer from the retrieved passages. It is the largest part of both the wait and the bill; retrieval settings decide what it gets to read."
            />
            <select
              id="bot-model"
              value={form.llm_model}
              onChange={(e) => patch({ llm_model: e.target.value })}
              className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
            >
              {catalog.data?.chat.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.display_name} — {m.provider}
                </option>
              ))}
              {!catalog.data && <option value={form.llm_model}>{form.llm_model}</option>}
            </select>
            {model ? (
              <p className="mt-1 text-[10px] text-slate-500">
                {(model.context_window / 1000).toFixed(0)}k context · {formatPrice(model)}
                {model.supports_vision ? " · vision" : ""}
              </p>
            ) : (
              <p className="mt-1 text-[10px] text-amber-700">
                Not in the catalog: cost cannot be calculated for this model.
              </p>
            )}
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <LabelWithHelp
                htmlFor="bot-temp"
                label="Temperature"
                help="How much the model is allowed to vary its wording. Zero for answers grounded in documents, where the same question should give the same answer; higher only when variety is the point. Some models reject it entirely."
              />
              <Input
                id="bot-temp"
                type="number"
                step="0.1"
                min={0}
                max={2}
                value={form.temperature}
                disabled={!!model && !model.supports_temperature}
                onChange={(e) => patch({ temperature: Number(e.target.value) })}
              />
              {model && !model.supports_temperature && (
                <p className="mt-1 text-[10px] text-slate-500">
                  This model rejects sampling parameters, so temperature is not sent.
                </p>
              )}
            </div>
            <div>
              <LabelWithHelp
                htmlFor="bot-k"
                label="Passages retrieved (k)"
                help="How many passages reach the prompt. Too few and the answer is missing; too many and the right one is buried among near-misses, while every extra passage is paid for on every question. Five is a sensible start."
              />
              <Input
                id="bot-k"
                type="number"
                min={1}
                max={20}
                value={form.num_k}
                onChange={(e) => patch({ num_k: Number(e.target.value) })}
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle>Sources</CardTitle></CardHeader>
        <CardContent>
          <div className="max-h-60 overflow-auto border border-slate-200 rounded p-2 text-sm">
            {tree.data?.length ? (
              <SourcePicker
                nodes={tree.data}
                selected={form.source_ids}
                onToggle={toggleSource}
              />
            ) : (
              <span className="text-slate-500">No sources yet</span>
            )}
          </div>
          <p className="mt-2 text-[10px] text-slate-500">
            Selecting a folder includes everything under it.
          </p>
        </CardContent>
      </Card>

      <div className="flex items-center gap-3">
        <Button onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save as new version"}
        </Button>
        {save.isSuccess && (
          <span className="text-sm text-emerald-700">
            Saved · version {save.data?.current_version_id}
          </span>
        )}
        {save.error && <span className="text-sm text-red-600">{(save.error as Error).message}</span>}
      </div>
    </div>
  );
}
