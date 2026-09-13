"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { LabelWithHelp } from "@/components/ui/help-tip";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type ObservedState =
  | "running"
  | "stopped"
  | "partial"
  | "starting"
  | "stopping"
  | "error";

type ManagedService = {
  name: string;
  desired_state: "on" | "off";
  off_at: string | null;
  observed_state: ObservedState | null;
  observed_detail: string | null;
  observed_at: string | null;
  agent_reporting: boolean;
  changed_at: string;
  changed_by_email: string | null;
};

/** What each switch is for, in the words of whoever has to decide to flip it. */
const ABOUT: Record<string, { title: string; what: string; whileOff: string }> =
  {
    langfuse: {
      title: "Langfuse (LLM traces)",
      what:
        "Records what the model was given and what it answered on every chat turn and eval " +
        "run: the prompt, the retrieved passages, tokens and cost. Six containers holding " +
        "about 2 GB of memory together, whether or not anybody is looking.",
      whileOff:
        "No traces are being recorded. Turns answered while it is off never appear in " +
        "Langfuse, not even after it is switched back on. What was recorded before stays on disk.",
    },
  };

const AUTO_OFF_CHOICES = [
  { value: "1", label: "1 hour" },
  { value: "4", label: "4 hours" },
  { value: "8", label: "8 hours" },
  { value: "24", label: "24 hours" },
  { value: "", label: "until switched off" },
];

type Tone = "ok" | "idle" | "busy" | "bad";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "border-emerald-200 bg-emerald-50 text-emerald-700",
  idle: "border-slate-200 bg-slate-100 text-slate-600",
  busy: "border-amber-200 bg-amber-50 text-amber-700",
  bad: "border-red-200 bg-red-50 text-red-700",
};

function describe(s: ManagedService): {
  label: string;
  tone: Tone;
  note?: string;
} {
  if (!s.agent_reporting) {
    return {
      label: s.observed_at ? "agent not reporting" : "no agent",
      tone: "bad",
      note:
        "Nothing on the server is applying this switch, so flipping it records the choice " +
        "and changes nothing else. The agent is installed with make prod-agent-install on " +
        "the server; make prod-agent-logs says why it is quiet.",
    };
  }
  if (s.observed_state === "error") {
    return {
      label: "failed",
      tone: "bad",
      note: s.observed_detail ?? undefined,
    };
  }
  const settled = s.desired_state === "on" ? "running" : "stopped";
  if (s.observed_state === settled) {
    return { label: settled, tone: settled === "running" ? "ok" : "idle" };
  }
  if (s.observed_state === "partial" && s.desired_state === "on") {
    return {
      label: "partly running",
      tone: "bad",
      note: s.observed_detail ?? undefined,
    };
  }
  return {
    label: s.desired_state === "on" ? "starting" : "stopping",
    tone: "busy",
  };
}

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

export default function ServicesPage() {
  const list = useQuery({
    queryKey: ["managed-services"],
    queryFn: () => api.get<ManagedService[]>("/admin/services"),
    // Quick while something is starting or stopping, so the page follows it
    refetchInterval: (query) =>
      query.state.data?.some((s) => describe(s).tone === "busy") ? 3000 : 15000,
  });

  return (
    <div className="max-w-3xl">
      <h1 className="text-2xl font-bold text-slate-900 mb-1">Services</h1>
      <p className="text-sm text-slate-500 mb-6">
        Parts of the deployment that hold memory while nobody uses them.
        Switching one off stops its containers on the server; the application
        keeps working without it.
      </p>
      {list.isLoading && <p>Loading...</p>}
      {list.error && (
        <p className="text-red-600">{(list.error as Error).message}</p>
      )}
      <div className="space-y-4">
        {list.data?.map((s) => (
          <ServiceCard key={s.name} service={s} />
        ))}
      </div>
    </div>
  );
}

function ServiceCard({ service }: { service: ManagedService }) {
  const qc = useQueryClient();
  const [autoOff, setAutoOff] = useState("4");
  const about = ABOUT[service.name] ?? {
    title: service.name,
    what: "",
    whileOff: "",
  };
  const status = describe(service);
  const on = service.desired_state === "on";
  const selectId = `auto-off-${service.name}`;

  const flip = useMutation({
    mutationFn: (vars: { on: boolean; autoOffHours: number | null }) =>
      api.put<ManagedService>(`/admin/services/${service.name}`, {
        on: vars.on,
        auto_off_hours: vars.autoOffHours,
      }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["managed-services"] }),
  });

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-4">
        <CardTitle>{about.title}</CardTitle>
        <span
          className={cn(
            "rounded-full border px-2.5 py-0.5 text-xs font-medium",
            TONE_CLASSES[status.tone],
          )}
        >
          {status.label}
        </span>
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        {about.what && <p className="text-slate-600">{about.what}</p>}

        {status.note && (
          <p className="rounded-md border border-red-200 bg-red-50 p-3 text-red-800 break-words">
            {status.note}
          </p>
        )}
        {!on && about.whileOff && (
          <p className="rounded-md border border-amber-200 bg-amber-50 p-3 text-amber-800">
            {about.whileOff}
          </p>
        )}
        {on && service.off_at && (
          <p className="text-slate-600">
            Switches itself off at {when(service.off_at)}.
          </p>
        )}

        <div className="flex flex-wrap items-end gap-3">
          {on ? (
            <Button
              variant="outline"
              disabled={flip.isPending}
              onClick={() => flip.mutate({ on: false, autoOffHours: null })}
            >
              Switch off
            </Button>
          ) : (
            <>
              <div>
                <LabelWithHelp
                  htmlFor={selectId}
                  label="Keep it on for"
                  help={
                    <>
                      When the time is up it stops by itself, as if you had
                      pressed Switch off. Langfuse left running because nobody
                      remembered it is the idle memory this page exists to give
                      back. Starting takes a minute or so: its databases come up
                      before Langfuse does.
                    </>
                  }
                />
                <select
                  id={selectId}
                  value={autoOff}
                  onChange={(e) => setAutoOff(e.target.value)}
                  className="h-10 rounded-md border border-slate-300 bg-white px-3 text-sm"
                >
                  {AUTO_OFF_CHOICES.map((c) => (
                    <option key={c.value} value={c.value}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </div>
              <Button
                disabled={flip.isPending}
                onClick={() =>
                  flip.mutate({
                    on: true,
                    autoOffHours: autoOff ? Number(autoOff) : null,
                  })
                }
              >
                Switch on
              </Button>
            </>
          )}
        </div>
        {flip.error && (
          <p className="text-red-600">{(flip.error as Error).message}</p>
        )}

        {service.changed_by_email ? (
          <p className="text-xs text-slate-400">
            Switched {service.desired_state} by {service.changed_by_email},{" "}
            {when(service.changed_at)}
          </p>
        ) : (
          !on && (
            <p className="text-xs text-slate-400">
              Switched off by itself when its time ran out,{" "}
              {when(service.changed_at)}
            </p>
          )
        )}
      </CardContent>
    </Card>
  );
}
