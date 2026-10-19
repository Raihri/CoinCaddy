import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { ROLE_LABELS, useSession, type Account, type BusinessProfile } from "../auth/session";
import { formatMoney, minorToMajorString, parseMajorToMinor } from "../money";
import { errorFor, readErrors, type FieldErrors } from "./apiErrors";
import { exponentOf, Field, KIND_LABELS, TimezoneInput } from "./fields";

export function BusinessSettingsPage() {
  const { active, profile, options } = useSession();
  const [editing, setEditing] = useState(false);
  const canEdit = active?.allowed_actions.includes("business.settings.update") ?? false;

  if (!active) return <section className="card">You are not a member of any business. <Link className="link" to="/businesses/new">Create one</Link>.</section>;
  if (!profile) return <p className="muted">Loading settings…</p>;

  const exp = exponentOf(options, profile.reserve.currency);

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Business settings</h1>
          <p className="page-sub">
            {profile.name} · you are {ROLE_LABELS[active.role]}
            {profile.is_synthetic && <span className="badge badge-info inline-badge">Synthetic demo business</span>}
          </p>
        </div>
        <div className="header-actions">
          <Link to="/businesses/new" className="btn btn-ghost">New business</Link>
          {canEdit && !editing && <button type="button" className="btn btn-primary" onClick={() => setEditing(true)}>Edit settings</button>}
        </div>
      </div>

      {!canEdit && <p className="badge badge-info">Only an owner can change these settings. The server enforces this as well.</p>}

      {editing ? (
        <ProfileForm profile={profile} onDone={() => setEditing(false)} />
      ) : (
        <section className="card" aria-labelledby="profile-title">
          <h2 id="profile-title" className="card-title">Profile</h2>
          <dl className="facts">
            <div><dt>Business name</dt><dd>{profile.name}</dd></div>
            <div><dt>Jurisdiction</dt><dd>{profile.jurisdiction_name} ({profile.jurisdiction})</dd></div>
            <div><dt>Base currency</dt><dd>{profile.base_currency}</dd></div>
            <div><dt>Business timezone</dt><dd>{profile.timezone}</dd></div>
            <div>
              <dt>Reserve requirement</dt>
              <dd className="num">{formatMoney(profile.reserve.amount_minor, profile.reserve.currency, exp)}</dd>
            </div>
            <div><dt>Settings version</dt><dd className="num">v{profile.version} · updated {new Date(profile.updated_at).toLocaleString("en-GB", { timeZone: profile.timezone })}</dd></div>
          </dl>
          <p className="muted small">
            The reserve is the cash level the business wants to keep. It is stored as {profile.reserve.amount_minor.toLocaleString("en-US")} minor
            units of {profile.reserve.currency}. Listing a jurisdiction is not a claim of legal compliance there.
          </p>
        </section>
      )}

      <AccountsCard profile={profile} canEdit={canEdit} />
    </>
  );
}

