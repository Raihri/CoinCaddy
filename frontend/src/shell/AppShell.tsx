import { Fragment, useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ROLE_LABELS, useSession } from "../auth/session";
import { useRegion } from "../region/RegionContext";
import { DESTINATIONS } from "./navigation";
import { NavIcon } from "./NavIcon";

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("") || "?";
}

export function AppShell() {
  const { me, active, profile, setActiveId, signOut } = useSession();
  const region = useRegion();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [fraudUnresolved, setFraudUnresolved] = useState(0);
  const location = useLocation();
  const canReadFraud = active?.allowed_actions.includes("fraud.read") ?? false;

  // Close the mobile drawer after navigating.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

  useEffect(() => {
    const businessId = active?.business_id;
    if (!businessId || !canReadFraud) { setFraudUnresolved(0); return; }
    let current = true;
    const check = async () => {
      try {
        const response = await fetch(`/api/businesses/${businessId}/fraud/overview`);
        if (!response.ok) return;
        const overview = await response.json() as { unresolved?: number };
        if (current) setFraudUnresolved(Math.max(0, overview.unresolved ?? 0));
      } catch { /* Keep the shell quiet when the API is unavailable. */ }
    };
    void check();
    const timer = window.setInterval(check, 45_000);
    return () => { current = false; window.clearInterval(timer); };
  }, [active?.business_id, canReadFraud]);

  return (
    <div className="shell">
      <aside className={`sidebar ${drawerOpen ? "is-open" : ""}`} aria-label="Primary">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span>
            <span className="brand-name">SME Finance</span>
            <span className="brand-sub">Copilot</span>
          </span>
        </div>

        <p className="nav-heading">Workspace</p>
        <nav>
          <ul className="nav-list">
            <li>
              <NavLink to="/" end className="nav-item">
                <NavIcon name="status" />
                <span className="nav-label">System status</span>
              </NavLink>
            </li>
            {DESTINATIONS.map((d) => (
              <li key={d.path}>
                <NavLink
                  to={d.path}
                  className={`nav-item ${d.available ? "" : "is-unavailable"}`}
                  aria-description={d.available ? undefined : "Unavailable: not built yet"}
                >
                  <NavIcon name={d.icon} />
                  <span className="nav-label">{d.label}</span>
                  {d.path === "/fraud-monitor" && fraudUnresolved > 0 && (
                    <span className="nav-alert-count" aria-label={`${fraudUnresolved} flags need review`}>
                      <span className="nav-alert-pulse" aria-hidden="true" />{fraudUnresolved}
                    </span>
                  )}
                  {!d.available && <span className="nav-soon">Not built</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="sidebar-footer">
          <NavLink to="/business-settings" className="nav-item">
            <NavIcon name="settings" />
            <span className="nav-label">Business settings</span>
          </NavLink>
          <NavLink to="/users-roles" className="nav-item">
            <NavIcon name="users" />
            <span className="nav-label">Users &amp; roles</span>
          </NavLink>
          <div className="profile">
            <span className="avatar" aria-hidden="true">{initials(me?.user.display_name ?? "")}</span>
            <span className="profile-text">
              <span className="profile-name">{me?.user.display_name}</span>
              <span className="profile-sub">
                {active ? `${ROLE_LABELS[active.role]} · ${active.business_name}` : "No business membership"}
              </span>
            </span>
          </div>
          <button type="button" className="btn btn-ghost" onClick={signOut}>Sign out</button>
        </div>
      </aside>

      {drawerOpen && <div className="scrim" onClick={() => setDrawerOpen(false)} aria-hidden="true" />}

      <div className="workspace">
        <header className="topbar">
          <button
            type="button"
            className="menu-button"
            aria-label={drawerOpen ? "Close navigation" : "Open navigation"}
            aria-expanded={drawerOpen}
            onClick={() => setDrawerOpen((o) => !o)}
          >
            <span /><span /><span />
          </button>
          {me && me.memberships.length > 1 ? (
            <select
              className="select"
              aria-label="Business"
              value={active?.business_id}
              onChange={(e) => setActiveId(e.target.value)}
            >
              {me.memberships.map((m) => (
                <option key={m.business_id} value={m.business_id}>{m.business_name}</option>
              ))}
            </select>
          ) : (
            <span className="topbar-title">{active?.business_name ?? "No business"}</span>
          )}
          <span className="topbar-pills">
            {profile && (
              <span className="pill pill-primary">{profile.base_currency} · {profile.jurisdiction_name}</span>
            )}
            {region.view?.active && (
              <span className="pill pill-muted" title={`Region pack ${region.view.active.version}`}>
                Region pack · {region.view.active.jurisdiction} · {region.view.active.version}
              </span>
            )}
            {active && <span className="pill pill-info">{ROLE_LABELS[active.role]}</span>}
            <span className="pill pill-warning">SANDBOX</span>
          </span>
        </header>
        <main className="content">
          {region.ready ? (
            // A new pack remounts the page, so every figure is written again with the pack that is shown in the header.
            <Fragment key={region.formatKey}><Outlet /></Fragment>
          ) : (
            <p className="muted">Loading…</p>
          )}
        </main>
      </div>
      <NavLink to="/finance-chat" className="chat-fab" aria-label="Open Finance Chat">
        <NavIcon name="chat" />
        <span>Finance Chat</span>
        <span className="chat-fab-hint">Ask</span>
      </NavLink>
    </div>
  );
}
