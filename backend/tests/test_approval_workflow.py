"""Approved Action Workflow: immutable proposals, tier gates, release recheck, and the database guards.

Covers the four required cases (staff denial, owner approval, expired approval, tampered payload) and the T3 gates
(re-authentication, documented verification, second reviewer).
"""

import re
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.contracts.business_time import utc_now
from app.db import get_engine
from app.modules.approvals import router as approvals_router
from app.modules.records.service import business_today
from tests.helpers import FIXTURE_ACCOUNTS, PASSWORD, Biz
from tests.test_financing_readiness import _setup as readiness_setup

HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _setup(biz=None):
    biz = biz or Biz()
    biz.today = business_today("Asia/Dhaka")
    biz.url = f"{biz.base}/approvals"
    day = lambda n: (biz.today + timedelta(days=n)).isoformat()
    biz.bank = biz.account["ACC-BANK"]

    def make(direction, num, party, due, amount, category):
        r = biz.owner.post(f"{biz.base}/obligations", json={"direction": direction, "party_name": party, "document_number": num, "issue_date": day(-40),
                                                            "due_date": day(due), "amount_minor": amount, "currency": "BDT", "category": category,
                                                            "source_reference": "book", "settlement_account_id": biz.bank})
        assert r.status_code == 201, r.text
        return r.json()["id"]
    biz.inv = make("receivable", "INV-108", "Eastline Stores", -12, 4_500_000, "sales")
    biz.inv_future = make("receivable", "INV-200", "Later Ltd", 10, 1_000_000, "sales")
    biz.bill = make("payable", "DS-441", "Delta Supplies", 3, 6_000_000, "inventory_suppliers")
    # One open reminder per invoice is allowed, so tests that need several use fresh overdue invoices.
    n = iter(range(300, 400))
    biz.new_invoice = lambda: make("receivable", f"INV-{next(n)}", "Eastline Stores", -12, 4_500_000, "sales")
    biz.new_bill = lambda: make("payable", f"DS-{next(n)}", "Delta Supplies", 3, 6_000_000, "inventory_suppliers")
    return biz


def _reminder(biz, client=None, **kw):
    body = {"kind": "invoice_reminder", "obligation_id": biz.inv, "channel": "email",
            "message": "Dear Eastline Stores, invoice INV-108 is overdue. Please arrange payment."} | kw
    return (client or biz.owner).post(biz.url, json=body)


def _payment(biz, client=None, **kw):
    body = {"kind": "supplier_payment", "obligation_id": biz.bill, "amount_minor": 6_000_000, "from_account_id": biz.bank} | kw
    return (client or biz.owner).post(biz.url, json=body)


VERIFY = {"method": "called_known_contact", "reference": "Called Delta Supplies on the number in our supplier file"}


def _approve(biz, client, p, **kw):
    body = {"decision": "approve", "payload_sha256": p["payload_sha256"]} | kw
    return client.post(f"{biz.url}/{p['id']}/decision", json=body)


def _release(biz, client, p, h=None):
    return client.post(f"{biz.url}/{p['id']}/release", json={"payload_sha256": h or p["payload_sha256"]})


def _events(biz):
    return biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]


# --- required case 1: staff denial ---------------------------------------------------------------------------


def test_staff_are_denied_and_bookkeepers_can_only_propose():
    biz = _setup()
    staff, keeper = biz.member("staff"), biz.member("bookkeeper")
    assert staff.get(biz.url).status_code == 403 and staff.get(f"{biz.url}/policy").status_code == 403
    assert _reminder(biz, staff).status_code == 403
    p = _reminder(biz, keeper)
    assert p.status_code == 201, p.text
    p = p.json()
    assert staff.get(f"{biz.url}/{p['id']}").status_code == 403
    assert _approve(biz, staff, p).status_code == 403 and _release(biz, staff, p).status_code == 403
    assert _approve(biz, keeper, p).status_code == 403                     # a bookkeeper proposes; only the owner approves
    assert keeper.post(f"{biz.url}/{p['id']}/decision", json={"decision": "reject", "payload_sha256": p["payload_sha256"], "reason": "No"}).status_code == 403
    assert _release(biz, keeper, p).status_code == 403
    assert keeper.get(f"{biz.url}/{p['id']}").json()["state"] == "pending"
    denied = {e["action"] for e in _events(biz) if e["outcome"] == "denied"}
    assert {"approvals.read", "approvals.propose", "approvals.decide", "approvals.release"} <= denied


