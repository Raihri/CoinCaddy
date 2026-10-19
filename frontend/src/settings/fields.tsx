import type { ReactNode } from "react";
import type { SettingsOptions } from "../auth/session";

export function Field({ label, error, hint, children }: { label: string; error?: string; hint?: string; children: ReactNode }) {
  return (
    <label className={`field ${error ? "has-error" : ""}`}>
      <span>{label}</span>
      {children}
      {hint && !error && <span className="field-hint">{hint}</span>}
      {error && <span className="field-error" role="alert">{error}</span>}
    </label>
  );
}

export const KIND_LABELS: Record<string, string> = { bank: "Bank", mobile_wallet: "Mobile wallet", cash: "Cash" };

export function exponentOf(options: SettingsOptions | null, currency: string): number {
  return options?.currencies.find((c) => c.code === currency)?.minor_unit_exponent ?? 2;
}

export function TimezoneInput({ value, onChange, options, id }: {
  value: string; onChange: (v: string) => void; options: SettingsOptions | null; id: string;
}) {
  return (
    <>
      <input list={`${id}-list`} value={value} onChange={(e) => onChange(e.target.value)} autoComplete="off" spellCheck={false} />
      <datalist id={`${id}-list`}>
        {options?.timezones.map((tz) => <option key={tz} value={tz} />)}
      </datalist>
    </>
  );
}
