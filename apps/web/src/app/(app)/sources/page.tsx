"use client";

import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import {
  Folder,
  FolderPlus,
  FileText,
  Globe,
  Map,
  Trash2,
  RefreshCw,
  Upload,
} from "lucide-react";
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
    <div className="p-8 max-w-6xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Sources</h1>
        <p className="text-slate-500 text-sm mt-1">
          Upload documents or add URLs. Each file is chunked and embedded; the
          bot retrieves relevant chunks at query time.
        </p>
      </div>
      <AddSourceCard />
      <Tree />
    </div>
  );
}

/* --- the tree --- */

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
      <CardHeader className="p-4">
        <div className="flex items-center justify-between">
          <CardTitle>Tree</CardTitle>
          <button onClick={() => list.refetch()} title="Refresh">
            <RefreshCw className="size-4 text-slate-500 hover:text-slate-900" />
          </button>
        </div>
      </CardHeader>
      <CardContent className="p-4">
        {list.isLoading && <p className="text-slate-500 text-sm">Loading...</p>}
        {list.data && list.data.length === 0 && (
          <p className="text-slate-500 text-sm">
            No sources yet. Add one with the tabs above.
          </p>
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

/* --- adding a source --- */

const TABS = [
  { key: "folder", label: "Folder", icon: FolderPlus },
  { key: "file", label: "File", icon: Upload },
  { key: "url", label: "URL", icon: Globe },
  { key: "sitemap", label: "Sitemap", icon: Map },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function AddSourceCard() {
  const [tab, setTab] = useState<TabKey>("folder");
  return (
    <Card>
      <div className="flex items-center gap-1 border-b border-slate-100 px-3 pt-2">
        {TABS.map((t) => {
          const Icon = t.icon;
          return (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={cn(
                "-mb-px flex items-center gap-2 border-b-2 px-3 py-2 text-sm",
                tab === t.key
                  ? "border-slate-900 font-medium text-slate-900"
                  : "border-transparent text-slate-500 hover:text-slate-900",
              )}
            >
              <Icon className="size-4" /> {t.label}
            </button>
          );
        })}
      </div>
      <CardContent className="p-4">
        {tab === "folder" && <CreateDirectoryForm />}
        {tab === "file" && <UploadFileForm />}
        {tab === "url" && <AddUrlForm />}
        {tab === "sitemap" && <ImportSitemapForm />}
      </CardContent>
    </Card>
  );
}

/** One labelled control in a row of controls. */
function Field({
  label,
  htmlFor,
  className,
  children,
}: {
  label: string;
  htmlFor?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={className}>
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
    </div>
  );
}

function flattenDirs(
  nodes: Node[],
  depth = 0,
): Array<{ id: number; name: string; depth: number }> {
  const out: Array<{ id: number; name: string; depth: number }> = [];
  for (const n of nodes) {
    if (n.type !== "directory") continue;
    out.push({ id: n.id, name: n.name, depth });
    out.push(...flattenDirs(n.children, depth + 1));
  }
  return out;
}

/**
 * Folders are picked from the tree, never typed. The numeric id the API wants
 * is not shown anywhere in the UI, so asking for it was asking the user to
 * guess.
 */
function FolderSelect({
  id,
  value,
  onChange,
}: {
  id: string;
  value: string;
  onChange: (v: string) => void;
}) {
  const list = useTree();
  const dirs = flattenDirs(list.data ?? []);
  return (
    <select
      id={id}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="h-10 w-full rounded-md border border-slate-300 bg-white px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-slate-400"
    >
      <option value="">root</option>
      {dirs.map((d) => (
        <option key={d.id} value={String(d.id)}>
          {"  ".repeat(d.depth)}
          {d.name}
        </option>
      ))}
    </select>
  );
}

function parentIdOf(value: string): number | null {
  return value ? Number(value) : null;
}

function CreateDirectoryForm() {
  const qc = useQueryClient();
  const [name, setName] = useState("");
  const [parent, setParent] = useState("");
  const m = useMutation({
    mutationFn: () =>
      api.post("/sources/directory", { name, parent_id: parentIdOf(parent) }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="Name" htmlFor="dir-name" className="min-w-64 flex-1">
        <Input
          id="dir-name"
          placeholder="Handbook"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
      </Field>
      <Field label="Inside" htmlFor="dir-parent" className="w-56">
        <FolderSelect id="dir-parent" value={parent} onChange={setParent} />
      </Field>
      <Button onClick={() => m.mutate()} disabled={!name || m.isPending}>
        Create
      </Button>
      {m.error && (
        <p className="w-full text-xs text-red-600">{(m.error as Error).message}</p>
      )}
    </div>
  );
}

const ACCEPTED = ".pdf,.docx,.xlsx,.txt,.md,.json,.html,.htm";

function UploadFileForm() {
  const qc = useQueryClient();
  const inputRef = useRef<HTMLInputElement>(null);
  const [parent, setParent] = useState("");
  const m = useMutation({
    mutationFn: (file: File) =>
      api.upload("/sources/file", file, parent ? { parent_id: parent } : {}),
    onSuccess: () => {
      if (inputRef.current) inputRef.current.value = "";
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="Inside" htmlFor="up-parent" className="w-56">
        <FolderSelect id="up-parent" value={parent} onChange={setParent} />
      </Field>
      <Button
        variant="outline"
        onClick={() => inputRef.current?.click()}
        disabled={m.isPending}
      >
        {m.isPending ? "Uploading..." : "Choose file"}
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept={ACCEPTED}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) m.mutate(f);
        }}
      />
      <p className="pb-2.5 text-xs text-slate-500">
        PDF, DOCX, XLSX, TXT, MD, JSON, HTML. Indexing starts on upload.
      </p>
      {m.error && (
        <p className="w-full text-xs text-red-600">{(m.error as Error).message}</p>
      )}
    </div>
  );
}

function AddUrlForm() {
  const qc = useQueryClient();
  const [url, setUrl] = useState("");
  const [parent, setParent] = useState("");
  const m = useMutation({
    mutationFn: () =>
      api.post("/sources/url", { url, parent_id: parentIdOf(parent) }),
    onSuccess: () => {
      setUrl("");
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });
  return (
    <div className="flex flex-wrap items-end gap-3">
      <Field label="Page URL" htmlFor="url-value" className="min-w-64 flex-1">
        <Input
          id="url-value"
          placeholder="https://example.com/pricing"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
        />
      </Field>
      <Field label="Inside" htmlFor="url-parent" className="w-56">
        <FolderSelect id="url-parent" value={parent} onChange={setParent} />
      </Field>
      <Button onClick={() => m.mutate()} disabled={!url || m.isPending}>
        Fetch and index
      </Button>
      {m.error && (
        <p className="w-full text-xs text-red-600">{(m.error as Error).message}</p>
      )}
    </div>
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

function ImportSitemapForm() {
  const qc = useQueryClient();
  const [baseUrl, setBaseUrl] = useState("");
  const [limit, setLimit] = useState("100");
  const [parent, setParent] = useState("");
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
        parent_id: parentIdOf(parent),
      }),
    onSuccess: () => {
      setPreview(null);
      setBaseUrl("");
      qc.invalidateQueries({ queryKey: ["sources", "tree"] });
    },
  });

  const error = (previewM.error ?? importM.error) as Error | null;

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-3">
        <Field label="Site" htmlFor="sitemap-url" className="min-w-64 flex-1">
          <Input
            id="sitemap-url"
            placeholder="https://example.com"
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value);
              setPreview(null);
            }}
          />
        </Field>
        <Field label="Max pages" htmlFor="sitemap-limit" className="w-28">
          <Input
            id="sitemap-limit"
            value={limit}
            onChange={(e) => {
              setLimit(e.target.value);
              setPreview(null);
            }}
            inputMode="numeric"
          />
        </Field>
        <Field label="Inside" htmlFor="sitemap-parent" className="w-56">
          <FolderSelect
            id="sitemap-parent"
            value={parent}
            onChange={setParent}
          />
        </Field>
        <Button
          onClick={() => previewM.mutate()}
          disabled={!baseUrl || previewM.isPending || preview !== null}
        >
          {previewM.isPending ? "Scanning sitemap..." : "Preview"}
        </Button>
      </div>

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
                ...and {preview.total_found - preview.urls.length} more
              </li>
            )}
          </ul>
          <div className="flex gap-2">
            <Button
              onClick={() => importM.mutate()}
              disabled={importM.isPending || preview.would_import === 0}
            >
              {importM.isPending
                ? "Queueing..."
                : `Import ${preview.would_import}`}
            </Button>
            <Button
              variant="outline"
              onClick={() => setPreview(null)}
              disabled={importM.isPending}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      {error && <p className="text-xs text-red-600">{error.message}</p>}
    </div>
  );
}