# --- required case 2: owner approval -------------------------------------------------------------------------


def test_t2_proposal_is_exact_and_needs_explicit_owner_approval_then_release():
    biz = _setup()
    keeper = biz.member("bookkeeper")
    r = _reminder(biz, keeper)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["tier"] == "T2" and p["required_role"] == "owner" and p["state"] == "pending" and p["second_reviewer_required"] is False
    assert HEX64.match(p["payload_sha256"]) and p["integrity"]["payload_hash_matches"] and p["policy_version"] == "approval-policy-v1"
    # The payload comes from the records, not from the client: customer, document number, and the outstanding amount.
    assert p["payload"]["customer"] == "Eastline Stores" and p["payload"]["document_number"] == "INV-108"
    assert p["payload"]["outstanding_minor"] == 4_500_000 and p["payload"]["currency"] == "BDT" and p["payload"]["delivery"] == "sandbox"
    created, expires = (utc_now().fromisoformat(p[k]) for k in ("created_at", "expires_at"))
    assert expires - created == timedelta(hours=48)
    assert p["can"]["approve"] is False and p["can"]["withdraw"] is True        # for the bookkeeper (proposer)

    listed = biz.owner.get(biz.url).json()
    assert listed["counts"]["pending"] == 1 and listed["items"][0]["can"]["approve"] is True
    a = _approve(biz, biz.owner, p)
    assert a.status_code == 200, a.text
    a = a.json()
    assert a["state"] == "approved" and a["decisions"][0]["event"] == "approved" and a["decisions"][0]["reauthenticated"] is False
    assert a["decisions"][0]["payload_sha256"] == p["payload_sha256"] and a["can"]["release"] is True
    assert _approve(biz, biz.owner, p).status_code == 409                       # a proposal is decided once
    rel = _release(biz, biz.owner, p)
    assert rel.status_code == 200, rel.text
    rel = rel.json()
    assert rel["state"] == "executable" and rel["executed"] is False
    checks = rel["decisions"][-1]["checks"]
    assert {c["code"] for c in checks} == {"releaser_role", "approved", "approver_role", "second_reviewer", "not_expired", "payload_integrity", "records_current"}
    assert all(c["ok"] for c in checks)
    assert _release(biz, biz.owner, p).status_code == 409                       # released once
    ev = [e for e in _events(biz) if e["action"].startswith("approvals.") and e["outcome"] == "success"]
    assert [e["action"] for e in reversed(ev)] == ["approvals.propose", "approvals.decide", "approvals.release"]
    assert "message" not in str(ev[-1]["details"])                              # the reminder text is not copied into the audit trail


