import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useSession, type Account, type BusinessProfile } from "../auth/session";
import { parseMajorToMinor } from "../money";
import { errorFor, readErrors, type FieldErrors } from "./apiErrors";
import { exponentOf, Field, KIND_LABELS, TimezoneInput } from "./fields";

type Row = { label: string; kind: Account["kind"] };

export function CreateBusinessPage() {
  const { options, refreshMe, setActiveId, setProfile } = useSession();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [jurisdiction, setJurisdiction] = useState("BD");
  const [currency, setCurrency] = useState("BDT");
  const [timezone, setTimezone] = useState("Asia/Dhaka");
  const [reserve, setReserve] = useState("");
  const [accounts, setAccounts] = useState<Row[]>([{ label: "", kind: "bank" }]);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [busy, setBusy] = useState(false);

  // Picking a jurisdiction suggests its usual currency and timezone; both stay editable.
  const pickJurisdiction = (code: string) => {
    setJurisdiction(code);
    const j = options?.jurisdictions.find((x) => x.code === code);
    if (j) {
      setCurrency(j.suggested_currency);
      setTimezone(j.suggested_timezone);
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const parsed = parseMajorToMinor(reserve, exponentOf(options, currency), options?.limits.max_amount_minor ?? 1e15);
    if (!parsed.ok) return setErrors({ "reserve.amount_minor": parsed.error });
    const body = {
      name, jurisdiction, base_currency: currency, timezone,
      reserve: { amount_minor: parsed.minor, currency },
      // Blank rows are ignored; every account uses the base currency at creation.
      accounts: accounts.filter((a) => a.label.trim()).map((a) => ({ ...a, currency })),
    };
    setBusy(true);
    const r = await fetch("/api/businesses", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    setBusy(false);
    if (!r.ok) return setErrors(await readErrors(r));
    const created = (await r.json()) as BusinessProfile;
    await refreshMe();
    setActiveId(created.id);
    setProfile(created);
    navigate("/business-settings");
  };

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">New business</h1>
          <p className="page-sub">You become the owner of the business you create.</p>
        </div>
      </div>
      <form className="card form-grid" onSubmit={submit} noValidate>
        {errors._form && <p className="badge badge-danger span-2" role="alert">{errors._form}</p>}
        <Field label="Business name" error={errorFor(errors, "name")}>
          <input value={name} maxLength={options?.limits.name_max ?? 120} onChange={(e) => setName(e.target.value)} required />
        </Field>
        <Field label="Jurisdiction" error={errorFor(errors, "jurisdiction")}>
          <select className="select" value={jurisdiction} onChange={(e) => pickJurisdiction(e.target.value)}>
            {options?.jurisdictions.map((j) => <option key={j.code} value={j.code}>{j.name} ({j.code})</option>)}
          </select>
        </Field>
        <Field label="Base currency" error={errorFor(errors, "base_currency")}>
          <select className="select" value={currency} onChange={(e) => setCurrency(e.target.value)}>
            {options?.currencies.map((c) => <option key={c.code} value={c.code}>{c.code}</option>)}
          </select>
        </Field>
        <Field label="Business timezone (IANA)" error={errorFor(errors, "timezone")}>
          <TimezoneInput id="tz-new" value={timezone} onChange={setTimezone} options={options} />
        </Field>
        <Field label={`Reserve requirement (${currency})`} error={errorFor(errors, "reserve")}
               hint={`For example 80,000.00. Up to ${exponentOf(options, currency)} decimal places.`}>
          <input inputMode="decimal" value={reserve} onChange={(e) => setReserve(e.target.value)} />
        </Field>

        <fieldset className="span-2 accounts-fieldset">
          <legend className="card-title">Cash accounts ({currency})</legend>
          {errorFor(errors, "accounts") && <p className="field-error" role="alert">{errorFor(errors, "accounts")}</p>}
          {accounts.map((row, i) => (
            <div className="add-row" key={i}>
              <Field label={`Account ${i + 1} label`} error={errorFor(errors, `accounts.${i}`)}>
                <input value={row.label} maxLength={60}
                       onChange={(e) => setAccounts(accounts.map((a, j) => (j === i ? { ...a, label: e.target.value } : a)))} />
              </Field>
              <Field label="Kind">
                <select className="select" value={row.kind}
                        onChange={(e) => setAccounts(accounts.map((a, j) => (j === i ? { ...a, kind: e.target.value as Account["kind"] } : a)))}>
                  {Object.entries(KIND_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                </select>
              </Field>
              <button type="button" className="btn btn-ghost" onClick={() => setAccounts(accounts.filter((_, j) => j !== i))}>Remove</button>
            </div>
          ))}
          {accounts.length < (options?.limits.max_accounts ?? 20) && (
            <button type="button" className="btn btn-ghost" onClick={() => setAccounts([...accounts, { label: "", kind: "bank" }])}>Add another account</button>
          )}
        </fieldset>

        <div className="form-actions span-2">
          <button type="button" className="btn btn-ghost" onClick={() => navigate(-1)} disabled={busy}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Creating…" : "Create business"}</button>
        </div>
      </form>
    </>
  );
}
