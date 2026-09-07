"use client";

import { use, useCallback, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  addEdge,
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  type Connection,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import Link from "next/link";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api } from "@/lib/api";
import {
  defaultConfig,
  fieldType,
  schemaFields,
  useNodeTypes,
  type Dag,
  type NodeType,
  type PipelineDetail,
  type PipelineVersion,
  type TestRun,
} from "@/lib/pipelines";
import { ArrowLeft, History, Play, Save, Trash2 } from "lucide-react";

type BotSummary = { id: number; name: string };

export default function PipelineEditorPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const pipelineId = Number(id);
  const qc = useQueryClient();

  const pipeline = useQuery({
    queryKey: ["pipeline", pipelineId],
    queryFn: () => api.get<PipelineDetail>(`/pipelines/${pipelineId}`),
  });
  const palette = useNodeTypes();
  const bots = useQuery({ queryKey: ["bots"], queryFn: () => api.get<BotSummary[]>("/bots") });
  const versions = useQuery({
    queryKey: ["pipeline-versions", pipelineId],
    queryFn: () => api.get<PipelineVersion[]>(`/pipelines/${pipelineId}/versions`),
  });

  const [nodes, setNodes, onNodesChange] = useNodesState<Node>([]);
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [question, setQuestion] = useState("What do the sources say?");
  const [botId, setBotId] = useState<number | null>(null);
  const [showVersions, setShowVersions] = useState(false);

  // Load the saved graph into the canvas once
  useEffect(() => {
    if (!pipeline.data || nodes.length > 0) return;
    setNodes(
      pipeline.data.dag.nodes.map((n, index) => ({
        id: n.id,
        position: n.position ?? { x: 260 * index, y: 80 },
        data: { label: n.id, nodeType: n.type, config: n.config },
        type: "default",
      })),
    );
    setEdges(
      pipeline.data.dag.edges.map(([source, target]) => ({
        id: `${source}->${target}`,
        source,
        target,
        animated: true,
      })),
    );
  }, [pipeline.data, nodes.length, setNodes, setEdges]);

  useEffect(() => {
    if (bots.data?.length && botId === null) setBotId(bots.data[0].id);
  }, [bots.data, botId]);

  const currentDag: Dag = useMemo(
    () => ({
      nodes: nodes.map((n) => ({
        id: n.id,
        type: String(n.data.nodeType),
        config: (n.data.config ?? {}) as Record<string, unknown>,
        position: { x: Math.round(n.position.x), y: Math.round(n.position.y) },
      })),
      edges: edges.map((e) => [e.source, e.target] as [string, string]),
    }),
    [nodes, edges],
  );

  const save = useMutation({
    mutationFn: (label: string | null) =>
      api.put(`/pipelines/${pipelineId}/dag`, { dag: currentDag, label }),
    onSuccess: () => {
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["pipeline", pipelineId] });
      qc.invalidateQueries({ queryKey: ["pipeline-versions", pipelineId] });
    },
  });

  const testRun = useMutation({
    mutationFn: () =>
      api.post<TestRun>(`/pipelines/${pipelineId}/test`, {
        bot_id: botId,
        question,
        dag: currentDag,
      }),
  });

  const activate = useMutation({
    mutationFn: (versionId: number) =>
      api.post(`/pipelines/${pipelineId}/versions/${versionId}/activate`),
    onSuccess: () => {
      setNodes([]);
      setEdges([]);
      setDirty(false);
      qc.invalidateQueries({ queryKey: ["pipeline", pipelineId] });
      qc.invalidateQueries({ queryKey: ["pipeline-versions", pipelineId] });
    },
  });

  const onConnect = useCallback(
    (connection: Connection) => {
      setEdges((eds) => addEdge({ ...connection, animated: true }, eds));
      setDirty(true);
    },
    [setEdges],
  );

  const addNode = (nodeType: NodeType) => {
    const base = nodeType.type.replace(/[^a-z0-9]/gi, "_");
    let suffix = 1;
    let nodeId = base;
    while (nodes.some((n) => n.id === nodeId)) nodeId = `${base}_${++suffix}`;
    setNodes((current) => [
      ...current,
      {
        id: nodeId,
        position: { x: 120 + current.length * 40, y: 280 },
        data: {
          label: nodeId,
          nodeType: nodeType.type,
          config: defaultConfig(nodeType.config_schema),
        },
        type: "default",
      },
    ]);
    setSelectedId(nodeId);
    setDirty(true);
  };

  const updateConfig = (nodeId: string, key: string, value: unknown) => {
    setNodes((current) =>
      current.map((n) =>
        n.id === nodeId
          ? { ...n, data: { ...n.data, config: { ...(n.data.config as object), [key]: value } } }
          : n,
      ),
    );
    setDirty(true);
  };

  const removeNode = (nodeId: string) => {
    setNodes((current) => current.filter((n) => n.id !== nodeId));
    setEdges((current) => current.filter((e) => e.source !== nodeId && e.target !== nodeId));
    setSelectedId(null);
    setDirty(true);
  };

  const selected = nodes.find((n) => n.id === selectedId);
  const selectedSchema = palette.data?.find((t) => t.type === selected?.data.nodeType);

  if (pipeline.isLoading) return <div className="p-8 text-sm text-slate-500">Loading…</div>;
  if (pipeline.error) {
    return <div className="p-8 text-sm text-red-600">{(pipeline.error as Error).message}</div>;
  }

  return (
    <div className="flex flex-col h-[calc(100vh-0px)]">
      <header className="px-6 py-3 border-b border-slate-200 flex items-center justify-between gap-4">
        <div className="min-w-0">
          <Link
            href="/pipelines"
            className="text-xs text-slate-500 hover:text-slate-900 inline-flex items-center gap-1"
          >
            <ArrowLeft className="size-3" /> Pipelines
          </Link>
          <h1 className="text-lg font-semibold text-slate-900 truncate">
            {pipeline.data?.name}
            {dirty && <span className="ml-2 text-xs font-normal text-amber-700">unsaved</span>}
          </h1>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={() => setShowVersions((v) => !v)}
            title={`${pipeline.data?.version_count ?? 0} versions`}
          >
            <History className="size-4 mr-1" /> Versions
          </Button>
          <Button
            onClick={() => save.mutate(window.prompt("Label for this version (optional)") || null)}
            disabled={save.isPending}
          >
            <Save className="size-4 mr-1" />
            {save.isPending ? "Saving…" : "Save version"}
          </Button>
        </div>
      </header>

      {save.error && (
        <div className="px-6 py-2 bg-red-50 border-b border-red-200 text-sm text-red-700">
          {(save.error as Error).message}
        </div>
      )}

      <div className="flex flex-1 min-h-0">
        <aside className="w-56 border-r border-slate-200 p-3 space-y-2 overflow-y-auto">
          <div className="text-xs uppercase tracking-wide text-slate-500">Nodes</div>
          {palette.data?.map((nodeType) => (
            <button
              key={nodeType.type}
              onClick={() => addNode(nodeType)}
              className="w-full text-left border border-slate-200 rounded px-2 py-1.5 hover:bg-slate-50"
            >
              <div className="text-sm text-slate-900">{nodeType.title}</div>
              <div className="text-[10px] text-slate-500 leading-tight">
                {nodeType.description}
              </div>
            </button>
          ))}
        </aside>

        <main className="flex-1 min-w-0 relative">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={(changes) => {
              onNodesChange(changes);
              if (changes.some((c) => c.type === "position" || c.type === "remove")) setDirty(true);
            }}
            onEdgesChange={(changes) => {
              onEdgesChange(changes);
              if (changes.some((c) => c.type === "remove")) setDirty(true);
            }}
            onConnect={onConnect}
            onNodeClick={(_, node) => setSelectedId(node.id)}
            fitView
          >
            <Background />
            <Controls />
            <MiniMap pannable zoomable />
          </ReactFlow>
        </main>

        <aside className="w-80 border-l border-slate-200 p-4 space-y-4 overflow-y-auto">
          {showVersions ? (
            <VersionList
              versions={versions.data ?? []}
              onActivate={(versionId) => activate.mutate(versionId)}
              pending={activate.isPending}
            />
          ) : selected && selectedSchema ? (
            <div className="space-y-3">
              <div className="flex items-start justify-between">
                <div>
                  <div className="text-xs uppercase tracking-wide text-slate-500">Node</div>
                  <div className="text-sm font-medium text-slate-900">{selectedSchema.title}</div>
                  <div className="text-[11px] text-slate-500">{selected.id}</div>
                </div>
                <button
                  className="text-slate-400 hover:text-red-600"
                  onClick={() => removeNode(selected.id)}
                  title="Remove node"
                >
                  <Trash2 className="size-4" />
                </button>
              </div>
              {schemaFields(selectedSchema.config_schema).map(([key, field]) => {
                const type = fieldType(field);
                const value = (selected.data.config as Record<string, unknown>)[key];
                return (
                  <div key={key}>
                    <Label htmlFor={`cfg-${key}`}>{field.title ?? key}</Label>
                    {type === "boolean" ? (
                      <input
                        id={`cfg-${key}`}
                        type="checkbox"
                        className="ml-2 align-middle"
                        checked={Boolean(value)}
                        onChange={(e) => updateConfig(selected.id, key, e.target.checked)}
                      />
                    ) : (
                      <Input
                        id={`cfg-${key}`}
                        type={type === "integer" || type === "number" ? "number" : "text"}
                        value={value === undefined || value === null ? "" : String(value)}
                        onChange={(e) =>
                          updateConfig(
                            selected.id,
                            key,
                            type === "integer" || type === "number"
                              ? e.target.value === ""
                                ? null
                                : Number(e.target.value)
                              : e.target.value || null,
                          )
                        }
                      />
                    )}
                    {field.description && (
                      <p className="mt-1 text-[10px] text-slate-500">{field.description}</p>
                    )}
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-sm text-slate-500">
              Select a node to edit it, or add one from the palette.
            </p>
          )}

          <div className="border-t border-slate-200 pt-4 space-y-2">
            <div className="text-xs uppercase tracking-wide text-slate-500">Test run</div>
            <select
              value={botId ?? ""}
              onChange={(e) => setBotId(Number(e.target.value))}
              className="w-full h-9 rounded border border-slate-300 px-2 text-sm bg-white"
            >
              {bots.data?.map((b) => (
                <option key={b.id} value={b.id}>
                  {b.name}
                </option>
              ))}
              {!bots.data?.length && <option value="">No bots yet</option>}
            </select>
            <Input value={question} onChange={(e) => setQuestion(e.target.value)} />
            <Button
              className="w-full"
              onClick={() => testRun.mutate()}
              disabled={!botId || testRun.isPending}
            >
              <Play className="size-4 mr-1" />
              {testRun.isPending ? "Running…" : "Run this draft"}
            </Button>
            {testRun.error && (
              <p className="text-xs text-red-600">{(testRun.error as Error).message}</p>
            )}
            {testRun.data && <TestRunResult run={testRun.data} />}
          </div>
        </aside>
      </div>
    </div>
  );
}

function VersionList({
  versions,
  onActivate,
  pending,
}: {
  versions: PipelineVersion[];
  onActivate: (versionId: number) => void;
  pending: boolean;
}) {
  return (
    <div className="space-y-2">
      <div className="text-xs uppercase tracking-wide text-slate-500">Versions</div>
      {versions.map((v) => (
        <div
          key={v.id}
          className="border border-slate-200 rounded p-2 flex items-center justify-between gap-2"
        >
          <div className="min-w-0">
            <div className="text-sm text-slate-900 truncate">{v.label || `Version ${v.id}`}</div>
            <div className="text-[10px] text-slate-500">
              {new Date(v.created_at).toLocaleString()}
              {v.is_current && (
                <span className="ml-1 text-emerald-700">· current, this is what bots serve</span>
              )}
            </div>
          </div>
          {!v.is_current && (
            <Button size="sm" variant="outline" onClick={() => onActivate(v.id)} disabled={pending}>
              Restore
            </Button>
          )}
        </div>
      ))}
    </div>
  );
}

function TestRunResult({ run }: { run: TestRun }) {
  const slowest = Math.max(...run.nodes.map((n) => n.duration_ms), 1);
  return (
    <div className="space-y-2 border border-slate-200 rounded p-2">
      <div className="text-xs text-slate-500">
        {run.status === "ok" ? "Completed" : "Failed"} in {run.duration_ms} ms ·{" "}
        {run.tokens_in}+{run.tokens_out} tok
        {run.cost_usd && ` · $${run.cost_usd}`}
      </div>
      {run.error && <p className="text-xs text-red-600">{run.error}</p>}
      {run.answer && (
        <p className="text-sm text-slate-900 whitespace-pre-wrap max-h-40 overflow-y-auto">
          {run.answer}
        </p>
      )}
      <div className="space-y-1">
        {run.nodes.map((n) => (
          <div key={n.node_id} className="flex items-center gap-2 text-[10px] text-slate-600">
            <span className="w-24 truncate">{n.node_id}</span>
            {/* Where the time went, per node: one hue, magnitude only */}
            <span
              className="h-1.5 rounded-r bg-slate-700"
              style={{ width: `${Math.max((n.duration_ms / slowest) * 100, 2)}%` }}
            />
            <span className="tabular-nums">{n.duration_ms} ms</span>
          </div>
        ))}
      </div>
      {run.citations.length > 0 && (
        <div className="text-[10px] text-slate-500">
          Cited passages: {run.citations.map((c) => `[${c}]`).join(" ")}
        </div>
      )}
    </div>
  );
}