def test_owner_may_approve_own_t2_and_reject_or_withdraw_with_a_reason():
    biz = _setup()
    own = _reminder(biz).json()
    assert own["can"]["approve"] is True and _approve(biz, biz.owner, own).status_code == 200
    second = _reminder(biz, obligation_id=biz.new_invoice()).json()
    reject = lambda **kw: biz.owner.post(f"{biz.url}/{second['id']}/decision", json={"decision": "reject", "payload_sha256": second["payload_sha256"]} | kw)
    assert reject().status_code == 422 and reject(reason="x").status_code == 422
    done = reject(reason="Customer already called us")
    assert done.status_code == 200 and done.json()["state"] == "rejected" and done.json()["decisions"][0]["reason"] == "Customer already called us"
    assert _release(biz, biz.owner, second).status_code == 409
    keeper = biz.member("bookkeeper")
    third = _reminder(biz, keeper, obligation_id=biz.new_invoice()).json()
    w = keeper.post(f"{biz.url}/{third['id']}/withdraw", json={"payload_sha256": third["payload_sha256"], "reason": "Duplicate reminder"})
    assert w.status_code == 200 and w.json()["state"] == "withdrawn"
    assert _approve(biz, biz.owner, third).status_code == 409
    other_keeper = biz.member("bookkeeper")
    fourth = _reminder(biz, keeper, obligation_id=biz.new_invoice()).json()
    assert other_keeper.post(f"{biz.url}/{fourth['id']}/withdraw", json={"payload_sha256": fourth["payload_sha256"], "reason": "Not mine"}).status_code == 409
    assert _approve(biz, biz.owner, fourth, password=PASSWORD).status_code == 422   # re-authentication is only for T3


# --- T3 gates ------------------------------------------------------------------------------------------------


def test_t3_needs_reauth_verification_and_a_second_reviewer():
    biz = _setup()
    r = _payment(biz, amount_minor=5_000_000)
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["tier"] == "T3" and p["second_reviewer_required"] is True and len(p["gates"]) == 4
    assert p["payload"]["supplier"] == "Delta Supplies" and p["payload"]["amount_minor"] == 5_000_000 and p["payload"]["from_account"]
    assert utc_now().fromisoformat(p["expires_at"]) - utc_now().fromisoformat(p["created_at"]) == timedelta(hours=24)
    assert p["can"]["approve"] is False                                          # the owner proposed it
    v = VERIFY | {"checked_on": biz.today.isoformat()}
    self_ok = _approve(biz, biz.owner, p, password=PASSWORD, verification=v)
    assert self_ok.status_code == 409 and "second reviewer" in self_ok.json()["detail"]

    owner2 = biz.member("owner")
    assert _approve(biz, owner2, p, password=PASSWORD).status_code == 422       # no documented verification
    assert _approve(biz, owner2, p, verification=v).status_code == 403          # no re-authentication
    wrong = _approve(biz, owner2, p, password="not-the-password", verification=v)
    assert wrong.status_code == 403 and "Re-authentication failed" in wrong.json()["detail"]
    assert _approve(biz, owner2, p, password=PASSWORD, verification=VERIFY | {"checked_on": (biz.today + timedelta(days=1)).isoformat()}).status_code == 422
    before = _approve(biz, owner2, p, password=PASSWORD, verification=VERIFY | {"checked_on": (biz.today - timedelta(days=1)).isoformat()})
    assert before.status_code == 422 and "between the proposal date" in before.json()["detail"]   # verified before it was proposed
    assert _approve(biz, owner2, p, password=PASSWORD, verification=VERIFY | {"checked_on": biz.today.isoformat(), "reference": "Acct 1234 5678 9012"}).status_code == 422
    assert biz.owner.get(f"{biz.url}/{p['id']}").json()["state"] == "pending"
    ok = _approve(biz, owner2, p, password=PASSWORD, verification=v)
    assert ok.status_code == 200, ok.text
    d = ok.json()["decisions"][0]
    assert d["reauthenticated"] and d["verification"]["method"] == "called_known_contact" and d["actor_role"] == "owner"
    rel = _release(biz, biz.owner, p)                                           # any current owner may release after the checks
    assert rel.status_code == 200, rel.text
    assert "t3_gates" in {c["code"] for c in rel.json()["decisions"][-1]["checks"]}
    events = _events(biz)
    assert any(e["action"] == "approvals.reauth" and e["outcome"] == "failed" and e["details"]["reason"] == "wrong password" for e in events)
    assert not any("not-the-password" in str(e["details"]) or PASSWORD in str(e["details"]) for e in events)

    # A bookkeeper's T3 proposal can be approved by the (only) owner.
    keeper = biz.member("bookkeeper")
    from_keeper = _payment(biz, keeper, amount_minor=1_000_000).json()
    assert _approve(biz, biz.owner, from_keeper, password=PASSWORD, verification=v).status_code == 200


