import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";

export type Membership = {
  business_id: string;
  business_name: string;
  role: "owner" | "bookkeeper" | "staff";
  allowed_actions: string[];
};
export type Me = {
  user: { id: string; email: string; display_name: string };
  memberships: Membership[];
};

export type Account = { id: string; ref: string; label: string; kind: "bank" | "mobile_wallet" | "cash"; currency: string };
export type BusinessProfile = {
  id: string;
  name: string;
  jurisdiction: string;
  jurisdiction_name: string;
  base_currency: string;
  timezone: string;
  reserve: { amount_minor: number; currency: string };
  is_synthetic: boolean;
  version: number;
  updated_at: string;
  accounts: Account[];
  your_role: Membership["role"];
};
export type SettingsOptions = {
  jurisdictions: { code: string; name: string; suggested_currency: string; suggested_timezone: string }[];
  currencies: { code: string; minor_unit_exponent: number }[];
  timezones: string[];
  account_kinds: Account["kind"][];
  limits: { max_amount_minor: number; max_accounts: number; name_max: number; label_max: number };
};

type SessionState = {
  me: Me | null;
  loading: boolean;
  active: Membership | null;
  setActiveId: (businessId: string) => void;
  profile: BusinessProfile | null;
  setProfile: (p: BusinessProfile) => void;
  options: SettingsOptions | null;
  refreshMe: () => Promise<void>;
  signIn: (email: string, password: string) => Promise<string | null>;
  signOut: () => Promise<void>;
};

const SessionContext = createContext<SessionState | null>(null);

export function SessionProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);
  const [activeId, setActiveId] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/auth/me")
      .then((r) => (r.ok ? (r.json() as Promise<Me>) : null))
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setLoading(false));
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!r.ok) return r.status === 401 ? "Email or password is incorrect." : "Sign-in failed. Is the API running?";
    setMe((await r.json()) as Me);
    return null;
  }, []);

  const signOut = useCallback(async () => {
    await fetch("/api/auth/logout", { method: "POST" });
    setMe(null);
    setActiveId(null);
  }, []);

  const refreshMe = useCallback(async () => {
    const r = await fetch("/api/auth/me");
    setMe(r.ok ? ((await r.json()) as Me) : null);
  }, []);

  // The selected business is only a UI choice; the API re-checks membership on every request.
  const active = me?.memberships.find((m) => m.business_id === activeId) ?? me?.memberships[0] ?? null;

  const [profile, setProfile] = useState<BusinessProfile | null>(null);
  const [options, setOptions] = useState<SettingsOptions | null>(null);
  useEffect(() => {
    setProfile(null);
    if (!active) return;
    fetch(`/api/businesses/${active.business_id}`)
      .then((r) => (r.ok ? (r.json() as Promise<BusinessProfile>) : null))
      .then(setProfile)
      .catch(() => setProfile(null));
  }, [active?.business_id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!me) return;
    fetch("/api/reference/settings-options")
      .then((r) => (r.ok ? (r.json() as Promise<SettingsOptions>) : null))
      .then(setOptions)
      .catch(() => setOptions(null));
  }, [me?.user.id]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <SessionContext.Provider
      value={{ me, loading, active, setActiveId, profile, setProfile, options, refreshMe, signIn, signOut }}
    >
      {children}
    </SessionContext.Provider>
  );
}

export function useSession(): SessionState {
  const ctx = useContext(SessionContext);
  if (!ctx) throw new Error("useSession must be used inside SessionProvider");
  return ctx;
}

export const ROLE_LABELS: Record<Membership["role"], string> = {
  owner: "Owner",
  bookkeeper: "Bookkeeper",
  staff: "Staff",
};
