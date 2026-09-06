"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";
import { useAuthStore } from "@/lib/auth-store";
import { Activity, BarChart3, ExternalLink, Radar, ScrollText } from "lucide-react";

type ModelUsage = {
  provider: string;
  model: string;
  calls: number;
  tokens: number;
  cost_usd: string;
};

type UsageSummary = {
  days: number;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  cost_usd: string;
  calls_without_price: number;
  by_model: ModelUsage[];
};

type Links = {
  grafana_url: string | null;
  jaeger_url: string | null;
  langfuse_url: string | null;
  prometheus_url: string | null;
  sentry_enabled: boolean;
  tracing_enabled: boolean;
};

const RANGES = [7, 30, 90] as const;

export default function ObservabilityPage() {
  const [days, setDays] = useState<number>(7);
  const usage = useQuery({
    queryKey: ["usage", days],
    queryFn: () => api.get<UsageSummary>(`/observability/usage?days=${days}`),
  });
  // Operator tooling, and the endpoint is super-admin only: asking for it as a
  // tenant would be a 403 in the error banner rather than a hidden card.
  const isSuperAdmin = useAuthStore((s) => s.user?.is_super_admin ?? false);
  const links = useQuery({
    queryKey: ["observability-links"],
    queryFn: () => api.get<Links>("/observability/links"),
    enabled: isSuperAdmin,
  });

  return (
    <div className="p-8 max-w-5xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-slate-900">Observability</h1>
        <p className="text-slate-500 text-sm mt-1">
          Spend and traffic for this organization
          {isSuperAdmin ? ", and where the deeper tooling lives" : ""}.
        </p>
      </div>

      {/* Filters sit in one row above the numbers they change */}
      <div className="flex items-center gap-1">
        {RANGES.map((r) => (
          <button
            key={r}
            onClick={() => setDays(r)}
            className={
              days === r
                ? "text-xs px-2.5 py-1 rounded bg-slate-900 text-white"
                : "text-xs px-2.5 py-1 rounded border border-slate-200 text-slate-600 hover:bg-slate-50"
            }
          >
            {r} days
          </button>
        ))}
      </div>

      <UsagePanel usage={usage.data} loading={usage.isLoading} />
      <ModelTable rows={usage.data?.by_model ?? []} />
      {isSuperAdmin && <ToolLinks links={links.data} />}
    </div>
  );
}

function StatTile({
  label,
  value,
  hint,
  tone = "default",
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "default" | "warning";
}) {
  return (
    <div className="rounded border border-slate-200 p-4">
      <div className="text-xs uppercase tracking-wide text-slate-500">{label}</div>
      <div
        className={
          tone === "warning"
            ? "mt-1 text-2xl font-semibold text-amber-700 tabular-nums"
            : "mt-1 text-2xl font-semibold text-slate-900 tabular-nums"
        }
      >
        {value}
      </div>
      {hint && <div className="mt-1 text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

function formatNumber(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function formatUsd(value: string): string {
  const n = Number(value);
  if (!Number.isFinite(n)) return "$0";
  if (n > 0 && n < 0.01) return "<$0.01";
  return `$${n.toFixed(2)}`;
}

function UsagePanel({ usage, loading }: { usage?: UsageSummary; loading: boolean }) {
  if (loading) return <p className="text-sm text-slate-500">Loading…</p>;
  if (!usage) return null;
  const tokens = usage.tokens_in + usage.tokens_out;
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <StatTile label="LLM calls" value={formatNumber(usage.calls)} hint={`last ${usage.days} days`} />
      <StatTile
        label="Tokens"
        value={formatNumber(tokens)}
        hint={`${formatNumber(usage.tokens_in)} in · ${formatNumber(usage.tokens_out)} out`}
      />
      <StatTile label="Spend" value={formatUsd(usage.cost_usd)} hint="estimated from the catalog" />
      <StatTile
        label="Unpriced calls"
        value={String(usage.calls_without_price)}
        hint={
          usage.calls_without_price > 0
            ? "model missing from the catalog: spend unknown"
            : "every model was priced"
        }
        tone={usage.calls_without_price > 0 ? "warning" : "default"}
      />
    </div>
  );
}

function ModelTable({ rows }: { rows: ModelUsage[] }) {
  const maxCost = Math.max(...rows.map((r) => Number(r.cost_usd) || 0), 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle>By model</CardTitle>
      </CardHeader>
      <CardContent>
        {rows.length === 0 ? (
          <p className="text-sm text-slate-500">No LLM calls in this window yet.</p>
        ) : (
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-slate-500">
                <th className="pb-2 font-medium">Model</th>
                <th className="pb-2 font-medium text-right">Calls</th>
                <th className="pb-2 font-medium text-right">Tokens</th>
                <th className="pb-2 font-medium text-right">Spend</th>
                <th className="pb-2 font-medium w-40">Share</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((r) => {
                const cost = Number(r.cost_usd) || 0;
                const share = maxCost > 0 ? (cost / maxCost) * 100 : 0;
                return (
                  <tr key={`${r.provider}/${r.model}`}>
                    <td className="py-2">
                      <div className="text-slate-900">{r.model}</div>
                      <div className="text-[11px] text-slate-500">{r.provider}</div>
                    </td>
                    <td className="py-2 text-right tabular-nums text-slate-700">
                      {formatNumber(r.calls)}
                    </td>
                    <td className="py-2 text-right tabular-nums text-slate-700">
                      {formatNumber(r.tokens)}
                    </td>
                    <td className="py-2 text-right tabular-nums text-slate-900">
                      {formatUsd(r.cost_usd)}
                    </td>
                    <td className="py-2 pl-3">
                      {/* Magnitude within one measure: one hue, thin mark, rounded end */}
                      <div
                        className="h-2 rounded-r bg-slate-800"
                        style={{ width: `${Math.max(share, cost > 0 ? 4 : 0)}%` }}
                        title={`${formatUsd(r.cost_usd)} of ${formatUsd(String(maxCost))} (largest)`}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  );
}

/**
 * Grafana, Jaeger and Prometheus bind to loopback on the server and are reached
 * over an SSH tunnel, so their links are right and unreachable at the same
 * time. Saying so beats letting the reader discover it by clicking.
 */
function needsTunnel(url: string): boolean {
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "127.0.0.1" || host === "::1";
  } catch {
    return false;
  }
}

function ToolLinks({ links }: { links?: Links }) {
  if (!links) return null;
  const entries = [
    { label: "Grafana dashboards", url: links.grafana_url, icon: BarChart3 },
    { label: "Jaeger traces", url: links.jaeger_url, icon: Radar },
    { label: "Langfuse (LLM traces)", url: links.langfuse_url, icon: ScrollText },
    { label: "Prometheus", url: links.prometheus_url, icon: Activity },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Tooling</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {entries.map(({ label, url, icon: Icon }) => (
          <div key={label} className="flex items-center gap-2 text-sm">
            <Icon className="size-4 text-slate-400" />
            {url ? (
              <>
                <a
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-slate-900 hover:underline inline-flex items-center gap-1"
                >
                  {label} <ExternalLink className="size-3" />
                </a>
                {needsTunnel(url) && (
                  <span className="text-xs text-slate-400">
                    server-local, needs an SSH tunnel
                  </span>
                )}
              </>
            ) : (
              <span className="text-slate-400">{label} - not configured</span>
            )}
          </div>
        ))}
        <div className="pt-2 text-xs text-slate-500">
          Request tracing {links.tracing_enabled ? "on" : "off"} · Sentry{" "}
          {links.sentry_enabled ? "on" : "off"}
        </div>
      </CardContent>
    </Card>
  );
}