def test_reauth_is_locked_after_repeated_wrong_passwords():
    biz = _setup()
    keeper = biz.member("bookkeeper")
    p = _payment(biz, keeper).json()
    v = VERIFY | {"checked_on": biz.today.isoformat()}
    for _ in range(5):
        assert _approve(biz, biz.owner, p, password="wrong", verification=v).status_code == 403
    assert _approve(biz, biz.owner, p, password=PASSWORD, verification=v).status_code == 429
    assert biz.owner.get(f"{biz.url}/{p['id']}").json()["state"] == "pending"


# --- required case 3: expired approval -----------------------------------------------------------------------


def test_expired_proposals_and_expired_approvals_are_refused(monkeypatch):
    biz = _setup()
    late = lambda hours: monkeypatch.setattr(approvals_router, "utc_now", lambda: utc_now() + timedelta(hours=hours))
    pending = _reminder(biz, expires_in_hours=2).json()
    approved = _reminder(biz, expires_in_hours=2, obligation_id=biz.new_invoice()).json()
    assert _approve(biz, biz.owner, approved).status_code == 200
    late(3)
    r = _approve(biz, biz.owner, pending)
    assert r.status_code == 409 and "expired" in r.json()["detail"]
    got = biz.owner.get(f"{biz.url}/{approved['id']}").json()
    assert got["state"] == "expired" and got["expired_after_approval"] is True and got["can"]["release"] is False
    rel = _release(biz, biz.owner, approved)
    assert rel.status_code == 409
    failed = {c["code"] for c in rel.json()["detail"]["checks"] if not c["ok"]}
    assert failed == {"not_expired"}
    assert biz.owner.get(f"{biz.url}?state=expired").json()["counts"]["expired"] == 2
    assert any(e["action"] == "approvals.release" and e["outcome"] == "conflict" and e["details"]["failed_checks"] == ["not_expired"] for e in _events(biz))
    monkeypatch.undo()
    # The database refuses a decision after expiry even if the API check were skipped.
    with get_engine().connect() as conn, pytest.raises(Exception, match="expired"):
        conn.execute(text("""INSERT INTO action_decision (business_id, proposal_id, event, actor_id, actor_role, payload_sha256, created_at)
                             SELECT business_id, id, 'approved', (SELECT user_id FROM membership WHERE business_id = :b AND role = 'owner' LIMIT 1),
                                    'owner', payload_sha256, expires_at + interval '1 second' FROM action_proposal WHERE id = :p"""),
                     {"b": biz.id, "p": pending["id"]})


def test_expiry_limits_per_tier():
    biz = _setup()
    assert _payment(biz, expires_in_hours=25).status_code == 422               # T3 at most 24 hours
    assert _reminder(biz, expires_in_hours=73).status_code == 422 and _reminder(biz, expires_in_hours=0).status_code == 422
    assert _reminder(biz, expires_in_hours=72).status_code == 201


# --- required case 4: tampered payload -----------------------------------------------------------------------


def _tamper(proposal_id, sql="UPDATE action_proposal SET payload = jsonb_set(payload, '{amount_minor}', '9900000') WHERE id = :p"):
    with get_engine().begin() as conn:                                          # simulates a direct database edit
        conn.execute(text("ALTER TABLE action_proposal DISABLE TRIGGER action_proposal_append_only"))
        conn.execute(text(sql), {"p": proposal_id})
        conn.execute(text("ALTER TABLE action_proposal ENABLE TRIGGER action_proposal_append_only"))


