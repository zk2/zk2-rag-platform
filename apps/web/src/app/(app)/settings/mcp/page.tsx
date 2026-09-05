"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { Plug, RefreshCw, Trash2, Wrench } from "lucide-react";

type McpServer = {
  id: number;
  name: string;
  url: string;
  enabled: boolean;
  status: string;
  last_error: string | null;
  tool_count: number | null;
  last_checked_at: string | null;
};

type Tool = {
  name: string;
  description: string;
  kind: string;
  args_schema: Record<string, unknown>;
};

export default function McpPage() {
  return (
    <div className="p-8 max-w-3xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Agent tools</h1>
        <p className="text-slate-500 text-sm mt-1">
          Built-in tools come with the deployment. MCP servers add more - their tools appear to
          the agent exactly like the built-in ones.
        </p>
      </div>
      <ServersCard />
      <ToolsCard />
    </div>
  );
}

function statusTone(status: string): string {
  if (status === "ok") return "text-emerald-700";
  if (status === "error") return "text-red-600";
  return "text-slate-500";
}

function ServersCard() {
  const qc = useQueryClient();
  const servers = useQuery({
    queryKey: ["mcp-servers"],
    queryFn: () => api.get<McpServer[]>("/settings/mcp"),
  });
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["mcp-servers"] });
    qc.invalidateQueries({ queryKey: ["agent-tools"] });
  };

  const add = useMutation({
    mutationFn: () => api.post<McpServer>("/settings/mcp", { name, url }),
    onSuccess: () => {
      setName("");
      setUrl("");
      invalidate();
    },
  });
  const check = useMutation({
    mutationFn: (id: number) => api.post<McpServer>(`/settings/mcp/${id}/check`),
    onSuccess: invalidate,
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/settings/mcp/${id}`),
    onSuccess: invalidate,
  });

  return (
    <Card>
      <CardHeader>
        <CardTitle>MCP servers</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3 items-end">
          <div>
            <Label htmlFor="mcp-name">Name</Label>
            <Input
              id="mcp-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="jira"
            />
            <p className="mt-1 text-[10px] text-slate-500">
              Prefixes the tool names, so two servers can both offer &quot;search&quot;.
            </p>
          </div>
          <div>
            <Label htmlFor="mcp-url">URL</Label>
            <Input
              id="mcp-url"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              placeholder="https://mcp.example.com/mcp"
            />
          </div>
          <Button onClick={() => add.mutate()} disabled={!name || !url || add.isPending}>
            <Plug className="size-4 mr-1" />
            {add.isPending ? "Connecting…" : "Connect"}
          </Button>
        </div>
        <p className="text-[10px] text-slate-500">
          Authenticated servers are not supported yet - only servers that need no credentials.
        </p>
        {add.error && <p className="text-xs text-red-600">{(add.error as Error).message}</p>}

        <ul className="divide-y divide-slate-100">
          {servers.data?.map((server) => (
            <li key={server.id} className="py-3 flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="font-medium text-slate-900">{server.name}</div>
                <div className="text-xs text-slate-500 truncate">{server.url}</div>
                <div className={`text-xs mt-0.5 ${statusTone(server.status)}`}>
                  {server.status}
                  {server.tool_count !== null && ` · ${server.tool_count} tools`}
                  {server.last_checked_at &&
                    ` · checked ${new Date(server.last_checked_at).toLocaleTimeString()}`}
                </div>
                {server.last_error && (
                  <div className="text-[10px] text-red-600 mt-0.5">{server.last_error}</div>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => check.mutate(server.id)}
                  disabled={check.isPending}
                >
                  <RefreshCw className="size-4" />
                </Button>
                <button
                  className="text-slate-400 hover:text-red-600"
                  onClick={() => confirm(`Disconnect "${server.name}"?`) && remove.mutate(server.id)}
                >
                  <Trash2 className="size-4" />
                </button>
              </div>
            </li>
          ))}
        </ul>
        {servers.data?.length === 0 && (
          <p className="text-sm text-slate-500">No MCP servers connected.</p>
        )}
      </CardContent>
    </Card>
  );
}

function ToolsCard() {
  const tools = useQuery({
    queryKey: ["agent-tools"],
    queryFn: () => api.get<Tool[]>("/agents/tools"),
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Available to agents</CardTitle>
      </CardHeader>
      <CardContent>
        {tools.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
        <ul className="space-y-2">
          {tools.data?.map((tool) => (
            <li key={tool.name} className="flex items-start gap-2">
              <Wrench className="size-4 text-slate-400 mt-0.5 shrink-0" />
              <div>
                <div className="text-sm text-slate-900">
                  {tool.name}
                  <span className="ml-2 text-[10px] uppercase tracking-wide text-slate-400">
                    {tool.kind}
                  </span>
                </div>
                <div className="text-xs text-slate-500">{tool.description}</div>
              </div>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