function ProfileForm({ profile, onDone }: { profile: BusinessProfile; onDone: () => void }) {
  const { options, setProfile, refreshMe } = useSession();
  const [name, setName] = useState(profile.name);
  const [jurisdiction, setJurisdiction] = useState(profile.jurisdiction);
  const [currency, setCurrency] = useState(profile.base_currency);
  const [timezone, setTimezone] = useState(profile.timezone);
  const [reserve, setReserve] = useState(minorToMajorString(profile.reserve.amount_minor, exponentOf(options, profile.reserve.currency)));
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const parsed = parseMajorToMinor(reserve, exponentOf(options, currency), options?.limits.max_amount_minor ?? 1e15);
    if (!parsed.ok) return setErrors({ "reserve.amount_minor": parsed.error });

    // Send only what changed; the reserve is restated whenever the currency changes.
    const body: Record<string, unknown> = { expected_version: profile.version };
    if (name !== profile.name) body.name = name;
    if (jurisdiction !== profile.jurisdiction) body.jurisdiction = jurisdiction;
    if (currency !== profile.base_currency) body.base_currency = currency;
    if (timezone !== profile.timezone) body.timezone = timezone;
    if (currency !== profile.base_currency || parsed.minor !== profile.reserve.amount_minor) {
      body.reserve = { amount_minor: parsed.minor, currency };
    }
    if (Object.keys(body).length === 1) return onDone();

    setBusy(true);
    const r = await fetch(`/api/businesses/${profile.id}/settings`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    setBusy(false);
    if (!r.ok) return setErrors(await readErrors(r));
    setProfile((await r.json()) as BusinessProfile);
    await refreshMe();
    onDone();
  };

  return (
    <form className="card form-grid" onSubmit={submit} noValidate aria-labelledby="edit-title">
      <h2 id="edit-title" className="card-title span-2">Edit profile</h2>
      {errors._form && <p className="badge badge-danger span-2" role="alert">{errors._form}</p>}
      <Field label="Business name" error={errorFor(errors, "name")}>
        <input value={name} maxLength={options?.limits.name_max ?? 120} onChange={(e) => setName(e.target.value)} />
      </Field>
      <Field label="Jurisdiction" error={errorFor(errors, "jurisdiction")}>
        <select className="select" value={jurisdiction} onChange={(e) => setJurisdiction(e.target.value)}>
          {options?.jurisdictions.map((j) => <option key={j.code} value={j.code}>{j.name} ({j.code})</option>)}
        </select>
      </Field>
      <Field label="Base currency" error={errorFor(errors, "base_currency")}
             hint={currency !== profile.base_currency ? "Re-enter the reserve in the new currency." : undefined}>
        <select className="select" value={currency} onChange={(e) => setCurrency(e.target.value)}>
          {options?.currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
        </select>
      </Field>
      <Field label="Business timezone (IANA)" error={errorFor(errors, "timezone")}>
        <TimezoneInput id="tz-edit" value={timezone} onChange={setTimezone} options={options} />
      </Field>
      <Field label={`Reserve requirement (${currency})`} error={errorFor(errors, "reserve")}
             hint={`Up to ${exponentOf(options, currency)} decimal places.`}>
        <input inputMode="decimal" value={reserve} onChange={(e) => setReserve(e.target.value)} />
      </Field>
      <div className="form-actions span-2">
        <button type="button" className="btn btn-ghost" onClick={onDone} disabled={busy}>Cancel</button>
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Save settings"}</button>
      </div>
    </form>
  );
}

function AccountsCard({ profile, canEdit }: { profile: BusinessProfile; canEdit: boolean }) {
  const { options, setProfile } = useSession();
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [rowErrors, setRowErrors] = useState<Record<string, string>>({});
  const [newAcc, setNewAcc] = useState({ label: "", kind: "bank" as Account["kind"], currency: profile.base_currency });
  const [addErrors, setAddErrors] = useState<FieldErrors>({});

  const relabel = async (acc: Account) => {
    const label = drafts[acc.id] ?? acc.label;
    const r = await fetch(`/api/businesses/${profile.id}/accounts/${acc.id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ label }),
    });
    if (!r.ok) {
      const errs = await readErrors(r);
      return setRowErrors((e) => ({ ...e, [acc.id]: errs.label ?? errs._form ?? "Could not save." }));
    }
    setRowErrors((e) => ({ ...e, [acc.id]: "" }));
    setDrafts((d) => { const { [acc.id]: _, ...rest } = d; return rest; });
    setProfile((await r.json()) as BusinessProfile);
  };

  const add = async (e: FormEvent) => {
    e.preventDefault();
    const r = await fetch(`/api/businesses/${profile.id}/accounts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(newAcc),
    });
    if (!r.ok) return setAddErrors(await readErrors(r));
    setAddErrors({});
    setNewAcc({ label: "", kind: "bank", currency: profile.base_currency });
    setProfile((await r.json()) as BusinessProfile);
  };

  return (
    <section className="card" aria-labelledby="accounts-title">
      <h2 id="accounts-title" className="card-title">Cash accounts</h2>
      <p className="muted small">Labels only. Balances and records arrive with Records import (Prompt 04). Account kind and currency are fixed once created.</p>
      <div className="table-wrap">
        <table className="table">
          <thead><tr><th>Label</th><th>Kind</th><th>Currency</th><th>Reference</th></tr></thead>
          <tbody>
            {profile.accounts.length === 0 && <tr><td colSpan={4} className="muted">No accounts yet.</td></tr>}
            {profile.accounts.map((a) => {
              const draft = drafts[a.id] ?? a.label;
              return (
                <tr key={a.id}>
                  <td>
                    {canEdit ? (
                      <div className="inline-edit">
                        <input aria-label={`Label for ${a.label}`} value={draft} maxLength={60}
                               onChange={(e) => setDrafts((d) => ({ ...d, [a.id]: e.target.value }))} />
                        {draft !== a.label && <button type="button" className="btn btn-primary btn-small" onClick={() => relabel(a)}>Save</button>}
                        {rowErrors[a.id] && <span className="field-error" role="alert">{rowErrors[a.id]}</span>}
                      </div>
                    ) : a.label}
                  </td>
                  <td>{KIND_LABELS[a.kind]}</td>
                  <td>{a.currency}</td>
                  <td className="muted"><code>{a.ref}</code></td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {canEdit && (
        <form className="add-row" onSubmit={add} noValidate aria-label="Add account">
          <Field label="New account label" error={errorFor(addErrors, "label") ?? addErrors._form}>
            <input value={newAcc.label} maxLength={60} onChange={(e) => setNewAcc({ ...newAcc, label: e.target.value })} />
          </Field>
          <Field label="Kind" error={errorFor(addErrors, "kind")}>
            <select className="select" value={newAcc.kind} onChange={(e) => setNewAcc({ ...newAcc, kind: e.target.value as Account["kind"] })}>
              {(options?.account_kinds ?? ["bank", "mobile_wallet", "cash"]).map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
            </select>
          </Field>
          <Field label="Currency" error={errorFor(addErrors, "currency")}>
            <select className="select" value={newAcc.currency} onChange={(e) => setNewAcc({ ...newAcc, currency: e.target.value })}>
              {options?.currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
            </select>
          </Field>
          <button type="submit" className="btn btn-primary">Add account</button>
        </form>
      )}
    </section>
  );
}
