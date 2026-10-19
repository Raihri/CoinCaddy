import { useState, type FormEvent } from "react";
import { useSession } from "./session";

export function SignInPage() {
  const { signIn } = useSession();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(await signIn(email, password));
    setBusy(false);
  };

  return (
    <div className="signin">
      <form className="card signin-card" onSubmit={submit}>
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">S</span>
          <span>
            <span className="brand-name">SME Finance</span>
            <span className="brand-sub">Copilot</span>
          </span>
        </div>
        <h1 className="page-title">Sign in</h1>
        <p className="muted">Local demo sign-in for synthetic users only.</p>
        <label className="field">
          <span>Email</span>
          <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label className="field">
          <span>Password</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && <p className="badge badge-danger" role="alert">{error}</p>}
        <button type="submit" className="btn btn-primary" disabled={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </button>
        <p className="muted small">
          Demo users are listed in fixtures/demo_users.json; the password is DEMO_USER_PASSWORD in .env.
        </p>
      </form>
    </div>
  );
}
