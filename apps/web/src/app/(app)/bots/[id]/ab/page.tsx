"use client";

import Link from "next/link";
import { use, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LabelWithHelp } from "@/components/ui/help-tip";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Pipeline, PipelineVersion } from "@/lib/pipelines";
import { ArrowLeft, Play, Square, Trophy } from "lucide-react";

type Variant = {
  id: number;
  name: string;
  pipeline_version_id: number | null;
  traffic_percent: number;
  is_control: boolean;
};

type Experiment = {
  id: number;
  bot_id: number;
  name: string;
  status: string;
  started_at: string | null;
  stopped_at: string | null;
  variants: Variant[];
};

type VariantStats = {
  variant_id: number;
  name: string;
  is_control: boolean;
  traffic_percent: number;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: string;
};

export default function AbPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const botId = Number(id);
  const qc = useQueryClient();

  const experiments = useQuery({
    queryKey: ["experiments", botId],
    queryFn: () => api.get<Experiment[]>(`/experiments?bot_id=${botId}`),
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api.get<Pipeline[]>("/pipelines"),
  });
  const [pipelineId, setPipelineId] = useState<number | null>(null);
  const versions = useQuery({
    queryKey: ["pipeline-versions", pipelineId],
    queryFn: () => api.get<PipelineVersion[]>(`/pipelines/${pipelineId}/versions`),
    enabled: pipelineId !== null,
  });

  const [name, setName] = useState("Pipeline A/B");
  const [versionId, setVersionId] = useState<number | null>(null);
  const [split, setSplit] = useState(50);

  const create = useMutation({
    mutationFn: () =>
      api.post<Experiment>("/experiments", {
        bot_id: botId,
        name,
        variants: [
          { name: "control", traffic_percent: 100 - split, is_control: true },
          { name: "candidate", pipeline_version_id: versionId, traffic_percent: split },
        ],
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["experiments", botId] }),
  });
  const act = useMutation({
    mutationFn: ({ id: experimentId, action }: { id: number; action: string }) =>
      api.post(`/experiments/${experimentId}/${action}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["experiments", botId] }),
  });
  const promote = useMutation({
    mutationFn: ({ id: experimentId, variantId }: { id: number; variantId: number }) =>
      api.post(`/experiments/${experimentId}/promote`, { variant_id: variantId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["experiments", botId] });
      qc.invalidateQueries({ queryKey: ["bot", botId] });
    },
  });

  return (
    <div className="p-8 max-w-4xl space-y-6">
      <div>
        <Link
          href="/bots"
          className="text-sm text-slate-500 hover:text-slate-900 inline-flex items-center gap-1"
        >
          <ArrowLeft className="size-3.5" /> Bots
        </Link>
        <h1 className="text-2xl font-bold text-slate-900 mt-1">A/B experiments</h1>
        <p className="text-slate-500 text-sm mt-1">
          Split live traffic between the bot&apos;s current pipeline and a candidate version. One
          experiment runs at a time; a visitor keeps the variant they were given.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>New experiment</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div>
            <Label htmlFor="exp-name">Name</Label>
            <Input id="exp-name" value={name} onChange={(e) => setName(e.target.value)} />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div>
              <LabelWithHelp
                htmlFor="exp-pipeline"
                label="Candidate pipeline"
                help="The graph being tried out. Everyone not assigned to it keeps talking to the bot's current pipeline, which is the control - so the two are measured on the same live questions rather than on a golden set."
              />
              <select
                id="exp-pipeline"
                value={pipelineId ?? ""}
                onChange={(e) => {
                  setPipelineId(Number(e.target.value));
                  setVersionId(null);
                }}
                className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
              >
                <option value="">Choose…</option>
                {pipelines.data?.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <LabelWithHelp
                htmlFor="exp-version"
                label="Version"
                help="Which saved version of that pipeline the candidate serves. Pinning it means the experiment keeps measuring the same graph even if someone edits the pipeline while it runs."
              />
              <select
                id="exp-version"
                value={versionId ?? ""}
                onChange={(e) => setVersionId(Number(e.target.value))}
                disabled={!pipelineId}
                className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
              >
                <option value="">Choose…</option>
                {versions.data?.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.label || `Version ${v.id}`}
                    {v.is_current ? " (current)" : ""}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <LabelWithHelp
                htmlFor="exp-split"
                label={`Candidate traffic: ${split}%`}
                help="Share of conversations sent to the candidate. A conversation is assigned once and stays there, so a person does not see the pipeline change mid-chat. Start small: this is a change to what real users get, not a rehearsal."
              />
              <input
                id="exp-split"
                type="range"
                min={5}
                max={95}
                step={5}
                value={split}
                onChange={(e) => setSplit(Number(e.target.value))}
                className="w-full"
              />
              <p className="text-[10px] text-slate-500">Control gets {100 - split}%.</p>
            </div>
          </div>
          <Button onClick={() => create.mutate()} disabled={!versionId || create.isPending}>
            {create.isPending ? "Creating…" : "Create experiment"}
          </Button>
          {create.error && <p className="text-xs text-red-600">{(create.error as Error).message}</p>}
        </CardContent>
      </Card>

      {experiments.data?.map((experiment) => (
        <ExperimentCard
          key={experiment.id}
          experiment={experiment}
          onAction={(action) => act.mutate({ id: experiment.id, action })}
          onPromote={(variantId) => promote.mutate({ id: experiment.id, variantId })}
          error={(act.error ?? promote.error) as Error | null}
        />
      ))}
    </div>
  );
}

function ExperimentCard({
  experiment,
  onAction,
  onPromote,
  error,
}: {
  experiment: Experiment;
  onAction: (action: string) => void;
  onPromote: (variantId: number) => void;
  error: Error | null;
}) {
  const stats = useQuery({
    queryKey: ["experiment-stats", experiment.id],
    queryFn: () => api.get<VariantStats[]>(`/experiments/${experiment.id}/stats`),
    refetchInterval: experiment.status === "running" ? 10000 : false,
  });
  const maxCalls = Math.max(...(stats.data ?? []).map((s) => s.calls), 1);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>
            {experiment.name}
            <span
              className={
                experiment.status === "running"
                  ? "ml-2 text-xs font-normal text-emerald-700"
                  : "ml-2 text-xs font-normal text-slate-500"
              }
            >
              {experiment.status}
            </span>
          </CardTitle>
          <div className="flex gap-2">
            {experiment.status !== "running" && experiment.status !== "stopped" && (
              <Button size="sm" onClick={() => onAction("start")}>
                <Play className="size-4 mr-1" /> Start
              </Button>
            )}
            {experiment.status === "running" && (
              <Button size="sm" variant="outline" onClick={() => onAction("stop")}>
                <Square className="size-4 mr-1" /> Stop
              </Button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
              <th className="pb-2 font-medium">Variant</th>
              <th className="pb-2 font-medium text-right">Split</th>
              <th className="pb-2 font-medium text-right">Turns</th>
              <th className="pb-2 font-medium text-right">Tokens</th>
              <th className="pb-2 font-medium text-right">Spend</th>
              <th className="pb-2 font-medium w-28" />
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {(stats.data ?? []).map((row) => (
              <tr key={row.variant_id}>
                <td className="py-2">
                  <div className="text-slate-900">{row.name}</div>
                  {row.is_control && <div className="text-[10px] text-slate-500">control</div>}
                </td>
                <td className="py-2 text-right tabular-nums">{row.traffic_percent}%</td>
                <td className="py-2 text-right tabular-nums">
                  <div className="flex items-center justify-end gap-2">
                    <span
                      className="h-1.5 rounded-r bg-slate-800 inline-block"
                      style={{ width: `${Math.max((row.calls / maxCalls) * 60, 2)}px` }}
                    />
                    {row.calls}
                  </div>
                </td>
                <td className="py-2 text-right tabular-nums">{row.tokens_in + row.tokens_out}</td>
                <td className="py-2 text-right tabular-nums">${row.cost_usd}</td>
                <td className="py-2 text-right">
                  {!row.is_control && experiment.status !== "stopped" && (
                    <Button size="sm" variant="outline" onClick={() => onPromote(row.variant_id)}>
                      <Trophy className="size-4 mr-1" /> Promote
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="mt-2 text-[10px] text-slate-500">
          Turns and spend come from usage events, so these are the same numbers billing sees.
        </p>
        {error && <p className="mt-2 text-xs text-red-600">{error.message}</p>}
      </CardContent>
    </Card>
  );
}
