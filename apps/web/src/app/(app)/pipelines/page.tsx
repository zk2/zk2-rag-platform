"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import type { Pipeline } from "@/lib/pipelines";
import { GitBranch, Trash2 } from "lucide-react";

export default function PipelinesPage() {
  const qc = useQueryClient();
  const list = useQuery({
    queryKey: ["pipelines"],
    queryFn: () => api.get<Pipeline[]>("/pipelines"),
  });
  const [name, setName] = useState("");

  const create = useMutation({
    mutationFn: () => api.post<Pipeline>("/pipelines", { name }),
    onSuccess: () => {
      setName("");
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    },
  });
  const remove = useMutation({
    mutationFn: (id: number) => api.delete(`/pipelines/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipelines"] }),
  });

  return (
    <div className="p-8 max-w-4xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Pipelines</h1>
        <p className="text-slate-500 text-sm mt-1">
          A pipeline is the graph a bot runs for every question. Bots without one use the built-in
          hybrid default.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>New pipeline</CardTitle>
        </CardHeader>
        <CardContent className="flex gap-3 items-end">
          <div className="flex-1">
            <Label htmlFor="pipeline-name">Name</Label>
            <Input
              id="pipeline-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Support RAG"
            />
          </div>
          <Button onClick={() => create.mutate()} disabled={!name || create.isPending}>
            {create.isPending ? "Creating…" : "Create"}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Your pipelines</CardTitle>
        </CardHeader>
        <CardContent>
          {list.isLoading && <p className="text-sm text-slate-500">Loading…</p>}
          {list.data?.length === 0 && (
            <p className="text-sm text-slate-500">
              None yet. A new pipeline starts as a copy of the default graph.
            </p>
          )}
          <ul className="divide-y divide-slate-100">
            {list.data?.map((p) => (
              <li key={p.id} className="py-3 flex items-center justify-between">
                <div>
                  <div className="font-medium text-slate-900">{p.name}</div>
                  <div className="text-xs text-slate-500">
                    {p.description || "No description"}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Link href={`/pipelines/${p.id}`}>
                    <Button size="sm" variant="outline">
                      <GitBranch className="size-4 mr-1" /> Edit
                    </Button>
                  </Link>
                  <button
                    className="text-slate-400 hover:text-red-600"
                    onClick={() => confirm(`Delete pipeline "${p.name}"?`) && remove.mutate(p.id)}
                  >
                    <Trash2 className="size-4" />
                  </button>
                </div>
              </li>
            ))}
          </ul>
          {remove.error && (
            <p className="mt-2 text-xs text-red-600">{(remove.error as Error).message}</p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
