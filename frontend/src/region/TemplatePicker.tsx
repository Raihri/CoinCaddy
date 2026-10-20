import { useEffect, useState } from "react";
import { api } from "../api";
import type { Mapping } from "../records/types";

type Template = {
  id: string;
  name: string;
  description: string;
  date_format: string;
  matches_file: boolean;
  usable: boolean;
  mapping: Mapping | null;
  reason: string | null;
};
type Result = { pack: { id: string; version: string; jurisdiction: string } | null; account_currency?: string; templates: Template[] };

/** Region Pack import templates, checked by the server against this file's header row. Choosing one only fills the form; the user still applies it. */
export function TemplatePicker({ base, batchId, onUse }: { base: string; batchId: string; onUse: (m: Mapping) => void }) {
  const [data, setData] = useState<Result | null>(null);
  const [used, setUsed] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setUsed(null);
    api<Result>(`${base}/region-templates`).then((r) => {
      if (live && r.ok) setData(r.data);
    });
    return () => {
      live = false;
    };
  }, [base, batchId]);

  if (!data || !data.pack || data.templates.length === 0) return null;
  return (
    <div className="template-picker" role="group" aria-label="Region pack import templates">
      <p className="card-title">Templates from the region pack ({data.pack.jurisdiction} · {data.pack.version})</p>
      <ul className="template-list">
        {data.templates.map((t) => (
          <li key={t.id} className="template-item">
            <span className="template-text">
              <strong>{t.name}</strong>
              <span className="muted small block">{t.description}</span>
              {!t.usable && t.reason && (
                <span className="muted small block">{t.matches_file ? t.reason : `Does not fit this file: ${t.reason}.`}</span>
              )}
            </span>
            <span className={`badge ${t.usable ? "badge-success" : "badge-muted"}`}>
              {t.usable ? "Fits this file" : t.matches_file ? "Wrong currency" : "Does not fit"}
            </span>
            <button
              type="button"
              className="btn btn-small btn-ghost"
              disabled={!t.usable || !t.mapping}
              onClick={() => {
                onUse(t.mapping!);
                setUsed(t.id);
              }}
            >
              {used === t.id ? "Filled in" : "Use template"}
            </button>
          </li>
        ))}
      </ul>
      <p className="muted small">A template only fills in the form below. Check it, then apply the mapping.</p>
    </div>
  );
}
