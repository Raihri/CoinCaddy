import { useCallback, useEffect, useState } from "react";
import { ROLE_LABELS, useSession, type Membership } from "./session";

type Member = { membership_id: string; display_name: string; email: string; role: Membership["role"] };

export function UsersRolesPage() {
  const { me, active } = useSession();
  const [members, setMembers] = useState<Member[] | null>(null);
  const [message, setMessage] = useState<{ kind: "success" | "danger"; text: string } | null>(null);

  const canManage = active?.allowed_actions.includes("membership.manage") ?? false;
  const base = active ? `/api/businesses/${active.business_id}` : null;

  const load = useCallback(() => {
    if (!base) return;
    fetch(`${base}/memberships`)
      .then((r) => (r.ok ? (r.json() as Promise<Member[]>) : Promise.reject(r.status)))
      .then(setMembers)
      .catch(() => setMessage({ kind: "danger", text: "Could not load members." }));
  }, [base]);
  useEffect(load, [load]);

  const changeRole = async (m: Member, role: Member["role"]) => {
    setMessage(null);
    const r = await fetch(`${base}/memberships/${m.membership_id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role }),
    });
    if (r.ok) setMessage({ kind: "success", text: `${m.display_name} is now ${ROLE_LABELS[role]}.` });
    else {
      const detail = ((await r.json().catch(() => ({}))) as { detail?: string }).detail;
      setMessage({ kind: "danger", text: detail ?? `Role change refused (HTTP ${r.status}).` });
    }
    load();
  };

  if (!active) return <section className="card">You are not a member of any business.</section>;

  return (
    <>
      <div className="page-header">
        <div>
          <h1 className="page-title">Users &amp; roles</h1>
          <p className="page-sub">
            {active.business_name} · you are {ROLE_LABELS[active.role]}
          </p>
        </div>
      </div>

      <section className="card" aria-labelledby="members-title">
        <h2 id="members-title" className="card-title">Members</h2>
        {!canManage && (
          <p className="badge badge-info">Only an owner can change roles. The server enforces this as well.</p>
        )}
        {message && (
          <p className={`badge badge-${message.kind}`} role="status">{message.text}</p>
        )}
        {members && (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr><th>Name</th><th>Email</th><th>Role</th></tr>
              </thead>
              <tbody>
                {members.map((m) => (
                  <tr key={m.membership_id}>
                    <td>
                      {m.display_name}
                      {m.email === me?.user.email && <span className="muted"> (you)</span>}
                    </td>
                    <td className="muted">{m.email}</td>
                    <td>
                      {canManage ? (
                        <select
                          className="select"
                          aria-label={`Role for ${m.display_name}`}
                          value={m.role}
                          onChange={(e) => changeRole(m, e.target.value as Member["role"])}
                        >
                          {(Object.keys(ROLE_LABELS) as Member["role"][]).map((r) => (
                            <option key={r} value={r}>{ROLE_LABELS[r]}</option>
                          ))}
                        </select>
                      ) : (
                        <span className="badge badge-info">{ROLE_LABELS[m.role]}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="perm-title">
        <h2 id="perm-title" className="card-title">What each role can do so far</h2>
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr><th>Action</th><th>Owner</th><th>Bookkeeper</th><th>Staff</th></tr>
            </thead>
            <tbody>
              <tr><td>View business profile and members</td><td>Yes</td><td>Yes</td><td>Yes</td></tr>
              <tr><td>Download business export</td><td>Yes</td><td>Yes</td><td>No</td></tr>
              <tr><td>View audit history</td><td>Yes</td><td>Yes</td><td>No</td></tr>
              <tr><td>Change member roles (owner setting)</td><td>Yes</td><td>No</td><td>No</td></tr>
              <tr><td>Edit business settings and accounts (owner setting)</td><td>Yes</td><td>No</td><td>No</td></tr>
              <tr><td>Anything not listed</td><td>No</td><td>No</td><td>No</td></tr>
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
