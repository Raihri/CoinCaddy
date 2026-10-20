import { useEffect, useState } from "react";
import { api } from "../api";
import { useSession } from "../auth/session";
import { exponentOf, Field } from "../settings/fields";
import { formatDateWith, formatMoneyWith } from "./format";
import { useRegion, type PackDetail, type RegionView } from "./RegionContext";

const SAMPLE_MINOR = 123456789;
const SAMPLE_DATE = "2026-10-05";
const FLASH_KEY = "region-pack-flash";
const SOURCE_TEXT: Record<RegionView["source"], string> = {
  selected: "Chosen by an owner",
  default: "Default for the business's jurisdiction",
  none: "No pack for this jurisdiction",
};

function sample(p: PackDetail): string {
  return formatMoneyWith(p, SAMPLE_MINOR, p.currency, p.minor_unit_exponent);
}

function mismatch(p: PackDetail, view: RegionView, businessJurisdiction: string): string[] {
  const out: string[] = [];
  if (p.jurisdiction !== view.business.jurisdiction) {
    out.push(`This pack is for ${p.name}, but the business is set up in ${businessJurisdiction}. Funding Matches will list the pack's sample products as not offered in ${view.business.jurisdiction}.`);
  }
  if (p.currency !== view.business.base_currency) {
    out.push(`The pack's currency is ${p.currency}, but the business keeps its books in ${view.business.base_currency}. Saved amounts stay in ${view.business.base_currency}; nothing is converted.`);
  }
  return out;
}

