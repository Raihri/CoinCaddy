import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { CHECK_LABELS } from "../approvals/types";
import { EVENT_BADGE, KIND_LABEL, type Adapters, type LogEntry } from "./types";

const PAGE = 10;

// What each log line says, built from structured fields (the server stores codes and redacted values, not prose).
function summary(x: LogEntry): string {
  const note = typeof x.result?.note === "string" ? x.result.note : null;
  switch (x.event) {
    case "blocked":
      return "Nothing sent. Failed: " + (x.checks ?? []).filter((c) => !c.ok).map((c) => CHECK_LABELS[c.code] ?? c.code).join(", ");
    case "dispatched":
      return `Sent to ${x.adapter_label ?? "the sandbox"} after ${(x.checks ?? []).length} checks passed`;
    case "succeeded":
      return `${x.reference}${note ? ` · ${note}` : ""}`;
    case "replayed":
      return `Same key: stored outcome returned (${x.reference ?? String(x.result?.outcome ?? "")}); nothing sent again`;
    case "rejected":
      return note ?? "The sandbox refused the request";
    case "failed":
      return note ?? "The sandbox did not answer";
  }
}

// Competition evidence: every executor attempt with its time, redacted request and result, and the checks that ran.
// Extra to images/08-approvals-and-audit.png, so it sits below the mockup layout.
export function ExecutionLog({ base, adapters, refreshKey, fmtTime }: {
  base: string; adapters: Adapters | null; refreshKey: number; fmtTime: (iso: string) => string;
}) {
  const [items, setItems] = useState<LogEntry[]>([]);
  const [next, setNext] = useState<number | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [open, setOpen] = useState<string | null>(null);

  const load = useCallback(async (before: number | null) => {
    const r = await api<{ items: LogEntry[]; next_before_seq: number | null }>(`${base}/sandbox/executions?limit=${PAGE}${before ? `&before_seq=${before}` : ""}`);
    if (!r.ok) return setStatus("error");
    setItems((prev) => (before ? [...prev, ...r.data.items] : r.data.items));
    setNext(r.data.next_before_seq);
    setStatus("ready");
  }, [base]);
  useEffect(() => { load(null); }, [load, refreshKey]);

  return (
    <section className="card exec-card" aria-labelledby="exec-title">
      <div className="card-head">
        <h2 id="exec-title" className="section-title">Sandbox execution log</h2>
        <button type="button" className="btn btn-ghost btn-small" onClick={() => load(null)}>Refresh</button>
      </div>
      {adapters && (
        <ul className="adapter-list" aria-label="Sandbox adapters">
          {adapters.adapters.map((a) => (
            <li key={a.code} className="adapter-item">
              <div className="adapter-body">
                <span className="adapter-name">{a.label}</span>
                <code className="muted small">{a.endpoint}</code>
              </div>
              <span className={`badge ${a.available ? "badge-success" : "badge-muted"}`} title={a.note || undefined}>{a.available ? "Ready" : "No proposal type yet"}</span>
            </li>
          ))}
        </ul>
      )}

      {status === "loading" && <p className="muted">Loading…</p>}
      {status === "error" && <p className="badge badge-danger block-badge" role="alert">Could not load the execution log.</p>}
      {status === "ready" && items.length === 0 && <p className="muted">No runs yet.</p>}
      {status === "ready" && items.length > 0 && (
        <ol className="exec-list">
          {items.map((x) => {
            const [cls, label] = EVENT_BADGE[x.event];
            return (
              <li key={x.id} className="exec-item">
                <span className={`badge ${cls} exec-badge`}>{label}</span>
                <div className="exec-body">
                  <span className="exec-title">{KIND_LABEL[x.kind] ?? x.kind} <span className="muted">({x.tier})</span></span>
                  <span className="exec-summary">{summary(x)}</span>
                  <span className="muted small">
                    <time dateTime={x.at}>{fmtTime(x.at)}</time> · {x.actor} ({x.actor_role}) · key <code title={x.idempotency_key}>{x.idempotency_key.slice(0, 12)}…</code>
                    {x.http_status ? ` · HTTP ${x.http_status}` : ""}
                  </span>
                  {open === x.id && (
                    <pre className="payload-json">{JSON.stringify({ request: x.request, result: x.result, checks: x.checks }, null, 2)}</pre>
                  )}
                </div>
                <button type="button" className="btn btn-ghost btn-small" aria-expanded={open === x.id} onClick={() => setOpen(open === x.id ? null : x.id)}>
                  {open === x.id ? "Hide" : "Details"}
                </button>
              </li>
            );
          })}
        </ol>
      )}
      {status === "ready" && next && <button type="button" className="btn btn-ghost" onClick={() => load(next)}>Load older entries</button>}
    </section>
  );
}
