"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { Folder, FileText, Globe, Trash2, RefreshCw } from "lucide-react";
import { cn } from "@/lib/utils";

type Node = {
  id: number;
  type: "directory" | "file" | "web";
  name: string;
  status: "pending" | "indexing" | "ready" | "failed";
  error: string | null;
  children: Node[];
};

export default function SourcesPage() {
  return (
    <div className="p-8 max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Sources</h1>
        <p className="text-slate-500 text-sm mt-1">
          Upload documents or add URLs. Each file is chunked and embedded; the
          bot retrieves relevant chunks at query time.
        </p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2">
          <Tree />
        </div>
        <div className="space-y-4">
          <CreateDirectoryCard />
          <UploadFileCard />
          <AddUrlCard />
          <ImportSitemapCard />
        </div>
      </div>
    </div>
  );
}

function hasPending(nodes: Node[] | undefined): boolean {
  if (!nodes) return false;
  for (const n of nodes) {
    if (n.status === "pending" || n.status === "indexing") return true;
    if (hasPending(n.children)) return true;
  }
  return false;
}

function useTree() {
  return useQuery({
    queryKey: ["sources", "tree"],
    queryFn: () => api.get<Node[]>("/sources/tree"),
    refetchInterval: (q) => (hasPending(q.state.data) ? 2000 : false),
  });
}

