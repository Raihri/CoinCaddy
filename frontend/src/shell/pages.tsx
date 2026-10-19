import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import type { Destination } from "./navigation";

type Check = { status: string; detail?: string; schema_revision?: string; mode?: string };
type Ready = { status: string; checks: Record<string, Check>; checked_at: string };

const CHECK_LABELS: Record<string, string> = {
  api: "API process",
  database: "PostgreSQL schema",
  mock_services: "Mock bank/lender boundary",
};

export function SystemStatusPage() {
  const [ready, setReady] = useState<Ready | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => {
    setError(null);
    fetch("/api/health/ready")
      .then(async (r) => {
        const body = (await r.json()) as Ready;
        setReady({ ...body, checks: { api: { status: "ok" }, ...body.checks } });
      })
      .catch(() => {
        setReady(null);
        setError("The API did not respond. Start it with the commands in README.md.");
      });
  };
  useEffect(load, []);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">System status</h1>
          <p className="page-sub">Development foundation only. No finance feature is built yet.</p>
        </div>
        <button type="button" className="btn btn-primary" onClick={load}>Re-check</button>
      </div>

      <section className="card" aria-labelledby="health-title">
        <h2 id="health-title" className="card-title">Health checks</h2>
        {error && <p className="badge badge-danger" role="alert">{error}</p>}
        {!ready && !error && <p className="muted">Checking…</p>}
        {ready && (
          <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Component</th><th>Status</th><th>Detail</th></tr>
            </thead>
            <tbody>
              {Object.entries(ready.checks).map(([name, c]) => (
                <tr key={name}>
                  <td>{CHECK_LABELS[name] ?? name}</td>
                  <td>
                    <span className={`badge ${c.status === "ok" ? "badge-success" : "badge-danger"}`}>
                      {c.status === "ok" ? "Healthy" : "Error"}
                    </span>
                  </td>
                  <td className="muted">{c.schema_revision ?? c.mode ?? c.detail ?? "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        )}
        {ready && <p className="muted small">Checked {new Date(ready.checked_at).toLocaleString()}</p>}
      </section>
    </>
  );
}

export function UnavailablePage({ destination }: { destination: Destination }) {
  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">{destination.label}</h1>
          <p className="page-sub">This destination is not available yet.</p>
        </div>
      </div>
      <section className="card">
        <span className="badge badge-info">Unavailable</span>
        <p>
          {destination.label} has not been implemented. It is planned in {destination.plannedIn} of
          build_prompts.md. No figures are shown here because none have been calculated.
        </p>
        <Link to="/" className="link">Back to system status</Link>
      </section>
    </>
  );
}

export function NotFoundPage() {
  return (
    <section className="card">
      <h1 className="page-title">Page not found</h1>
      <Link to="/" className="link">Back to system status</Link>
    </section>
  );
}
