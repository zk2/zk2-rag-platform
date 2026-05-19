"use client";

import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";

type Invite = {
  id: number;
  email: string;
  role: string;
  org_id: number | null;
  expires_at: string;
  used_at: string | null;
  created_at: string;
};

export default function InvitesPage() {
  const list = useQuery({
    queryKey: ["invites"],
    queryFn: () => api.get<Invite[]>("/admin/invites"),
  });

  return (
    <div className="max-w-4xl">
      <h1 className="text-2xl font-bold text-slate-900 mb-6">Invites</h1>
      {list.isLoading && <p>Loading…</p>}
      {list.error && <p className="text-red-600">{(list.error as Error).message}</p>}
      <table className="w-full bg-white border border-slate-200 rounded-lg overflow-hidden text-sm">
        <thead className="bg-slate-100">
          <tr>
            <th className="text-left p-3">Email</th>
            <th className="text-left p-3">Role</th>
            <th className="text-left p-3">Org ID</th>
            <th className="text-left p-3">Status</th>
            <th className="text-left p-3">Expires</th>
          </tr>
        </thead>
        <tbody>
          {list.data?.map((i) => (
            <tr key={i.id} className="border-t border-slate-100">
              <td className="p-3">{i.email}</td>
              <td className="p-3">{i.role}</td>
              <td className="p-3">{i.org_id ?? "—"}</td>
              <td className="p-3">
                {i.used_at
                  ? "used"
                  : new Date(i.expires_at) < new Date()
                    ? "expired"
                    : "pending"}
              </td>
              <td className="p-3">{new Date(i.expires_at).toLocaleString()}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
