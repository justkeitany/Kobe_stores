import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  Sparkles, Send, Loader2, RefreshCw, AlertCircle, Wrench, Stethoscope, FileText, MessageSquare,
} from "lucide-react";
import toast from "react-hot-toast";
import api from "../lib/api";
import clsx from "clsx";

interface AiProvider {
  name: string;
  type: "sdk" | "cli";
  base_url: string;
  model: string;
  available: boolean;
}
interface AiStatus {
  providers: AiProvider[];
  providers_count: number;
  key_present: boolean;
  enabled: boolean;
  autonomy: "suggest" | "autofix";
  model: string;
  calls_today: number;
  daily_cap: number;
}
interface TestResult {
  name: string; type: string; base_url: string;
  ok: boolean; latency_ms?: number; reply?: string; error?: string;
}
interface AiEvent {
  id: number;
  kind: "diagnosis" | "action" | "digest" | "chat";
  stream_id: number | null;
  title: string;
  detail: string | null;
  data: any;
  created_at: string;
}

function timeAgo(iso: string): string {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 90) return "just now";
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

const KIND_ICON = {
  diagnosis: Stethoscope,
  action: Wrench,
  digest: FileText,
  chat: MessageSquare,
} as const;

export default function AIPage() {
  const qc = useQueryClient();

  const { data: status } = useQuery<AiStatus>({
    queryKey: ["ai-status"],
    queryFn: () => api.get("/ai/status").then((r) => r.data),
    refetchInterval: 30_000,
  });

  const { data: events = [] } = useQuery<AiEvent[]>({
    queryKey: ["ai-events"],
    queryFn: () => api.get("/ai/events?limit=40").then((r) => r.data),
    refetchInterval: 20_000,
  });

  const { data: digest } = useQuery<{ detail: string | null; created_at?: string }>({
    queryKey: ["ai-digest"],
    queryFn: () => api.get("/ai/digest").then((r) => r.data),
  });

  const setMut = useMutation({
    mutationFn: (body: { enabled?: boolean; autonomy?: string }) => api.put("/ai/settings", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["ai-status"] }),
    onError: () => toast.error("Could not update AI settings"),
  });

  const digestMut = useMutation({
    mutationFn: () => api.post("/ai/digest/run").then((r) => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-digest"] });
      qc.invalidateQueries({ queryKey: ["ai-events"] });
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Digest failed"),
  });

  const [tests, setTests] = useState<TestResult[] | null>(null);
  const testMut = useMutation({
    mutationFn: () => api.post("/ai/test").then((r) => r.data.results as TestResult[]),
    onSuccess: (r) => { setTests(r); qc.invalidateQueries({ queryKey: ["ai-status"] }); },
    onError: () => toast.error("Test failed"),
  });

  const keyMissing = status && status.providers_count === 0;

  return (
    <div className="p-lg space-y-md max-w-[1100px]">
      <div className="flex items-center gap-2">
        <Sparkles size={20} className="text-primary" />
        <h2 className="text-lg font-bold tracking-tight">AI Assistant</h2>
        {status && (
          <span className="ml-2 text-[11px] text-on-surface-variant font-code-label">
            {status.model} · {status.calls_today}/{status.daily_cap} calls today
          </span>
        )}
      </div>

      {keyMissing && (
        <div className="flex items-start gap-2 badge-yellow rounded-md px-3 py-2 text-[13px]">
          <AlertCircle size={16} className="shrink-0 mt-0.5" />
          <div>
            <p className="font-bold">No API key configured.</p>
            <p>Add <code className="font-mono">ANTHROPIC_API_KEY=sk-ant-…</code> to{" "}
              <code className="font-mono">backend/.env</code> on the server and restart the panel.
              Don’t paste the key in chat or commit it.</p>
          </div>
        </div>
      )}

      {/* Controls */}
      {status && (
        <div className="bg-surface-container-low border border-outline-variant rounded-md p-md flex flex-wrap items-center gap-lg">
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="checkbox"
              checked={status.enabled}
              disabled={!status.key_present || setMut.isPending}
              onChange={(e) => setMut.mutate({ enabled: e.target.checked })}
              className="w-4 h-4 accent-[var(--color-primary)] cursor-pointer"
            />
            <span className="text-body-sm font-medium">Enabled</span>
          </label>

          <div className="flex items-center gap-2">
            <span className="text-body-sm text-on-surface-variant">Autonomy</span>
            <div className="flex border border-outline-variant rounded-md overflow-hidden">
              {(["suggest", "autofix"] as const).map((m) => (
                <button
                  key={m}
                  disabled={!status.key_present}
                  onClick={() => setMut.mutate({ autonomy: m })}
                  className={clsx(
                    "px-3 py-1 text-[12px] font-medium transition-colors",
                    status.autonomy === m
                      ? "bg-primary text-on-primary"
                      : "text-on-surface-variant hover:bg-surface-container"
                  )}
                >
                  {m === "suggest" ? "Suggest only" : "Auto-fix safe"}
                </button>
              ))}
            </div>
          </div>
          <p className="text-[11px] text-on-surface-variant max-w-xs">
            {status.autonomy === "autofix"
              ? "AI applies reversible fixes (switch source, drop quality, disable dead) and logs them."
              : "AI only diagnoses and recommends — nothing changes without you."}
          </p>
        </div>
      )}

      {/* Providers + failover */}
      {status && status.providers.length > 0 && (
        <div className="bg-surface-container-low border border-outline-variant rounded-md p-md">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-bold text-body-sm">Providers <span className="text-on-surface-variant font-normal">(failover order)</span></h3>
            <button className="btn-secondary text-[12px] py-1" onClick={() => testMut.mutate()} disabled={testMut.isPending}>
              {testMut.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Test all
            </button>
          </div>
          <div className="space-y-1.5">
            {status.providers.map((p, i) => {
              const t = tests?.find((r) => r.name === p.name);
              return (
                <div key={p.name} className="flex items-center gap-2.5 text-[13px]">
                  <span className="text-on-surface-variant/60 w-4 text-right">{i + 1}</span>
                  <span className={clsx("w-2 h-2 rounded-full shrink-0",
                    t ? (t.ok ? "bg-[#5edc8a]" : "bg-[#ffb4ab]") : (p.available ? "bg-on-surface-variant/40" : "bg-[#f5c86e]"))} />
                  <span className="font-medium">{p.name}</span>
                  <span className="text-[10px] font-code-label uppercase border border-outline-variant rounded px-1.5 py-0.5 text-on-surface-variant">{p.type}</span>
                  <span className="text-on-surface-variant truncate font-mono text-[11px]">{p.base_url}</span>
                  <span className="ml-auto text-[11px] text-on-surface-variant shrink-0">
                    {t ? (t.ok ? `OK · ${t.latency_ms}ms` : (t.error || "down")) : (p.available ? "" : "cooling down")}
                  </span>
                </div>
              );
            })}
          </div>
          <p className="text-[11px] text-on-surface-variant mt-2">
            Calls try providers top-to-bottom; a failed one is skipped for ~2 min and the next is used automatically.
          </p>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-gutter">
        <ChatBox disabled={!status?.enabled} />

        {/* Digest */}
        <div className="bg-surface-container-low border border-outline-variant rounded-md p-md flex flex-col">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-bold flex items-center gap-1.5"><FileText size={15} /> Daily digest</h3>
            <button
              className="btn-secondary text-[12px] py-1"
              onClick={() => digestMut.mutate()}
              disabled={!status?.enabled || digestMut.isPending}
            >
              {digestMut.isPending ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Generate
            </button>
          </div>
          {digest?.detail ? (
            <>
              <p className="text-[11px] text-on-surface-variant mb-1">{digest.created_at && timeAgo(digest.created_at)}</p>
              <p className="text-body-sm whitespace-pre-wrap leading-relaxed">{digest.detail}</p>
            </>
          ) : (
            <p className="text-on-surface-variant text-[13px]">No digest yet — generate one or wait for the daily run.</p>
          )}
        </div>
      </div>

      {/* Activity feed */}
      <div>
        <h3 className="font-bold mb-2">Recent AI activity</h3>
        {events.length === 0 ? (
          <p className="text-on-surface-variant text-[13px] py-8 text-center">
            Nothing yet. Diagnoses appear automatically when streams fail; ask a question or generate a digest above.
          </p>
        ) : (
          <div className="space-y-2">
            {events.map((e) => {
              const Icon = KIND_ICON[e.kind] ?? Sparkles;
              const autoApplied = e.data?.auto_applied;
              return (
                <div key={e.id} className="bg-surface-container-low border border-outline-variant rounded-md p-3 flex items-start gap-3">
                  <Icon size={16} className="shrink-0 mt-0.5 text-on-surface-variant" />
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-body-sm">{e.title}</span>
                      <span className="text-[10px] font-code-label uppercase text-on-surface-variant border border-outline-variant rounded px-1.5 py-0.5">
                        {e.kind}
                      </span>
                      {e.kind === "diagnosis" && e.data?.severity && (
                        <span className={clsx("text-[10px] rounded px-1.5 py-0.5",
                          e.data.severity === "high" ? "badge-red" : e.data.severity === "medium" ? "badge-yellow" : "badge-gray")}>
                          {e.data.severity}
                        </span>
                      )}
                      {autoApplied && <span className="badge-green text-[10px]">auto-fixed</span>}
                    </div>
                    {e.detail && <p className="text-[13px] text-on-surface-variant mt-0.5 whitespace-pre-wrap">{e.detail}</p>}
                    {e.kind === "diagnosis" && e.data?.recommended_action && e.data.recommended_action !== "none" && !autoApplied && (
                      <ApplyButton streamId={e.stream_id} action={e.data.recommended_action} />
                    )}
                  </div>
                  <span className="text-[10px] text-on-surface-variant/70 shrink-0">{timeAgo(e.created_at)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

function ApplyButton({ streamId, action }: { streamId: number | null; action: string }) {
  const qc = useQueryClient();
  const mut = useMutation({
    mutationFn: () => api.post("/ai/apply", { stream_id: streamId, action }),
    onSuccess: () => {
      toast.success(`Applied: ${action}`);
      qc.invalidateQueries({ queryKey: ["ai-events"] });
      qc.invalidateQueries({ queryKey: ["streams"] });
    },
    onError: (e: any) => toast.error(e?.response?.data?.detail || "Could not apply"),
  });
  if (!streamId) return null;
  return (
    <button
      onClick={() => mut.mutate()}
      disabled={mut.isPending}
      className="mt-1.5 inline-flex items-center gap-1 text-[12px] font-medium border border-outline-variant rounded px-2 py-1 hover:bg-surface-container transition-colors"
    >
      {mut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Wrench size={12} />}
      Apply: {action.replace("_", " ")}
    </button>
  );
}

function ChatBox({ disabled }: { disabled: boolean }) {
  const [q, setQ] = useState("");
  const [log, setLog] = useState<{ role: "you" | "ai"; text: string }[]>([]);
  const mut = useMutation({
    mutationFn: (question: string) => api.post("/ai/chat", { question }).then((r) => r.data.answer as string),
    onSuccess: (answer) => setLog((l) => [...l, { role: "ai", text: answer }]),
    onError: () => setLog((l) => [...l, { role: "ai", text: "Request failed — check the backend logs." }]),
  });

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const question = q.trim();
    if (!question || mut.isPending) return;
    setLog((l) => [...l, { role: "you", text: question }]);
    setQ("");
    mut.mutate(question);
  }

  return (
    <div className="bg-surface-container-low border border-outline-variant rounded-md p-md flex flex-col h-[22rem]">
      <h3 className="font-bold flex items-center gap-1.5 mb-2"><MessageSquare size={15} /> Ask about your system</h3>
      <div className="flex-1 overflow-y-auto space-y-2 mb-2 pr-1">
        {log.length === 0 && (
          <p className="text-on-surface-variant text-[13px]">
            e.g. “Which streams are down and why?”, “Why is HGTV buffering?”, “How many viewers right now?”
          </p>
        )}
        {log.map((m, i) => (
          <div key={i} className={clsx("text-body-sm", m.role === "you" ? "text-right" : "")}>
            <span className={clsx(
              "inline-block rounded-lg px-3 py-1.5 max-w-[85%] whitespace-pre-wrap text-left",
              m.role === "you" ? "bg-primary text-on-primary" : "bg-surface-container"
            )}>
              {m.text}
            </span>
          </div>
        ))}
        {mut.isPending && <Loader2 size={16} className="animate-spin text-on-surface-variant" />}
      </div>
      <form onSubmit={submit} className="flex gap-2">
        <input
          className="input flex-1"
          placeholder={disabled ? "Enable AI to chat…" : "Ask a question…"}
          value={q}
          disabled={disabled || mut.isPending}
          onChange={(e) => setQ(e.target.value)}
        />
        <button className="btn-primary px-3" disabled={disabled || mut.isPending || !q.trim()}>
          <Send size={16} />
        </button>
      </form>
    </div>
  );
}
