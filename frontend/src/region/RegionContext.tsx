import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api } from "../api";
import { useSession } from "../auth/session";
import { KIND_LABELS } from "../settings/fields";
import { setRegionFormat, type RegionFormat } from "./format";

export type PackDetail = RegionFormat & {
  name: string;
  jurisdiction: string;
  jurisdiction_name: string;
  language: string;
  minor_unit_exponent: number;
  notice: string;
  import_templates: { id: string; name: string; description: string; date_format: string; amount_mode: string; columns: Record<string, string> }[];
  catalogue: { version: string; label: string; updated: string; source: string; products: { id: string; name: string; kind: string; provider: string; jurisdictions: string[]; currencies: string[] }[] };
  no_conversion_note: string;
  history_note: string;
};

export type RegionView = {
  business_id: string;
  source: "selected" | "default" | "none";
  active: PackDetail | null;
  selected_version: string | null;
  outdated: boolean;
  business: { jurisdiction: string; base_currency: string };
  jurisdiction_matches: boolean;
  currency_matches: boolean;
  warnings: string[];
  available: PackDetail[];
  history: { pack_id: string; pack_version: string; previous_pack_id: string | null; selected_at: string; selected_by: string }[];
  can_select: boolean;
  no_conversion_note: string;
  history_note: string;
};

type RegionState = { view: RegionView | null; ready: boolean; formatKey: string; reload: () => Promise<void>; setView: (v: RegionView) => void };
const RegionContext = createContext<RegionState | null>(null);

const BUILT_IN_MOBILE_WALLET = "Mobile wallet";

// Applies the pack to the module-level formatters (read by formatMoney / fmtDate / account-kind labels).
function applyRegion(pack: PackDetail | null): void {
  setRegionFormat(pack);
  KIND_LABELS.mobile_wallet = pack?.labels.mobile_wallet ?? BUILT_IN_MOBILE_WALLET;
}

export function RegionProvider({ children }: { children: ReactNode }) {
  const { active } = useSession();
  const businessId = active?.business_id ?? null;
  const [view, setViewState] = useState<RegionView | null>(null);
  const [ready, setReady] = useState(false);

  const load = useCallback(async () => {
    if (!businessId) return;
    const r = await api<RegionView>(`/api/businesses/${businessId}/region-pack`);
    setViewState(r.ok ? r.data : null);   // on failure the built-in display is kept; the server still enforces everything
    setReady(true);
  }, [businessId]);

  useEffect(() => {
    applyRegion(null);
    setViewState(null);
    setReady(!businessId);
    load();
  }, [load, businessId]);

  // Applied while rendering, so every page below formats with the pack that is shown in the header.
  applyRegion(view?.active ?? null);
  const formatKey = `${businessId ?? "none"}:${view?.active ? `${view.active.id}:${view.active.version}` : "built-in"}`;

  return (
    <RegionContext.Provider value={{ view, ready, formatKey, reload: load, setView: setViewState }}>
      {children}
    </RegionContext.Provider>
  );
}

export function useRegion(): RegionState {
  const ctx = useContext(RegionContext);
  if (!ctx) throw new Error("useRegion must be used inside RegionProvider");
  return ctx;
}