function Tree() {
  const list = useTree();
  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>Tree</CardTitle>
          <button onClick={() => list.refetch()} title="Refresh">
            <RefreshCw className="size-4 text-slate-500 hover:text-slate-900" />
          </button>
        </div>
      </CardHeader>
      <CardContent>
        {list.isLoading && <p className="text-slate-500 text-sm">Loading…</p>}
        {list.data && list.data.length === 0 && (
          <p className="text-slate-500 text-sm">No sources yet. Add one →</p>
        )}
        <ul className="space-y-1">
          {list.data?.map((n) => (
            <NodeRow key={n.id} node={n} depth={0} />
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

function NodeRow({ node, depth }: { node: Node; depth: number }) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ["sources", "tree"] });
  const del = useMutation({
    mutationFn: () => api.delete(`/sources/${node.id}`),
    onSuccess: invalidate,
  });
  const reindex = useMutation({
    mutationFn: () => api.post(`/sources/${node.id}/reindex`),
    onSuccess: invalidate,
  });
  const Icon =
    node.type === "directory" ? Folder : node.type === "web" ? Globe : FileText;
  const color =
    node.status === "ready"
      ? "text-emerald-600"
      : node.status === "failed"
        ? "text-red-600"
        : "text-amber-600";
  return (
    <li>
      <div
        style={{ paddingLeft: depth * 16 }}
        className="group flex items-center justify-between py-1.5 px-2 rounded hover:bg-slate-50"
      >
        <div className="flex items-center gap-2 min-w-0">
          <Icon className="size-4 text-slate-500 shrink-0" />
          <span className="text-sm text-slate-900 truncate">{node.name}</span>
          <span className={cn("text-[10px] uppercase tracking-wide", color)}>
            {node.status}
          </span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          {/* A directory holds no text of its own - the API refuses to index one */}
          {node.type !== "directory" && (
            <button
              className={cn(
                "text-slate-400 hover:text-slate-900 disabled:opacity-40",
                node.status === "ready" && "opacity-0 group-hover:opacity-100",
              )}
              onClick={() => reindex.mutate()}
              disabled={reindex.isPending || node.status === "indexing"}
              title={
                node.status === "ready"
                  ? "Index again with the current embedding model"
                  : "Index this document"
              }
            >
              <RefreshCw
                className={cn("size-4", reindex.isPending && "animate-spin")}
              />
            </button>
          )}
          <button
            className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-600"
            onClick={() => {
              if (confirm(`Delete "${node.name}" and its sub-tree?`))
                del.mutate();
            }}
            title="Delete"
          >
            <Trash2 className="size-4" />
          </button>
        </div>
      </div>
      {node.error && (
        <p
          style={{ paddingLeft: depth * 16 + 32 }}
          className="pb-1 text-xs text-red-600"
        >
          {node.error}
        </p>
      )}
      {node.children.length > 0 && (
        <ul>
          {node.children.map((c) => (
            <NodeRow key={c.id} node={c} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}

function CreateDirectoryCard() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const m = useMutation({
    mutationFn: () => api.post("/sources/directory", { name, parent_id: null }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>New folder</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          placeholder="Folder name"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Button
          onClick={() => m.mutate()}
          disabled={!name || m.isPending}
          className="w-full"
        >
          Create
        </Button>
      </CardContent>
    </Card>
  );
}

function UploadFileCard() {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [parentId, setParentId] = useState<string>("");
  const m = useMutation({
    mutationFn: (file: File) =>
      api.upload(
        "/sources/file",
        file,
        parentId ? { parent_id: parentId } : {},
      ),
    onSuccess: () => {
      if (inputRef.current) inputRef.current.value = "";
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Upload file</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Label htmlFor="up-parent">Parent folder ID (optional)</Label>
        <Input
          id="up-parent"
          value={parentId}
          onChange={(e) => setParentId(e.target.value)}
          placeholder="leave empty for root"
        />
        <input
          ref={inputRef}
          type="file"
          className="text-sm block w-full"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) m.mutate(f);
          }}
        />
        {m.isPending && <p className="text-xs text-slate-500">Uploading…</p>}
        {m.error && (
          <p className="text-xs text-red-600">{(m.error as Error).message}</p>
        )}
      </CardContent>
    </Card>
  );
}

function AddUrlCard() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [parentId, setParentId] = useState("");
  const m = useMutation({
    mutationFn: () =>
      api.post("/sources/url", {
        url,
        parent_id: parentId ? Number(parentId) : null,
      }),
    onSuccess: () => {
      setUrl("");
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });
  return (
    <Card>
      <CardHeader>
        <CardTitle>Add URL</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          placeholder="https://…"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
        <Input
          placeholder="Parent folder ID (optional)"
          value={parentId}
          onChange={(e) => setParentId(e.target.value)}
        />
        <Button
          onClick={() => m.mutate()}
          disabled={!url || m.isPending}
          className="w-full"
        >
          Fetch & index
        </Button>
      </CardContent>
    </Card>
  );
}

type SitemapPreview = {
  base_url: string;
  total_found: number;
  would_import: number;
  urls: string[];
};

// Rough per-page estimate, only ever shown as an order of magnitude.
const TOKENS_PER_PAGE = 1500;

function ImportSitemapCard() {
  const qc = useQueryClient();
  const [baseUrl, setBaseUrl] = useState("");
  const [limit, setLimit] = useState("100");
  const [preview, setPreview] = useState<SitemapPreview | null>(null);

  const previewM = useMutation({
    mutationFn: () =>
      api.post<SitemapPreview>("/sources/sitemap/preview", {
        base_url: baseUrl,
        limit: Number(limit),
      }),
    onSuccess: (data) => setPreview(data),
  });

  const importM = useMutation({
    mutationFn: () =>
      api.post("/sources/sitemap", {
        base_url: baseUrl,
        limit: Number(limit),
        parent_id: null,
      }),
    onSuccess: () => {
      setPreview(null);
      setBaseUrl("");
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });

  const error = (previewM.error ?? importM.error) as Error | null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Import sitemap</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input
          placeholder="https://example.com"
          value={baseUrl}
          onChange={(e) => {
            setBaseUrl(e.target.value);
            setPreview(null);
          }}
        />
        <div className="space-y-1">
          <Label htmlFor="sitemap-limit">Max pages</Label>
          <Input
            id="sitemap-limit"
            value={limit}
            onChange={(e) => {
              setLimit(e.target.value);
              setPreview(null);
            }}
            inputMode="numeric"
          />
        </div>

        {!preview && (
          <Button
            onClick={() => previewM.mutate()}
            disabled={!baseUrl || previewM.isPending}
            className="w-full"
          >
            {previewM.isPending ? "Scanning sitemap…" : "Preview"}
          </Button>
        )}

        {preview && (
          <div className="rounded border border-slate-200 bg-slate-50 p-3 space-y-2">
            <p className="text-sm text-slate-900">
              Found <strong>{preview.total_found}</strong> pages, will import{" "}
              <strong>{preview.would_import}</strong>.
            </p>
            <p className="text-xs text-slate-500">
              Each page is fetched and embedded: roughly{" "}
              {Math.round((preview.would_import * TOKENS_PER_PAGE) / 1000)}k
              tokens of embedding work. This is billed to your provider key.
            </p>
            <ul className="text-xs text-slate-500 space-y-0.5 max-h-24 overflow-y-auto">
              {preview.urls.map((u) => (
                <li key={u} className="truncate">
                  {u}
                </li>
              ))}
              {preview.total_found > preview.urls.length && (
                <li className="text-slate-400">
                  …and {preview.total_found - preview.urls.length} more
                </li>
              )}
            </ul>
            <div className="flex gap-2">
              <Button
                onClick={() => importM.mutate()}
                disabled={importM.isPending || preview.would_import === 0}
                className="flex-1"
              >
                {importM.isPending
                  ? "Queueing…"
                  : `Import ${preview.would_import}`}
              </Button>
              <Button
                onClick={() => setPreview(null)}
                disabled={importM.isPending}
                className="flex-1 bg-white text-slate-700 border border-slate-300 hover:bg-slate-50"
              >
                Cancel
              </Button>
            </div>
          </div>
        )}

        {error && <p className="text-xs text-red-600">{error.message}</p>}
      </CardContent>
    </Card>
  );
}