export function RegionPackCard() {
  const { active, profile, options } = useSession();
  const { view, setView, reload } = useRegion();
  const [target, setTarget] = useState<string>("");
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    try {
      const f = sessionStorage.getItem(FLASH_KEY);
      if (f) {
        setFlash(f);
        sessionStorage.removeItem(FLASH_KEY);
      }
    } catch {
      /* storage can be unavailable; the message is a convenience only */
    }
    if (window.location.hash === "#region-pack") document.getElementById("region-pack")?.scrollIntoView();
  }, []);

  if (!active || !profile) return null;
  if (!view) {
    return (
      <section id="region-pack" className="card" aria-labelledby="region-title">
        <h2 id="region-title" className="card-title">Region pack</h2>
        <p className="muted">The region pack could not be loaded. The built-in display is being used.</p>
      </section>
    );
  }

  const pack = view.active;
  const chosen = view.available.find((p) => p.id === target) ?? null;
  const currentId = pack?.id ?? null;
  const canSwitch = view.can_select && active.allowed_actions.includes("regionpack.select");
  const pending = chosen && !(chosen.id === currentId && view.source === "selected" && !view.outdated) ? chosen : null;
  const exp = exponentOf(options, profile.base_currency);

  const confirm = async () => {
    if (!chosen) return;
    setBusy(true);
    setError(null);
    const r = await api<RegionView>(`/api/businesses/${active.business_id}/region-pack`, "PUT", {
      pack_id: chosen.id,
      expected_current_pack_id: currentId,
    });
    setBusy(false);
    if (!r.ok) {
      setError(r.message);
      if (r.status === 409) {
        setConfirming(false);
        await reload();
      }
      return;
    }
    try {
      sessionStorage.setItem(FLASH_KEY, `Region pack switched to ${chosen.name} (${chosen.version}). Saved amounts were not changed.`);
    } catch {
      /* optional */
    }
    setView(r.data);
  };

  return (
    <section id="region-pack" className="card" aria-labelledby="region-title">
      <div className="card-head">
        <h2 id="region-title" className="card-title">Region pack</h2>
        {pack && <span className="badge badge-info">{pack.version}</span>}
      </div>
      {flash && <p className="badge badge-success" role="status">{flash}</p>}
      <p className="muted small">
        A region pack sets how amounts and dates are written, the import templates on offer, and the sample financing catalogue. It does not change any saved amount.
      </p>

      {pack ? (
        <dl className="facts">
          <div><dt>Active pack</dt><dd>{pack.name} ({pack.jurisdiction})</dd></div>
          <div><dt>How it was chosen</dt><dd>{SOURCE_TEXT[view.source]}</dd></div>
          <div><dt>Currency</dt><dd>{pack.currency}</dd></div>
          <div><dt>Language</dt><dd>{pack.language}</dd></div>
          <div><dt>Amount example</dt><dd className="num">{sample(pack)}</dd></div>
          <div><dt>Date example</dt><dd className="num">{formatDateWith(pack, SAMPLE_DATE)}</dd></div>
          <div><dt>Sample catalogue</dt><dd>{pack.catalogue.version}</dd></div>
          <div><dt>Import templates</dt><dd>{pack.import_templates.length}</dd></div>
        </dl>
      ) : (
        <p className="badge badge-warning">No region pack applies to {view.business.jurisdiction}; the built-in display is used.</p>
      )}
      {view.outdated && (
        <p className="badge badge-warning block">A newer version of this pack is installed (chosen at {view.selected_version}). Choose it again to use the latest.</p>
      )}
      {view.warnings.map((w) => <p key={w} className="badge badge-warning block" role="note">{w}</p>)}
      <p className="muted small">{view.no_conversion_note}</p>
      {pack && <p className="muted small">{pack.notice}</p>}

      {pack && (
        <details className="region-details">
          <summary>Labels, import templates and sample catalogue in this pack</summary>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Term</th><th>Shown as</th></tr></thead>
              <tbody>{Object.entries(pack.labels).map(([k, v]) => <tr key={k}><td>{k.replace(/_/g, " ")}</td><td>{v}</td></tr>)}</tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Import template</th><th>Date format</th><th>Columns it looks for</th></tr></thead>
              <tbody>
                {pack.import_templates.map((t) => (
                  <tr key={t.id}>
                    <td className="wrap"><strong>{t.name}</strong><span className="muted small block">{t.description}</span></td>
                    <td>{t.date_format}</td>
                    <td className="wrap">{Object.values(t.columns).join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead><tr><th>Sample product</th><th>Provider</th><th>Market</th></tr></thead>
              <tbody>
                {pack.catalogue.products.map((p) => (
                  <tr key={p.id}><td>{p.name}</td><td>{p.provider}</td><td>{p.jurisdictions.join(", ")} · {p.currencies.join(", ")}</td></tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">{pack.catalogue.label}. Updated {formatDateWith(pack, pack.catalogue.updated)}. {pack.catalogue.source}</p>
        </details>
      )}

      {canSwitch ? (
        <div className="region-switch">
          <Field label="Switch region pack" hint="Only an owner can switch. The server checks this as well.">
            <select
              className="select"
              value={target}
              onChange={(e) => {
                setTarget(e.target.value);
                setConfirming(false);
                setError(null);
              }}
            >
              <option value="">Choose a pack</option>
              {view.available.map((p) => (
                <option key={p.id} value={p.id}>{p.name} ({p.jurisdiction}) · {p.version}{p.id === currentId ? " · active" : ""}</option>
              ))}
            </select>
          </Field>
          {chosen && !pending && <p className="muted small">{chosen.name} is already the active pack.</p>}
          {pending && (
            <div className="region-preview" aria-live="polite">
              <p className="card-title">Preview: {pending.name} ({pending.version})</p>
              <dl className="facts">
                <div><dt>Amount example</dt><dd className="num">{sample(pending)}</dd></div>
                <div>
                  <dt>This business's reserve</dt>
                  <dd className="num">{formatMoneyWith(pending, profile.reserve.amount_minor, profile.reserve.currency, exp)}</dd>
                </div>
                <div><dt>Date example</dt><dd className="num">{formatDateWith(pending, SAMPLE_DATE)}</dd></div>
                <div><dt>Sample catalogue</dt><dd>{pending.catalogue.version} · {pending.catalogue.products.length} products</dd></div>
              </dl>
              {mismatch(pending, view, profile.jurisdiction_name).map((w) => <p key={w} className="badge badge-warning block">{w}</p>)}
              <p className="muted small">{view.history_note}</p>
              {error && <p className="badge badge-danger block" role="alert">{error}</p>}
              <div className="form-actions">
                <button type="button" className="btn btn-ghost" disabled={busy} onClick={() => { setConfirming(false); setTarget(""); setError(null); }}>Cancel</button>
                {!confirming ? (
                  <button type="button" className="btn btn-primary" onClick={() => setConfirming(true)}>Review switch</button>
                ) : (
                  <button type="button" className="btn btn-primary" disabled={busy} onClick={confirm}>
                    {busy ? "Switching…" : `Confirm switch to ${pending.name}`}
                  </button>
                )}
              </div>
            </div>
          )}
        </div>
      ) : (
        <p className="badge badge-info">Only an owner can switch the region pack.</p>
      )}

      {view.history.length > 0 && (
        <div className="table-wrap">
          <table className="table">
            <caption className="region-caption">Pack history</caption>
            <thead><tr><th>When</th><th>Pack</th><th>Changed from</th><th>By</th></tr></thead>
            <tbody>
              {view.history.map((h) => (
                <tr key={`${h.selected_at}-${h.pack_id}`}>
                  <td className="nowrap">{new Date(h.selected_at).toLocaleString("en-GB", { timeZone: profile.timezone })}</td>
                  <td>{h.pack_id.toUpperCase()} · {h.pack_version}</td>
                  <td>{h.previous_pack_id ? h.previous_pack_id.toUpperCase() : "None"}</td>
                  <td>{h.selected_by}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
