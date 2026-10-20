import { useCallback, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, fmtDate } from "../api";
import { useSession } from "../auth/session";
import { formatMoney } from "../money";
import { exponentOf } from "../settings/fields";

// Every value shown here comes from the server's calculated facts and is formatted by this code. The answer text only
// refers to facts by placeholder ([[F3]]); it never carries a number of its own.
type Fact = { id: string; label: string; kind: "money" | "date" | "month" | "count" | "percent_bp" | "text" | "status"; source: string | null;
  amount_minor?: number; currency?: string; value?: string | number | null; untrusted?: boolean };
type Caveat = { code: string; severity: "warning" | "info"; message: string; source: string | null };
type Source = { ref: string; kind: string; id: string; label: string; href: string; untrusted_label: boolean;
  calc_version?: string; inputs_sha256?: string; catalogue_version?: string; count?: number };
type Exchange = { id: string; created_at: string; question: string; status: string; tool: string | null; assistant: "template" | "model";
  tool_calls: { tool: string; args: Record<string, string>; outcome: string }[];
  answer: { title: string; text: string; drafted_by: "template" | "model"; reason: string | null; facts: Fact[]; caveats: Caveat[]; sources: Source[] } };
type ToolInfo = { name: string; title: string; description: string; allowed: boolean };
type Catalogue = { read_only: boolean; assistant: "template" | "model"; tools: ToolInfo[]; suggestions: { topic: string; question: string }[]; question_max: number };

const STATUS: Record<string, [string, string]> = {
  answered: ["Answered", "badge-success"], missing_evidence: ["Missing evidence", "badge-warning"], not_found: ["Not found", "badge-danger"],
  not_allowed: ["Not allowed for your role", "badge-danger"], read_only: ["Read-only", "badge-info"], not_supported: ["Not supported", "badge-muted"],
};
const SOURCE_KIND: Record<string, string> = {
  financial_health: "Financial Health", ledger_transactions: "Posted transactions", forecast_snapshot: "Outlook snapshot",
  payment_review: "Payment Review", funding_match_run: "Funding Match", obligation: "Invoice or bill",
};

export function FinanceChatPage() {
  const { active, options } = useSession();
  const base = active ? `/api/businesses/${active.business_id}/chat` : "";
  const canAsk = active?.allowed_actions.includes("chat.ask") ?? false;
  const [catalogue, setCatalogue] = useState<Catalogue | null>(null);
  const [history, setHistory] = useState<Exchange[]>([]);
  const [question, setQuestion] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const max = catalogue?.question_max ?? 500;

  const load = useCallback(async () => {
    if (!base || !canAsk) return;
    const [c, h] = await Promise.all([api<Catalogue>(`${base}/tools`), api<Exchange[]>(`${base}/questions`)]);
    if (c.ok) setCatalogue(c.data); else setError(c.message);
    if (h.ok) setHistory(h.data);
  }, [base, canAsk]);
  useEffect(() => { setHistory([]); setCatalogue(null); load(); }, [load]);

  const ask = async (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (q.length < 2) return setError("Type a question first.");
    if (q.length > max) return setError(`Keep the question under ${max} characters.`);
    setBusy(true); setError(null);
    const r = await api<Exchange>(`${base}/questions`, "POST", topic ? { question: q, topic } : { question: q });
    setBusy(false);
    if (!r.ok) return setError(r.message);
    setHistory((old) => [r.data, ...old]);
    setQuestion(""); setTopic(null);
  };

  const fill = (s: { topic: string; question: string }) => { setQuestion(s.question); setTopic(s.topic); setError(null); };
  const value = (f: Fact): ReactNode => {
    switch (f.kind) {
      case "money": return formatMoney(f.amount_minor ?? 0, f.currency ?? "BDT", exponentOf(options, f.currency ?? "BDT"));
      case "date": return fmtDate(String(f.value));
      case "month": return new Date(`${f.value}-01T00:00:00`).toLocaleDateString("en-GB", { month: "long", year: "numeric" });
      case "percent_bp": return `${(Number(f.value) / 100).toFixed(2)}%`;
      case "count": return String(f.value);
      default: return String(f.value ?? "—");
    }
  };

  return <>
    <div className="page-header">
      <div><h1 className="page-title">Finance Chat</h1></div>
      <span className="badge badge-info">Read-only</span>
    </div>
    {!canAsk && <section className="card"><p>Your role cannot use Finance Chat in this business.</p></section>}
    {canAsk && <div className="chat-grid">
      <div className="stack">
        <section className="card" aria-labelledby="ask-title">
          <h2 id="ask-title" className="card-title">Ask a question</h2>
          <form className="chat-form" onSubmit={ask}>
            <label className="sr-only" htmlFor="chat-question">Question</label>
            <textarea id="chat-question" rows={2} value={question} maxLength={max} placeholder="For example: when is cash lowest in the next 13 weeks?"
              onChange={(e) => { setQuestion(e.target.value); setTopic(null); }}
              onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); e.currentTarget.form?.requestSubmit(); } }} />
            <div className="chat-form-foot">
              <span className="muted small num">{question.trim().length}/{max}</span>
              <button className="btn btn-primary" type="submit" disabled={busy || question.trim().length < 2}>{busy ? "Reading results…" : "Ask"}</button>
            </div>
          </form>
          {!!catalogue?.suggestions.length && <ul className="chip-list chat-suggestions" aria-label="Suggested questions">
            {catalogue.suggestions.map((s) => <li key={s.topic}><button type="button" className="chip chat-chip" onClick={() => fill(s)}>{s.question}</button></li>)}
          </ul>}
          {error && <p className="badge badge-danger chat-error" role="alert">{error}</p>}
        </section>

        {history.length === 0
          ? <section className="card empty-state"><p className="muted">No questions yet. Pick a suggestion above to start.</p></section>
          : <div className="chat-list" aria-live="polite">{history.map((x) => <Answer key={x.id} x={x} value={value} />)}</div>}
      </div>

      <aside className="card chat-side" aria-labelledby="reads-title">
        <h2 id="reads-title" className="card-title">What it reads</h2>
        <ul className="chat-tools">
          {catalogue?.tools.map((t) => <li key={t.name}>
            <div className="chat-tool-head"><strong>{t.title}</strong>
              <span className={`badge ${t.allowed ? "badge-success" : "badge-muted"}`}>{t.allowed ? "Available" : "Not for your role"}</span></div>
          </li>)}
        </ul>      </aside>
    </div>}
  </>;
}

