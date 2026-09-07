"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { HelpTip, LabelWithHelp } from "@/components/ui/help-tip";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { FlaskConical, Loader2, Play, Trash2, Upload } from "lucide-react";

type Dataset = { id: number; name: string; description: string | null; item_count: number };
type Metric = { name: string; needs_judge: boolean; doc: string };
type Run = {
  id: number;
  dataset_id: number;
  bot_id: number | null;
  label: string | null;
  status: string;
  error: string | null;
  items_total: number;
  items_done: number;
  summary: Record<string, number>;
  cost_usd: string | null;
  duration_ms: number | null;
  is_baseline: boolean;
  pipeline_version_id: number | null;
  created_at: string;
};
type Comparison = {
  baseline_run_id: number;
  candidate_run_id: number;
  metrics: Record<string, { baseline: number; candidate: number; delta: number }>;
  regressions: string[];
  threshold: number;
};
type Bot = { id: number; name: string };
type Pipeline = { id: number; name: string };
type PipelineVersion = { id: number; label: string | null; is_current: boolean };

export default function EvalsPage() {
  const [selected, setSelected] = useState<number | null>(null);
  return (
    <div className="p-8 max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Evals</h1>
        <p className="text-slate-500 text-sm mt-1">
          A golden set run through the real pipeline. Deterministic metrics are free; judge
          metrics cost one model call per item.
        </p>
      </div>
      <Datasets selected={selected} onSelect={setSelected} />
      {selected !== null && <Runs datasetId={selected} />}
    </div>
  );
}

function Datasets({
  selected,
  onSelect,
}: {
  selected: number | null;
  onSelect: (id: number) => void;
}) {
  const qc = useQueryClient();
  const datasets = useQuery({
    queryKey: ["eval-datasets"],
    queryFn: () => api.get<Dataset[]>("/evals/datasets"),
  });
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: () => api.post<Dataset>("/evals/datasets", { name }),
    onSuccess: (created) => {
      setName("");
      qc.invalidateQueries({ queryKey: ["eval-datasets"] });
      onSelect(created.id);
    },
  });
  const upload = useMutation({
    mutationFn: ({ id, file }: { id: number; file: File }) =>
      api.upload<{ imported: number; skipped: number; errors: string[] }>(
        `/evals/datasets/${id}/import`,
        file,
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-datasets"] }),
  });
  // Importing twice appends rather than replaces, so a dataset that has taken
  // a corrected file needs a way out. The API has always allowed it.
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/evals/datasets/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-datasets"] }),
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Datasets</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex gap-3 items-end">
          <div className="flex-1">
            <LabelWithHelp
              htmlFor="ds-name"
              label="New dataset"
              help="A golden set: questions whose answers you already know, with the document each should come from. It is what turns a change to the pipeline from an opinion into a measurement."
            />
            <Input
              id="ds-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Support questions"
            />
          </div>
          <Button onClick={() => create.mutate()} disabled={!name || create.isPending}>
            Create
          </Button>
        </div>

        <ul className="divide-y divide-slate-100">
          {datasets.data?.map((dataset) => (
            <li key={dataset.id} className="py-3 flex items-center justify-between gap-3">
              <button className="text-left min-w-0" onClick={() => onSelect(dataset.id)}>
                <div
                  className={
                    selected === dataset.id
                      ? "font-medium text-slate-900"
                      : "font-medium text-slate-700"
                  }
                >
                  {dataset.name}
                </div>
                <div className="text-xs text-slate-500">
                  {dataset.item_count} items
                  {selected === dataset.id && " · selected"}
                </div>
              </button>
              <label className="text-xs text-slate-600 inline-flex items-center gap-1 cursor-pointer hover:text-slate-900">
                <Upload className="size-4" />
                Import CSV/JSON
                <input
                  type="file"
                  className="hidden"
                  accept=".csv,.json"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (file) upload.mutate({ id: dataset.id, file });
                  }}
                />
              </label>
              <button
                className="text-slate-400 hover:text-red-600"
                title="Delete this dataset and its runs"
                disabled={remove.isPending}
                onClick={() => {
                  if (confirm(`Delete "${dataset.name}" with its items and runs?`))
                    remove.mutate(dataset.id);
                }}
              >
                <Trash2 className="size-4" />
              </button>
            </li>
          ))}
        </ul>
        {datasets.data?.length === 0 && (
          <p className="text-sm text-slate-500">
            No datasets yet. A CSV with question, expected_answer, expected_sources, tags works.
          </p>
        )}
        {remove.error && (
          <p className="text-xs text-red-600">{(remove.error as Error).message}</p>
        )}
        {upload.data && (
          <p className="text-xs text-slate-600">
            Imported {upload.data.imported}, skipped {upload.data.skipped}
            {upload.data.errors.length > 0 && `: ${upload.data.errors[0]}`}
          </p>
        )}
        {upload.error && <p className="text-xs text-red-600">{(upload.error as Error).message}</p>}
      </CardContent>
    </Card>
  );
}