def test_tampered_payload_is_rejected_before_approval_and_before_release():
    biz = _setup()
    owner2 = biz.member("owner")
    v = VERIFY | {"checked_on": biz.today.isoformat()}
    # After approval: the release recheck recomputes the hash and refuses.
    p = _payment(biz).json()
    assert _approve(biz, owner2, p, password=PASSWORD, verification=v).status_code == 200
    _tamper(p["id"])
    got = biz.owner.get(f"{biz.url}/{p['id']}").json()
    assert got["payload"]["amount_minor"] == 9_900_000 and got["integrity"]["payload_hash_matches"] is False
    rel = _release(biz, biz.owner, p)
    assert rel.status_code == 409
    assert "payload_integrity" in {c["code"] for c in rel.json()["detail"]["checks"] if not c["ok"]}
    assert biz.owner.get(f"{biz.url}/{p['id']}").json()["state"] == "approved"   # still not executable

    # Before approval: the stored payload fails its integrity check.
    q = _payment(biz, obligation_id=biz.new_bill()).json()
    _tamper(q["id"])
    r = _approve(biz, owner2, q, password=PASSWORD, verification=v)
    assert r.status_code == 409 and "integrity" in r.json()["detail"]

    # Swapping the expiry or the tier is also detected: the hash covers the whole envelope.
    s = _reminder(biz, obligation_id=biz.new_invoice()).json()
    _tamper(s["id"], "UPDATE action_proposal SET expires_at = expires_at + interval '20 hours' WHERE id = :p")
    assert _approve(biz, biz.owner, s).status_code == 409

    # The approver must have reviewed the stored version: a different hash is refused.
    t = _reminder(biz, obligation_id=biz.new_invoice()).json()
    assert _approve(biz, biz.owner, t, payload_sha256="0" * 64).status_code == 409
    assert _approve(biz, biz.owner, t, payload_sha256="not-a-hash").status_code == 422
    assert _approve(biz, biz.owner, t).status_code == 200
    assert _release(biz, biz.owner, t, h="f" * 64).status_code == 409
    reasons = [e["details"].get("reason") for e in _events(biz) if e["action"] == "approvals.decide" and e["outcome"] == "conflict"]
    assert "payload tampered" in reasons and "hash mismatch" in reasons


def test_release_rechecks_records_and_the_approvers_role():
    biz = _setup()
    keeper = biz.member("bookkeeper")
    p = _reminder(biz, keeper).json()
    assert _approve(biz, biz.owner, p).status_code == 200
    r = biz.owner.post(f"{biz.base}/obligations/{biz.inv}/allocations", json={"amount_minor": 500_000, "currency": "BDT", "allocation_date": biz.today.isoformat()})
    assert r.status_code == 201, r.text
    rel = _release(biz, biz.owner, p)
    assert rel.status_code == 409
    bad = [c for c in rel.json()["detail"]["checks"] if not c["ok"]]
    assert [c["code"] for c in bad] == ["records_current"] and "outstanding_minor" in bad[0]["detail"]

    # The approving owner is demoted after approving: the release recheck notices.
    owner2 = biz.member("owner")
    q = _reminder(biz, keeper, obligation_id=biz.new_invoice()).json()
    assert _approve(biz, owner2, q).status_code == 200
    name = owner2.get("/api/auth/me").json()["user"]["display_name"]
    mid = next(m["membership_id"] for m in biz.owner.get(f"{biz.base}/memberships").json() if m["display_name"] == name)
    assert biz.owner.patch(f"{biz.base}/memberships/{mid}", json={"role": "bookkeeper"}).status_code == 200
    bad = {c["code"] for c in _release(biz, biz.owner, q).json()["detail"]["checks"] if not c["ok"]}
    assert bad == {"approver_role"}

    # A disputed bill can no longer be released for payment.
    owner3 = biz.member("owner")
    pay = _payment(biz, keeper, amount_minor=1_000_000).json()
    assert _approve(biz, owner3, pay, password=PASSWORD, verification=VERIFY | {"checked_on": biz.today.isoformat()}).status_code == 200
    assert biz.owner.post(f"{biz.base}/obligations/{biz.bill}/dispute", json={"disputed": True, "reason": "Short delivery"}).status_code == 200
    rel = _release(biz, biz.owner, pay).json()["detail"]["checks"]
    assert [c["code"] for c in rel if not c["ok"]] == ["records_current"] and "disputed" in next(c["detail"] for c in rel if not c["ok"])


