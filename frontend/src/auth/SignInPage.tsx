import { useState } from "react";
import { useSession } from "./session";

const DEMO_USERS = [
  { email: "amina@northstar.example", name: "Amina Rahman", business: "Northstar Trading", role: "Owner" },
  { email: "bilal@northstar.example", name: "Bilal Hossain", business: "Northstar Trading", role: "Bookkeeper" },
  { email: "chaya@northstar.example", name: "Chaya Das", business: "Northstar Trading", role: "Staff" },
  { email: "tanvir@riverbend.example", name: "Tanvir Ahmed", business: "Riverbend Crafts", role: "Owner" },
];

export function SignInPage() {
  const { quickSignIn } = useSession();
  const [busyEmail, setBusyEmail] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const enter = async (email: string) => {
    setBusyEmail(email);
    setError(await quickSignIn(email));
    setBusyEmail(null);
  };

  return (
    <main className="signin">
      <section className="card signin-card quick-login-card" aria-labelledby="quick-login-title">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span><span className="brand-name">SME Finance</span><span className="brand-sub">Copilot</span></span>
        </div>
        <div>
          <p className="eyebrow">Interactive sandbox</p>
          <h1 id="quick-login-title" className="page-title">Choose a demo profile</h1>
          <p className="muted">Enter the workspace as an owner, bookkeeper, or staff member. All profiles use synthetic demo data.</p>
        </div>
        <div className="quick-user-list" aria-label="Demo profiles">
          {DEMO_USERS.map((user) => (
            <button key={user.email} type="button" className="quick-user" onClick={() => enter(user.email)} disabled={busyEmail !== null}>
              <span className="quick-avatar" aria-hidden="true">{user.name.split(" ").map((part) => part[0]).join("")}</span>
              <span className="quick-user-copy"><strong>{user.name}</strong><span>{user.role} · {user.business}</span></span>
              <span className="quick-user-arrow" aria-hidden="true">→</span>
            </button>
          ))}
        </div>
        {busyEmail && <p className="muted small" role="status">Opening demo profile…</p>}
        {error && <p className="badge badge-danger" role="alert">{error}</p>}
        <p className="muted small quick-login-note">Demo access is limited to synthetic sandbox accounts. Role permissions still apply.</p>
      </section>
    </main>
  );
}