/**
 * Which graph the run executes. Left alone it follows the bot, which is what a
 * chat turn does; pinned to a version it runs that graph instead, so two
 * versions can be measured on the same questions with the same bot.
 */
function usePipelineVersions(pipelines: Pipeline[]) {
  return useQuery({
    queryKey: ["pipeline-versions", pipelines.map((p) => p.id)],
    enabled: pipelines.length > 0,
    queryFn: async () => {
      const lists = await Promise.all(
        pipelines.map(async (pipeline) => {
          const rows = await api.get<PipelineVersion[]>(`/pipelines/${pipeline.id}/versions`);
          return rows.map((version) => ({ pipeline, version }));
        }),
      );
      return lists.flat();
    },
  });
}

/** The name a person saw when they saved it, not the row id they never see. */
function versionName(
  versions: { pipeline: Pipeline; version: PipelineVersion }[] | undefined,
  versionId: number,
): string {
  const found = versions?.find((v) => v.version.id === versionId);
  if (!found) return `version ${versionId}`;
  return `${found.pipeline.name} - ${found.version.label ?? `version ${versionId}`}`;
}

/** The graph a given run executed, said the way the rest of the screen says it. */
function runVersionName(
  runs: Run[] | undefined,
  versions: { pipeline: Pipeline; version: PipelineVersion }[] | undefined,
  runId: number,
): string {
  const run = runs?.find((r) => r.id === runId);
  if (!run) return `run ${runId}`;
  if (run.pipeline_version_id === null) return "the bot's own pipeline";
  return versionName(versions, run.pipeline_version_id);
}

function PipelineVersionSelect({
  pipelines,
  value,
  onChange,
}: {
  pipelines: Pipeline[];
  value: number | null;
  onChange: (id: number | null) => void;
}) {
  const versions = usePipelineVersions(pipelines);

  return (
    <select
      id="run-version"
      value={value ?? ""}
      onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
      className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
    >
      <option value="">The bot&apos;s own pipeline</option>
      {versions.data?.map(({ pipeline, version }) => (
        <option key={version.id} value={version.id}>
          {pipeline.name} - {version.label ?? `v${version.id}`}
          {version.is_current ? " (current)" : ""}
        </option>
      ))}
    </select>
  );
}

/** A run that has been queued or is executing, as opposed to finished. */
function isWorking(run: Run): boolean {
  return run.status === "pending" || run.status === "running";
}

/**
 * A run takes tens of seconds and the row was static text while it did.
 * Nothing said whether the work had started, stalled or finished, so the only
 * way to find out was to reload the page and hope.
 *
 * Queued and running look different on purpose: a bar that slides has no
 * numbers behind it and must not pretend otherwise, while a run in progress
 * fills its bar by items actually scored.
 */
function RunProgress({ run }: { run: Run }) {
  const done = run.items_done;
  const total = run.items_total;
  const known = run.status === "running" && total > 0;
  const percent = known ? Math.max(4, Math.round((done / total) * 100)) : 0;

  return (
    <div className="mt-1.5 w-56 max-w-full">
      <div className="h-1 overflow-hidden rounded-full bg-slate-200">
        {known ? (
          <div
            className="h-full rounded-full bg-slate-700 transition-[width] duration-500 ease-out"
            style={{ width: `${percent}%` }}
          />
        ) : (
          <div className="h-full w-1/3 animate-[indeterminate-slide_1.4s_ease-in-out_infinite] rounded-full bg-slate-400" />
        )}
      </div>
      <div className="mt-1 text-[10px] text-slate-500">
        {known ? `${done} of ${total} questions` : "waiting for a worker"}
      </div>
    </div>
  );
}