function Answer({ x, value }: { x: Exchange; value: (f: Fact) => ReactNode }) {
  const a = x.answer;
  const facts = new Map(a.facts.map((f) => [f.id, f]));
  const [label, cls] = STATUS[x.status] ?? [x.status, "badge-muted"];
  const parts = a.text.split(/(\[\[F\d+\]\])/g);
  // The caveat that the answer text already states is not repeated underneath it.
  const warnings = a.caveats.filter((c) => c.severity === "warning" && c.code !== a.reason);
  const notes = a.caveats.filter((c) => c.severity === "info");
  return <article className="card chat-answer">
    <header className="chat-answer-head">
      <p className="chat-q"><span className="sr-only">You asked: </span>{x.question}</p>
      <span className="muted small nowrap">{new Date(x.created_at).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })}</span>
    </header>
    <div className="badges">
      <span className={`badge ${cls}`}>{label}</span>
      {x.tool && <span className="badge badge-muted">{a.title}</span>}
      {a.drafted_by === "model" && <span className="badge badge-info" title="Wording drafted by AI; every value is calculated by the app">AI Suggested</span>}
    </div>
    <p className="chat-text">{parts.map((p, i) => {
      const m = /^\[\[(F\d+)\]\]$/.exec(p);
      const f = m ? facts.get(m[1]) : undefined;
      return f ? <strong key={i} className="num">{value(f)}</strong> : <span key={i}>{p}</span>;
    })}</p>
    {warnings.length > 0 && <ul className="chat-caveats" aria-label="Check before relying on this">
      {warnings.map((c) => <li key={c.code}><span className="badge badge-warning">Check</span><span>{c.message}</span></li>)}
    </ul>}
    {notes.length > 0 && <p className="muted small chat-notes">{notes.map((c) => c.message).join(" ")}</p>}
    {(a.facts.length > 0 || a.sources.length > 0) && <div className="chat-more">
    {a.facts.length > 0 && <details className="chat-sources">
      <summary>Calculated figures ({a.facts.length})</summary>
      <dl className="facts chat-facts">
        {a.facts.map((f) => <div key={f.id}><dt>{f.label}</dt>
          <dd className={f.kind === "money" || f.kind === "count" || f.kind === "percent_bp" ? "num" : undefined}>
            {value(f)}{f.untrusted && <span className="badge badge-muted tiny-badge chat-from-records">From records</span>}</dd></div>)}
      </dl>
    </details>}
    {a.sources.length > 0 && <details className="chat-sources">
      <summary>Sources ({a.sources.length})</summary>
      <ul>{a.sources.map((s) => <li key={s.ref}>
        <Link className="link" to={s.href}>{s.label}</Link>
        <span className="muted small"> · {SOURCE_KIND[s.kind] ?? s.kind}
          {s.calc_version ? ` · ${s.calc_version}` : ""}{s.catalogue_version ? ` · ${s.catalogue_version}` : ""}
          {s.inputs_sha256 ? <> · inputs <span className="hash">{s.inputs_sha256.slice(0, 12)}</span></> : null}</span>
      </li>)}</ul>
    </details>}
    </div>}
  </article>;
}