# --- validation ----------------------------------------------------------------------------------------------


def test_proposal_validation_uses_the_records():
    biz, other = _setup(), _setup()
    assert biz.owner.post(biz.url, json={"kind": "wire_money", "obligation_id": biz.inv}).status_code == 422
    assert _reminder(biz, extra="x").status_code == 422 and _payment(biz, executed=True).status_code == 422
    for msg in ("Pay here http://evil.example/pay now", "Pay to account 1234 5678 9012", "=HYPERLINK(\"x\")", "short", "   "):
        assert _reminder(biz, message=msg).status_code == 422, msg
    # A due date is not an account number (the default message names it); a number written with hyphens still is.
    dated = _reminder(biz, message="Invoice INV-108 for BDT 45,000.00 was due on 2026-10-05. Please arrange payment.")
    assert dated.status_code == 201, dated.text
    for msg in ("Due 2026-10-05, pay to 1234-5678-9012", "Pay to 1234-56-78 9012 today please"):
        assert _reminder(biz, message=msg).status_code == 422, msg
    assert _reminder(biz, channel="whatsapp").status_code == 422
    nf = _reminder(biz, obligation_id=biz.inv_future)
    assert nf.status_code == 422 and "not overdue" in nf.json()["detail"]
    assert _reminder(biz, obligation_id=biz.bill).status_code == 422             # a bill is not an invoice
    assert _reminder(biz, obligation_id=other.inv).status_code == 422            # another business's invoice is not found
    over = _payment(biz, amount_minor=6_000_001)
    assert over.status_code == 422 and "more than" in over.json()["detail"]
    assert _payment(biz, amount_minor=0).status_code == 422 and _payment(biz, amount_minor="100").status_code == 422
    cash = next(ref for ref, a in FIXTURE_ACCOUNTS.items() if a["kind"] == "cash")
    assert "bank or mobile wallet" in _payment(biz, from_account_id=biz.account[cash]).json()["detail"]
    assert _payment(biz, from_account_id=other.bank).status_code == 422
    assert _payment(biz, payment_review_id="00000000-0000-0000-0000-000000000000").status_code == 422
    assert biz.owner.post(f"{biz.base}/obligations/{biz.inv}/dispute", json={"disputed": True, "reason": "Wrong price"}).status_code == 200
    assert "disputed" in _reminder(biz).json()["detail"]


def test_financing_application_needs_a_ready_and_current_readiness_check():
    biz = readiness_setup()
    biz.url = f"{biz.base}/approvals"
    check = lambda: biz.owner.post(f"{biz.base}/financing-readiness", json={"run_id": biz.run["id"], "product_id": "wc-line"}).json()
    apply = lambda cid: biz.owner.post(biz.url, json={"kind": "financing_application", "readiness_check_id": cid})
    not_ready = check()
    assert not_ready["state"] == "not_ready"
    assert apply(not_ready["id"]).status_code == 422
    assert biz.owner.post(f"{biz.base}/cash/coverage", json={"account_id": biz.account["ACC-BANK"], "period_start": biz.day(100), "period_end": biz.today.isoformat(),
                                                             "source_kind": "statement", "note": "Statements"}).status_code == 201
    for doc in ({"kind": "id", "reference": "Passport copy, office drive"},
                {"kind": "trade_licence", "reference": "Framed, front office", "issued_on": "2024-01-15", "expires_on": (biz.today + timedelta(days=200)).isoformat()}):
        assert biz.owner.post(f"{biz.base}/business-documents", json=doc).status_code == 201
    ready = check()
    assert ready["state"] == "ready"
    r = apply(ready["id"])
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["tier"] == "T3" and p["payload"]["product"] == "Working capital line" and p["payload"]["illustrative"] is True
    assert p["payload"]["amount_minor"] == 4_000_000 and p["payload"]["recipient"] == "sandbox lender"
    assert "already open" in apply(ready["id"]).json()["detail"]                 # one open application per product
    assert biz.owner.post(f"{biz.base}/business-documents", json={"kind": "tax_certificate", "reference": "Tax folder"}).status_code == 201
    assert "register changed" in apply(ready["id"]).json()["detail"]
    newer = check()
    assert "newer readiness check" in apply(ready["id"]).json()["detail"] and newer["state"] in ("ready", "needs_verification")


