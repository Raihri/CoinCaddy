import { Link } from "react-router-dom";
import { useRegion } from "./RegionContext";

/** One line showing which Region Pack (version and jurisdiction) shapes the figures and samples on the current screen. */
export function RegionNote({ catalogue = false }: { catalogue?: boolean }) {
  const { view } = useRegion();
  if (!view) return null;
  const pack = view.active;
  return (
    <p className="region-note" data-testid="region-note">
      {pack ? (
        <>
          <span className="badge badge-info">Region pack</span>
          <strong>{pack.name} ({pack.jurisdiction})</strong>
          <span className="muted"> · {pack.version} · {pack.currency}{catalogue ? ` · ${pack.catalogue.version}` : ""}</span>
        </>
      ) : (
        <>
          <span className="badge badge-muted">No region pack</span>
          <span className="muted">Built-in display for {view.business.jurisdiction}.</span>
        </>
      )}
      <span className="muted"> Presentation and sample terms only; not a claim of legal compliance. </span>
      <Link className="link" to="/business-settings#region-pack">Details</Link>
    </p>
  );
}
