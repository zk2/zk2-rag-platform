"use client";

import { use, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent } from "@/components/ui/card";
import { useAuthStore } from "@/lib/auth-store";
import { wsUrl } from "@/lib/ws";

type Msg = {
  role: "user" | "assistant";
  content: string;
  sources?: Array<{
    chunk_id: number;
    source_id: number;
    name: string;
    ordinal: number;
    score?: number;
    matched_by?: string[];
    // Set once the answer is complete: did the model actually cite this passage
    cited?: boolean;
  }>;
  usage?: { tokens_in: number; tokens_out: number; cost_usd: string; latency_ms: number };
};

export default function ChatPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const botId = Number(id);
  const { accessToken, currentOrgId } = useAuthStore();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [connected, setConnected] = useState(false);
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const bottomRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!accessToken || !currentOrgId) return;
    const ws = new WebSocket(wsUrl(`/ws/chat/${botId}`));
    wsRef.current = ws;

    ws.onopen = () => {
      ws.send(
        JSON.stringify({ type: "auth", token: accessToken, org_id: currentOrgId }),
      );
    };
    ws.onmessage = (ev) => {
      const data = JSON.parse(ev.data);
      switch (data.type) {
        case "ready":
          setConnected(true);
          break;
        case "conversation":
          setConversationId(data.id);
          break;
        case "sources":
          setMessages((m) => {
            const last = m[m.length - 1];
            if (last && last.role === "assistant") {
              return [...m.slice(0, -1), { ...last, sources: data.items }];
            }
            return [...m, { role: "assistant", content: "", sources: data.items }];
          });
          break;
        case "citations": {
          // The answer names the passages it used; mark those, dim the rest
          const cited = new Set<number>(
            (data.items as Array<{ chunk_id: number }>).map((i) => i.chunk_id),
          );
          setMessages((m) => {
            const last = m[m.length - 1];
            if (!last || last.role !== "assistant" || !last.sources) return m;
            return [
              ...m.slice(0, -1),
              {
                ...last,
                sources: last.sources.map((s) => ({ ...s, cited: cited.has(s.chunk_id) })),
              },
            ];
          });
          break;
        }
        case "token":
          setMessages((m) => {
            const last = m[m.length - 1];
            if (last && last.role === "assistant") {
              return [
                ...m.slice(0, -1),
                { ...last, content: last.content + data.delta },
              ];
            }
            return [...m, { role: "assistant", content: data.delta }];
          });
          break;
        case "done":
          setStreaming(false);
          setMessages((m) => {
            const last = m[m.length - 1];
            if (last && last.role === "assistant") {
              return [
                ...m.slice(0, -1),
                {
                  ...last,
                  usage: {
                    tokens_in: data.tokens_in,
                    tokens_out: data.tokens_out,
                    cost_usd: data.cost_usd,
                    latency_ms: data.latency_ms,
                  },
                },
              ];
            }
            return m;
          });
          break;
        case "error":
          setStreaming(false);
          setError(data.message);
          break;
      }
    };
    ws.onclose = () => setConnected(false);
    ws.onerror = () => setError("WebSocket error");
    return () => ws.close();
  }, [accessToken, botId, currentOrgId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const send = () => {
    const ws = wsRef.current;
    if (!ws || ws.readyState !== WebSocket.OPEN || !input.trim()) return;
    setError(null);
    setMessages((m) => [...m, { role: "user", content: input }]);
    ws.send(
      JSON.stringify({
        type: "user_message",
        content: input,
        conversation_id: conversationId,
      }),
    );
    setInput("");
    setStreaming(true);
  };

  return (
    <div className="p-8 max-w-4xl space-y-4 h-screen flex flex-col">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-900">Chat · bot #{botId}</h1>
        <span
          className={
            connected ? "text-xs text-emerald-600" : "text-xs text-slate-400"
          }
        >
          {connected ? "connected" : "connecting…"}
        </span>
      </div>

      <Card className="flex-1 flex flex-col overflow-hidden">
        <CardContent className="flex-1 overflow-auto p-6 space-y-4">
          {messages.length === 0 && (
            <p className="text-slate-400 text-sm">Say hi to your bot.</p>
          )}
          {messages.map((m, i) => <MessageBubble key={i} msg={m} />)}
          {streaming && (
            <p className="text-xs text-slate-400 italic">streaming…</p>
          )}
          {error && <p className="text-sm text-red-600">{error}</p>}
          <div ref={bottomRef} />
        </CardContent>
      </Card>

      <div className="flex gap-2">
        <Textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask anything…"
          rows={2}
          className="flex-1"
        />
        <Button onClick={send} disabled={!connected || streaming || !input.trim()}>
          Send
        </Button>
      </div>
    </div>
  );
}

/** Compact marker for which retriever surfaced a chunk. */
function retrieverBadge(retriever: string): string {
  if (retriever === "dense") return "V";
  if (retriever === "bm25") return "T";
  if (retriever === "rerank") return "R";
  return "?";
}

function MessageBubble({ msg }: { msg: Msg }) {
  return (
    <div className={msg.role === "user" ? "text-right" : ""}>
      <div
        className={
          msg.role === "user"
            ? "inline-block bg-slate-900 text-white rounded-lg px-3 py-2 text-sm max-w-[80%]"
            : "inline-block bg-slate-100 text-slate-900 rounded-lg px-3 py-2 text-sm max-w-[80%] whitespace-pre-wrap"
        }
      >
        {msg.content || (msg.role === "assistant" ? "…" : "")}
      </div>
      {msg.sources && msg.sources.length > 0 && (
        <div className="mt-1 space-y-1">
          <div className="flex flex-wrap gap-1">
            {msg.sources.map((s) => (
              <span
                key={s.chunk_id}
                className={
                  s.cited
                    ? "text-[10px] bg-emerald-50 text-emerald-800 border border-emerald-300 px-1.5 py-0.5 rounded font-medium"
                    : "text-[10px] bg-slate-50 text-slate-500 border border-slate-200 px-1.5 py-0.5 rounded"
                }
                title={`${s.cited ? "cited in the answer" : "retrieved, not cited"} · score ${
                  s.score?.toFixed(4) ?? "?"
                } · found by ${s.matched_by?.join(" + ") ?? "retrieval"}`}
              >
                {s.name}#{s.ordinal}
                {s.matched_by && s.matched_by.length > 0 && (
                  <span className="ml-1 opacity-70">
                    {s.matched_by.map(retrieverBadge).join("")}
                  </span>
                )}
              </span>
            ))}
          </div>
          <div className="text-[10px] text-slate-400">
            Green = cited in the answer, grey = retrieved but unused · V dense, T lexical, R
            reranked
          </div>
        </div>
      )}
      {msg.usage && (
        <div className="mt-1 text-[10px] text-slate-400">
          {msg.usage.tokens_in}+{msg.usage.tokens_out} tok · ${msg.usage.cost_usd} ·{" "}
          {msg.usage.latency_ms} ms
        </div>
      )}
    </div>
  );
}