def test_open_proposals_cannot_overcommit_a_bill_or_repeat_an_action():
    biz = _setup()
    a = _payment(biz, amount_minor=4_000_000)
    assert a.status_code == 201, a.text
    over = _payment(biz, amount_minor=3_000_000)                                 # 40,000 + 30,000 > 60,000 outstanding
    assert over.status_code == 422
    assert "already cover ৳ 40,000.00 of the ৳ 60,000.00 outstanding; at most ৳ 20,000.00 more" in over.json()["detail"]
    assert _payment(biz, amount_minor=2_000_000).status_code == 201             # exactly what is left
    a = a.json()
    assert biz.owner.post(f"{biz.url}/{a['id']}/withdraw", json={"payload_sha256": a["payload_sha256"], "reason": "Paying less"}).status_code == 200
    assert _payment(biz, amount_minor=3_000_000).status_code == 201             # a withdrawn proposal no longer counts

    first = _reminder(biz)
    assert first.status_code == 201
    again = _reminder(biz)
    assert again.status_code == 422 and "already open" in again.json()["detail"]
    first = first.json()
    assert biz.owner.post(f"{biz.url}/{first['id']}/decision", json={"decision": "reject", "payload_sha256": first["payload_sha256"],
                                                                     "reason": "Wrong wording"}).status_code == 200
    assert _reminder(biz).status_code == 201                                     # a rejected reminder no longer blocks


def test_release_refuses_payments_that_together_exceed_the_bill():
    biz = _setup()
    keeper, owner2 = biz.member("bookkeeper"), biz.member("owner")
    v = VERIFY | {"checked_on": biz.today.isoformat()}
    p1, p2 = _payment(biz, keeper, amount_minor=4_000_000).json(), _payment(biz, keeper, amount_minor=2_000_000).json()
    for p in (p1, p2):
        assert _approve(biz, biz.owner, p, password=PASSWORD, verification=v).status_code == 200
    r = biz.owner.post(f"{biz.base}/obligations/{biz.bill}/allocations", json={"amount_minor": 1_000_000, "currency": "BDT", "allocation_date": biz.today.isoformat()})
    assert r.status_code == 201, r.text                                         # 50,000 now outstanding
    assert _release(biz, owner2, p1).status_code == 200
    rel = _release(biz, owner2, p2)
    assert rel.status_code == 409
    bad = [c for c in rel.json()["detail"]["checks"] if not c["ok"]]
    assert [c["code"] for c in bad] == ["records_current"]
    assert "released payment proposals for DS-441 already cover ৳ 40,000.00 of the ৳ 50,000.00" in bad[0]["detail"]


# --- isolation, routes, database guards ----------------------------------------------------------------------