function Runs({ datasetId }: { datasetId: number }) {
  const qc = useQueryClient();
  const runs = useQuery({
    queryKey: ["eval-runs", datasetId],
    queryFn: () => api.get<Run[]>(`/evals/runs?dataset_id=${datasetId}`),
    refetchInterval: (q) =>
      q.state.data?.some((r) => r.status === "pending" || r.status === "running") ? 3000 : false,
  });
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<Bot[]>("/bots") });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api.get<Pipeline[]>("/pipelines"),
  });
  const metrics = useQuery({
    queryKey: ["eval-metrics"],
    queryFn: () => api.get<Metric[]>("/evals/metrics"),
  });

  const versions = usePipelineVersions(pipelines.data ?? []);
  const [botId, setBotId] = useState<number | null>(null);
  // Pinning a version is how two graphs are compared on one dataset: same
  // questions, same bot, one node different.
  const [versionId, setVersionId] = useState<number | null>(null);
  const [chosen, setChosen] = useState<string[]>([]);
  const [baseline, setBaseline] = useState(false);
  const [compareWith, setCompareWith] = useState<[number, number] | null>(null);

  const start = useMutation({
    mutationFn: () =>
      api.post<Run>(`/evals/datasets/${datasetId}/runs`, {
        bot_id: botId ?? bots.data?.[0]?.id,
        pipeline_version_id: versionId,
        metrics: chosen.length ? chosen : metrics.data?.filter((m) => !m.needs_judge).map((m) => m.name),
        mark_baseline: baseline,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-runs", datasetId] }),
  });

  const comparison = useQuery({
    queryKey: ["eval-comparison", compareWith],
    queryFn: () =>
      api.get<Comparison>(`/evals/runs/${compareWith![0]}/compare/${compareWith![1]}`),
    enabled: compareWith !== null,
  });

  const judgeChosen = chosen.some((name) => metrics.data?.find((m) => m.name === name)?.needs_judge);

  return (
    <>
      <Card>
        <CardHeader>
          <CardTitle>New run</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <div>
              <Label htmlFor="run-bot">Bot</Label>
              <select
                id="run-bot"
                value={botId ?? bots.data?.[0]?.id ?? ""}
                onChange={(e) => setBotId(Number(e.target.value))}
                className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
              >
                {bots.data?.map((b) => (
                  <option key={b.id} value={b.id}>
                    {b.name}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <LabelWithHelp
                htmlFor="run-version"
                label="Pipeline version"
                help="Which graph this run executes. Left on the bot's own pipeline it measures what people are actually talking to; pinned to a version it measures that graph instead - which is how two versions are compared on the same questions without changing what the bot serves."
              />
              <PipelineVersionSelect
                pipelines={pipelines.data ?? []}
                value={versionId}
                onChange={setVersionId}
              />
            </div>
            <div className="md:col-span-2">
              <Label>
                Metrics
                <HelpTip>
                  Deterministic metrics are computed from what the run already produced and cost
                  nothing. The ones marked $ ask a model to judge each answer: one extra call per
                  question, per metric.
                </HelpTip>
              </Label>
              <div className="flex flex-wrap gap-2 mt-1">
                {metrics.data?.map((metric) => {
                  const active = chosen.includes(metric.name);
                  return (
                    <button
                      key={metric.name}
                      title={metric.doc}
                      onClick={() =>
                        setChosen((current) =>
                          active
                            ? current.filter((n) => n !== metric.name)
                            : [...current, metric.name],
                        )
                      }
                      className={
                        active
                          ? "text-xs px-2 py-1 rounded bg-slate-900 text-white"
                          : "text-xs px-2 py-1 rounded border border-slate-200 text-slate-600"
                      }
                    >
                      {metric.name}
                      {metric.needs_judge && <span className="ml-1 opacity-70">$</span>}
                    </button>
                  );
                })}
              </div>
            </div>
          </div>
          <label className="flex items-center gap-2 text-sm text-slate-700">
            <input type="checkbox" checked={baseline} onChange={(e) => setBaseline(e.target.checked)} />
            Mark as baseline
          </label>
          {judgeChosen && (
            <p className="text-xs text-amber-700">
              Judge metrics call a model once per item, on top of the pipeline turn itself.
            </p>
          )}
          <Button onClick={() => start.mutate()} disabled={start.isPending}>
            <Play className="size-4 mr-1" />
            {start.isPending ? "Queueing…" : "Run"}
          </Button>
          {start.error && <p className="text-xs text-red-600">{(start.error as Error).message}</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Runs</CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {runs.data?.length === 0 && <p className="text-sm text-slate-500">No runs yet.</p>}
          <ul className="divide-y divide-slate-100">
            {runs.data?.map((run) => (
              <li key={run.id} className="py-3">
                <div className="flex items-center justify-between gap-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2 text-sm text-slate-900">
                      {isWorking(run) && (
                        <Loader2 className="size-3.5 shrink-0 animate-spin text-slate-500" />
                      )}
                      <span>{run.label || `Run ${run.id}`}</span>
                      {run.is_baseline && (
                        <span className="text-[10px] uppercase text-emerald-700">baseline</span>
                      )}
                    </div>
                    <div className="text-xs text-slate-500">
                      {run.status}
                      {run.status === "running" && ` · ${run.items_done}/${run.items_total}`}
                      {run.duration_ms !== null && ` · ${(run.duration_ms / 1000).toFixed(1)}s`}
                      {run.cost_usd && ` · $${run.cost_usd}`}
                      {/* Two runs that differ only by graph are otherwise
                          indistinguishable in this list */}
                      {run.pipeline_version_id !== null &&
                        ` · ${versionName(versions.data, run.pipeline_version_id)}`}
                    </div>
                    {isWorking(run) && <RunProgress run={run} />}
                    {run.error && <div className="text-xs text-red-600">{run.error}</div>}
                  </div>
                  <div className="flex gap-1">
                    {runs.data
                      ?.filter((other) => other.id !== run.id && other.status === "done")
                      .slice(0, 1)
                      .map((other) => (
                        <Button
                          key={other.id}
                          size="sm"
                          variant="outline"
                          title={`Compare with ${other.label || `Run ${other.id}`}${
                            other.pipeline_version_id !== null
                              ? ` (${versionName(versions.data, other.pipeline_version_id)})`
                              : ""
                          }`}
                          onClick={() => setCompareWith([other.id, run.id])}
                        >
                          {/* "vs 10" meant nothing: a run id, next to a version
                              label, under a version selector - three numbering
                              schemes on one screen and no way to tell them apart */}
                          <FlaskConical className="size-4 mr-1" /> vs {other.label || `Run ${other.id}`}
                        </Button>
                      ))}
                  </div>
                </div>
                <MetricBars summary={run.summary} />
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      {comparison.data && (
        <Card>
          <CardHeader>
            <CardTitle>
              Run {comparison.data.baseline_run_id} vs {comparison.data.candidate_run_id}
            </CardTitle>
            {/* Which graph each side ran. Two runs of the same dataset differ
                only by that, and the numbers mean nothing without it. */}
            <div className="mt-1 grid grid-cols-1 gap-0.5 text-xs text-slate-500 sm:grid-cols-2">
              <div>
                <span className="uppercase tracking-wide">Baseline</span> -{" "}
                {runVersionName(runs.data, versions.data, comparison.data.baseline_run_id)}
              </div>
              <div>
                <span className="uppercase tracking-wide">Candidate</span> -{" "}
                {runVersionName(runs.data, versions.data, comparison.data.candidate_run_id)}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {comparison.data.regressions.length > 0 ? (
              <p className="text-sm text-red-700 mb-2">
                Regressed past {comparison.data.threshold}:{" "}
                {comparison.data.regressions.join(", ")}
              </p>
            ) : (
              <p className="text-sm text-emerald-700 mb-2">No metric regressed past the threshold.</p>
            )}
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                  <th className="pb-2 font-medium">Metric</th>
                  <th className="pb-2 font-medium text-right">Baseline</th>
                  <th className="pb-2 font-medium text-right">Candidate</th>
                  <th className="pb-2 font-medium text-right">Delta</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {Object.entries(comparison.data.metrics).map(([name, row]) => (
                  <tr key={name}>
                    <td className="py-1.5 text-slate-900">{name}</td>
                    <td className="py-1.5 text-right tabular-nums">{row.baseline.toFixed(3)}</td>
                    <td className="py-1.5 text-right tabular-nums">{row.candidate.toFixed(3)}</td>
                    <td
                      className={
                        row.delta < 0
                          ? "py-1.5 text-right tabular-nums text-red-700"
                          : "py-1.5 text-right tabular-nums text-emerald-700"
                      }
                    >
                      {row.delta > 0 ? "+" : ""}
                      {row.delta.toFixed(3)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>
      )}
    </>
  );
}

function MetricBars({ summary }: { summary: Record<string, number> }) {
  const entries = Object.entries(summary);
  if (entries.length === 0) return null;
  return (
    <div className="mt-2 space-y-1">
      {entries.map(([name, value]) => (
        <div key={name} className="flex items-center gap-2 text-[11px] text-slate-600">
          <span className="w-32 truncate">{name}</span>
          {/* Every metric is 0..1, so one scale and one hue is the whole story */}
          <span className="flex-1 h-1.5 bg-slate-100 rounded">
            <span
              className="block h-1.5 rounded-r bg-slate-800"
              style={{ width: `${Math.max(value * 100, 1)}%` }}
            />
          </span>
          <span className="tabular-nums w-10 text-right">{value.toFixed(2)}</span>
        </div>
      ))}
    </div>
  );
}
