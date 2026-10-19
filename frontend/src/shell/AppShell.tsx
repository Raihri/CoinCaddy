import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ROLE_LABELS, useSession } from "../auth/session";
import { DESTINATIONS } from "./navigation";

function initials(name: string): string {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0].toUpperCase()).join("") || "?";
}

export function AppShell() {
  const { me, active, profile, setActiveId, signOut } = useSession();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const location = useLocation();

  // Close the mobile drawer after navigating.
  useEffect(() => setDrawerOpen(false), [location.pathname]);

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
                <span className="nav-mono" aria-hidden="true">SY</span>
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
                  <span className="nav-mono" aria-hidden="true">{d.monogram}</span>
                  <span className="nav-label">{d.label}</span>
                  {!d.available && <span className="nav-soon">Not built</span>}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        <div className="sidebar-footer">
          <NavLink to="/business-settings" className="nav-item">
            <span className="nav-mono" aria-hidden="true">BS</span>
            <span className="nav-label">Business settings</span>
          </NavLink>
          <NavLink to="/users-roles" className="nav-item">
            <span className="nav-mono" aria-hidden="true">UR</span>
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
            {active && <span className="pill pill-info">{ROLE_LABELS[active.role]}</span>}
            <span className="pill pill-warning">SANDBOX</span>
          </span>
        </header>
        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