def test_isolation_and_no_execution_routes():
    biz, other = _setup(), _setup()
    p = _reminder(biz).json()
    for url in (f"{biz.url}/{p['id']}", biz.url):
        assert other.owner.get(url).status_code == 404
    assert _approve(other, other.owner, p).status_code == 404 and _release(other, other.owner, p).status_code == 404
    assert other.owner.post(f"{other.url}/{p['id']}/withdraw", json={"payload_sha256": p["payload_sha256"], "reason": "Not ours"}).status_code == 404
    assert other.owner.get(other.url).json()["items"] == []
    from app.main import app
    routes = {(m, r.path) for r in app.routes if "/approvals" in getattr(r, "path", "") for m in getattr(r, "methods", ())}
    assert {m for m, _ in routes} == {"GET", "POST"}
    assert not any(p.rsplit("/", 1)[-1] in {"execute", "send", "pay", "submit", "share"} for _, p in routes)


def test_database_guards_on_proposals_and_decisions():
    biz = _setup()
    owner2 = biz.member("owner")
    owner2_id = owner2.get("/api/auth/me").json()["user"]["id"]
    pay = _payment(biz).json()
    rem = _reminder(biz).json()
    with get_engine().connect() as conn:
        owner_id = conn.execute(text("SELECT proposed_by FROM action_proposal WHERE id = :p"), {"p": pay["id"]}).scalar_one()
        ins = """INSERT INTO action_decision (business_id, proposal_id, event, actor_id, actor_role, payload_sha256, reauthenticated_at, verification, created_at)
                 SELECT business_id, id, :e, :u, :r, coalesce(:h, payload_sha256), :ra, CAST(:v AS jsonb), now() FROM action_proposal WHERE id = :p"""
        base = {"e": "approved", "u": owner_id, "r": "owner", "h": None, "ra": None, "v": None, "p": pay["id"]}
        reviewer = {"u": owner2_id, "ra": utc_now(), "v": '{"method": "met_in_person"}'}
        for sql, params, match in (
            ("UPDATE action_proposal SET tier = 'T2' WHERE id = :p", {"p": pay["id"]}, "append-only"),
            ("DELETE FROM action_proposal WHERE id = :p", {"p": pay["id"]}, "append-only"),
            (ins, base | reviewer | {"h": "0" * 64}, "hash"),                          # wrong hash
            (ins, base | {"ra": utc_now(), "v": '{"method": "met_in_person"}'}, "second reviewer"),   # proposer approving a T3
            (ins, base | {"u": owner2_id}, "re-authentication"),                       # T3 without re-authentication or verification
            (ins, base | reviewer | {"ra": utc_now() - timedelta(minutes=10)}, "re-authentication"),  # stale re-authentication
            (ins, base | reviewer | {"r": "bookkeeper"}, "only the owner"),
            (ins, base | reviewer | {"e": "executable"}, "approved"),                  # release without an approval
        ):
            with pytest.raises(Exception, match=match):
                conn.execute(text(sql), params)
            conn.rollback()
    assert _approve(biz, biz.owner, rem).status_code == 200
    with get_engine().connect() as conn:
        with pytest.raises(Exception, match="duplicate|unique"):
            conn.execute(text("""INSERT INTO action_decision (business_id, proposal_id, event, actor_id, actor_role, payload_sha256, reason, created_at)
                                 SELECT business_id, id, 'rejected', proposed_by, 'owner', payload_sha256, 'again', now() FROM action_proposal WHERE id = :p"""),
                         {"p": rem["id"]})
        conn.rollback()
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("DELETE FROM action_decision WHERE proposal_id = :p"), {"p": rem["id"]})
        conn.rollback()
        with pytest.raises(Exception, match="check"):                                         # a T3 kind cannot be stored as T2
            conn.execute(text("""INSERT INTO action_proposal (business_id, kind, tier, required_role, second_reviewer_required, policy_version, payload,
                                 payload_sha256, proposed_by, proposed_role, created_at, expires_at)
                                 SELECT business_id, 'supplier_payment', 'T2', 'owner', false, policy_version, payload, payload_sha256, proposed_by,
                                 proposed_role, created_at, expires_at FROM action_proposal WHERE id = :p"""), {"p": pay["id"]})
