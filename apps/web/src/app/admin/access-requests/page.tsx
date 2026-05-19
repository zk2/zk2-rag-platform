"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

type AccessRequest = {
  id: number;
  email: string;
  message: string | null;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  decided_at: string | null;
};

export default function AccessRequestsPage() {
  const qc = useQueryClient();
  const [statusFilter, setStatusFilter] = useState<"pending" | "approved" | "rejected" | "all">(
    "pending",
  );

  const list = useQuery({
    queryKey: ["access-requests", statusFilter],
    queryFn: () =>
      api.get<AccessRequest[]>(
        `/admin/access-requests${statusFilter === "all" ? "" : `?status=${statusFilter}`}`,
      ),
  });

  const decide = useMutation({
    mutationFn: async (vars: { id: number; approve: boolean; orgName?: string }) =>
      api.post(`/admin/access-requests/${vars.id}/decide`, {
        approve: vars.approve,
        create_org_name: vars.orgName ?? null,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["access-requests"] }),
  });

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Access requests</h1>
      <div className="flex gap-2 mb-6">
        {(["pending", "approved", "rejected", "all"] as const).map((s) => (
          <Button
            key={s}
            variant={statusFilter === s ? "default" : "outline"}
            size="sm"
            onClick={() => setStatusFilter(s)}
          >
            {s}
          </Button>
        ))}
      </div>

      {list.isLoading && <p>Loading…</p>}
      {list.error && <p className="text-red-600">Failed: {(list.error as Error).message}</p>}
      {list.data && list.data.length === 0 && <p className="text-slate-500">No requests.</p>}

      <ul className="space-y-3">
        {list.data?.map((r) => (
          <RequestRow key={r.id} request={r} onDecide={decide.mutate} />
        ))}
      </ul>
    </div>
  );
}

function RequestRow({
  request,
  onDecide,
}: {
  request: AccessRequest;
  onDecide: (vars: { id: number; approve: boolean; orgName?: string }) => void;
}) {
  const [orgName, setOrgName] = useState(request.email.split("@")[0]);

  return (
    <li className="bg-white border border-slate-200 rounded-lg p-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="font-medium text-slate-900">{request.email}</div>
          <div className="text-xs text-slate-500">
            {new Date(request.created_at).toLocaleString()} — {request.status}
          </div>
          {request.message && (
            <p className="mt-2 text-sm text-slate-700 whitespace-pre-wrap">{request.message}</p>
          )}
        </div>
        {request.status === "pending" && (
          <div className="flex flex-col gap-2 w-64">
            <Input
              value={orgName}
              onChange={(e) => setOrgName(e.target.value)}
              placeholder="Org name"
            />
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={() => onDecide({ id: request.id, approve: true, orgName })}
                className="flex-1"
              >
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onDecide({ id: request.id, approve: false })}
                className="flex-1"
              >
                Reject
              </Button>
            </div>
          </div>
        )}
      </div>
    </li>
  );
}
