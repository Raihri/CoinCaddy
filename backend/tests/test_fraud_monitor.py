"""Transaction Fraud Monitor: versioned rules, holds on proposed sandbox payments, reviews, and redacted evidence.

Covers the four required cases (one hit, one legitimate lookalike, the hold, redacted audit evidence), every rule with a
non-trigger, the bounded cash outcomes (exact values), independent verification of a destination change, review rules,
access and isolation, and the database guards.
"""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db import get_engine
from app.modules.fraud import rules
from app.modules.records.service import business_today
from tests.helpers import PASSWORD, Biz

VERIFY_T3 = {"method": "called_known_contact", "reference": "Called Delta Supplies on the number in our supplier file"}


def _setup(reserve_minor: int = 5_000_000) -> Biz:
    biz = Biz(reserve_minor=reserve_minor)
    biz.today = business_today("Asia/Dhaka")
    biz.d = lambda n: (biz.today + timedelta(days=n)).isoformat()
    biz.bank = biz.account["ACC-BANK"]
    biz.fraud = f"{biz.base}/fraud"
    biz.approvals = f"{biz.base}/approvals"
    return biz


def _bill(biz, num: str, party: str, amount: int, due: int = 3) -> str:
    r = biz.owner.post(f"{biz.base}/obligations", json={"direction": "payable", "party_name": party, "document_number": num, "issue_date": biz.d(-20),
                                                        "due_date": biz.d(due), "amount_minor": amount, "currency": "BDT", "category": "inventory_suppliers",
                                                        "source_reference": "book", "settlement_account_id": biz.bank})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _post(biz, rows: list[tuple[str, str, int, str]]) -> dict[str, str]:
    """Import (date, payee, amount_minor, reference) rows into the bank account with a payee column; returns reference -> ledger id."""
    lines = ["Date,Payee,Description,Reference,Amount"]
    for d, payee, amt, ref in rows:
        lines.append(f"{d},{payee},Payment {ref},{ref},{'-' if amt < 0 else ''}{abs(amt) // 100}.{abs(amt) % 100:02d}")
    data = ("\r\n".join(lines) + "\r\n").encode()
    r = biz.owner.post(f"{biz.base}/imports", data={"account_id": biz.bank}, files={"file": (f"bank-{uuid.uuid4().hex[:6]}.csv", data, "text/csv")})
    assert r.status_code == 201, r.text
    b = r.json()
    b = biz.owner.put(f"{biz.base}/imports/{b['id']}/mapping", json={"expected_revision": b["revision"], "mapping": {
        "date_column": 0, "date_format": "YYYY-MM-DD", "counterparty_column": 1, "description_column": 2, "reference_column": 3,
        "amount_mode": "signed", "amount_column": 4}}).json()
    assert b["counts"]["valid"] == len(rows) and b["counts"]["pending"] == 0, b["counts"]
    r = biz.owner.post(f"{biz.base}/imports/{b['id']}/commit", json={"expected_revision": b["revision"], "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    with get_engine().connect() as conn:
        return {x.reference: str(x.id) for x in conn.execute(text("SELECT id, reference FROM ledger_transaction WHERE source_batch_id = :s"), {"s": b["id"]})}


def _anchor(biz, balance: int) -> None:
    r = biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": biz.bank, "anchor_date": biz.today.isoformat(), "balance_minor": balance,
                                                         "currency": "BDT", "source_kind": "bank_statement", "source_reference": "Statement today"})
    assert r.status_code == 201, r.text


def _pay(biz, client, bill: str, amount: int):
    return client.post(biz.approvals, json={"kind": "supplier_payment", "obligation_id": bill, "amount_minor": amount, "from_account_id": biz.bank})


def _flags(biz, client=None, state: str = "all") -> list[dict]:
    r = (client or biz.owner).get(f"{biz.fraud}/flags?state={state}")
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _review(biz, client, flag_id: str, decision: str, reason: str = "Checked against the supplier file", **kw):
    return client.post(f"{biz.fraud}/flags/{flag_id}/reviews", json={"decision": decision, "reason": reason} | kw)


def _verification(biz, **kw) -> dict:
    return {"method": "called_known_contact", "contact_source": "Phone number in the supplier file since 2024", "contact_predates_change": True,
            "checked_on": biz.today.isoformat()} | kw


def _events(biz) -> list[dict]:
    return biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]


def _scan(biz, client=None) -> dict:
    r = (client or biz.owner).post(f"{biz.fraud}/scans")
    assert r.status_code == 201, r.text
    return r.json()


# --- required case 1 and 3: one hit, and the hold on a proposed sandbox payment ----------------------------------


def test_duplicate_payment_hit_holds_the_proposed_payment_with_two_bounded_outcomes():
    biz = _setup(reserve_minor=5_000_000)
    keeper, owner2 = biz.member("bookkeeper"), biz.member("owner")
    bill = _bill(biz, "DS-441", "Delta Supplies", 300_000)
    txn = _post(biz, [(biz.d(-4), "Office rent", -2_800_000, "RENT-OCT"), (biz.d(-2), "Staff payroll", -6_200_000, "PAY-OCT"),
                      (biz.d(-1), "Delta Supplies", -300_000, "DS-441")])
    _anchor(biz, 5_700_000)

    # The bank already shows BDT 3,000 to Delta Supplies for DS-441 (not allocated to the bill); paying the bill again is a possible duplicate.
    p = _pay(biz, keeper, bill, 300_000)
    assert p.status_code == 201, p.text
    p = p.json()
    assert [h["rule"] for h in p["fraud_holds"]] == ["duplicate_payment"]
    assert biz.owner.get(f"{biz.approvals}/{p['id']}").json()["can"] == {"approve": False, "reject": True, "withdraw": True, "release": False}

    [flag] = _flags(biz)
    assert flag["rule"] == "duplicate_payment" and flag["rules_version"] == "fraud-rules-v1" and flag["severity"] == "high"
    assert flag["state"] == "open" and flag["holding"] is True and flag["amount_minor"] == 300_000
    assert flag["subject_refs"] == [{"type": "action_proposal", "id": p["id"]}]
    assert flag["compared_refs"] == [{"type": "ledger_transaction", "id": txn["DS-441"]}]
    assert "the same payee and the same reference" in flag["reason"] and "not a finding of fraud" in flag["verdict"]
    assert flag["evidence"]["compared"][0]["date"] == biz.d(-1) and flag["evidence"]["compared"][0]["payee"] == "Delta Supplies"

    # Two bounded outcomes on today's Cash Outlook inputs (hand-checked): opening 57,000.00, bill DS-441 3,000.00 due in 3 days.
    # If genuine: the 3,000.00 leaves tomorrow and settles DS-441 -> lowest 54,000.00. If duplicate: DS-441 was already paid -> 57,000.00.
    detail = biz.owner.get(f"{biz.fraud}/flags/{flag['id']}").json()
    co = detail["cash_outcomes"]
    assert co["available"] is True and co["reserve_minor"] == 5_000_000 and co["materiality_threshold_minor"] == 50_000
    genuine, duplicate = co["outcomes"]
    assert genuine["label"] == "If genuine" and genuine["lowest_expected"] == {"date": biz.d(1), "amount_minor": 5_400_000}
    assert duplicate["label"] == "If duplicate" and duplicate["lowest_expected"]["amount_minor"] == 5_700_000
    assert genuine["below_reserve"] is False and duplicate["below_reserve"] is False and co["difference_minor"] == 300_000
    assert len(co["inputs_sha256"]) == 64 and co["calc_version"] == "outlook-v1"

    # The hold: the owner cannot approve (API), and the database refuses an approval written directly.
    v = VERIFY_T3 | {"checked_on": biz.today.isoformat()}
    r = biz.owner.post(f"{biz.approvals}/{p['id']}/decision", json={"decision": "approve", "payload_sha256": p["payload_sha256"], "password": PASSWORD, "verification": v})
    assert r.status_code == 409 and "on hold in the Fraud Monitor" in r.json()["detail"]
    with get_engine().connect() as conn, pytest.raises(Exception, match="held by the fraud monitor"):
        conn.execute(text("""INSERT INTO action_decision (business_id, proposal_id, event, actor_id, actor_role, payload_sha256, reauthenticated_at, verification, created_at)
                             SELECT p.business_id, p.id, 'approved', m.user_id, 'owner', p.payload_sha256, now(), '{"method": "met_in_person"}', now()
                             FROM action_proposal p JOIN membership m ON m.business_id = p.business_id AND m.role = 'owner' WHERE p.id = :p LIMIT 1"""), {"p": p["id"]})

    # The table: the proposal is on hold; the earlier Delta payment and the others are posted.
    rows = {r["id"]: r for r in biz.owner.get(f"{biz.fraud}/transactions").json()["items"]}
    assert rows[p["id"]]["status"] == "on_hold" and rows[p["id"]]["flag_ids"] == [flag["id"]]
    assert {rows[txn[k]]["status"] for k in txn} == {"posted"}
    ov = biz.owner.get(f"{biz.fraud}/overview").json()
    assert ov["unresolved"] == 1 and [h["id"] for h in ov["held_payments"]] == [p["id"]]

    # Repeated scans never duplicate the flag.
    assert _scan(biz)["flags_created"] == 0 and _scan(biz)["flags_existing"] == 1 and len(_flags(biz)) == 1

    # The proposer cannot clear the hold; a bookkeeper cannot clear a hold at all; a second owner can, with a reason.
    assert _review(biz, keeper, flag["id"], "reject").status_code == 403
    cleared = _review(biz, owner2, flag["id"], "reject", "Earlier payment was for DS-440; bank advice checked")
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["state"] == "cleared" and cleared.json()["holding"] is False
    assert biz.owner.get(f"{biz.approvals}/{p['id']}").json()["fraud_holds"] == []

    # Cleared: the payment can now be approved and released, and the release check includes the Fraud Monitor.
    r = biz.owner.post(f"{biz.approvals}/{p['id']}/decision", json={"decision": "approve", "payload_sha256": p["payload_sha256"], "password": PASSWORD, "verification": v})
    assert r.status_code == 200, r.text
    rel = biz.owner.post(f"{biz.approvals}/{p['id']}/release", json={"payload_sha256": p["payload_sha256"]})
    assert rel.status_code == 200, rel.text
    checks = {c["code"]: c["ok"] for c in rel.json()["decisions"][-1]["checks"]}
    assert checks["fraud_hold"] is True and rel.json()["executed"] is False
    ev = [e for e in _events(biz) if e["action"] in ("fraud.review", "approvals.decide")]
    assert any(e["action"] == "approvals.decide" and e["outcome"] == "conflict" and e["details"]["reason"] == "fraud hold" for e in ev)
    assert any(e["action"] == "fraud.review" and e["details"]["decision"] == "rejected" and e["details"]["hold_released"] for e in ev)


def test_confirmed_suspicion_keeps_the_hold_and_is_final():
    biz = _setup()
    owner2 = biz.member("owner")
    bill = _bill(biz, "DS-500", "Delta Supplies", 1_000_000)
    _post(biz, [(biz.d(-1), "Delta Supplies", -1_000_000, "DS-500")])
    p = _pay(biz, biz.owner, bill, 1_000_000).json()
    [flag] = _flags(biz)
    r = _review(biz, owner2, flag["id"], "confirm", "Same invoice paid yesterday; escalated to the bank")
    assert r.status_code == 200 and r.json()["state"] == "confirmed" and r.json()["holding"] is True
    assert _review(biz, owner2, flag["id"], "reject").status_code == 409            # final
    assert _review(biz, owner2, flag["id"], "defer", defer_until=biz.d(3)).status_code == 409
    v = VERIFY_T3 | {"checked_on": biz.today.isoformat()}
    r = owner2.post(f"{biz.approvals}/{p['id']}/decision", json={"decision": "approve", "payload_sha256": p["payload_sha256"], "password": PASSWORD, "verification": v})
    assert r.status_code == 409
    # The owner can still reject the held proposal itself.
    assert owner2.post(f"{biz.approvals}/{p['id']}/decision", json={"decision": "reject", "payload_sha256": p["payload_sha256"],
                                                                    "reason": "Duplicate payment"}).status_code == 200


# --- required case 2: one legitimate lookalike ---------------------------------------------------------------------


def test_legitimate_lookalike_is_flagged_then_cleared_and_never_reflagged():
    biz = _setup()
    keeper = biz.member("bookkeeper")
    txn = _post(biz, [(biz.d(-60), "Rahman Traders", -1_000_000, "RT-1"), (biz.d(-40), "Rahman Traders", -1_100_000, "RT-2"),
                      (biz.d(-20), "Rahman Traders", -900_000, "RT-3"), (biz.d(-2), "Rahman Trader", -1_200_000, "RTR-1")])
    res = _scan(biz, keeper)
    assert res["flags_created"] == 1
    [flag] = _flags(biz)
    assert flag["rule"] == "lookalike_payee" and flag["severity"] == "medium" and flag["holding"] is False
    assert flag["subject_refs"] == [{"type": "ledger_transaction", "id": txn["RTR-1"]}]
    assert {r["id"] for r in flag["compared_refs"]} == {txn["RT-1"], txn["RT-2"], txn["RT-3"]}
    assert flag["evidence"]["similarity_percent"] == 92 and flag["evidence"]["established_name"] == "Rahman Traders"
    rows = {r["id"]: r["status"] for r in biz.owner.get(f"{biz.fraud}/transactions?days=90").json()["items"]}
    assert rows[txn["RTR-1"]] == "review" and rows[txn["RT-3"]] == "posted"
    # BDT 12,000 is material (over 1% of the BDT 50,000 reserve), but with no balance anchor the outcomes cannot be bounded.
    assert "no verified balance" in biz.owner.get(f"{biz.fraud}/flags/{flag['id']}").json()["cash_outcomes"]["reason"]

    # A bookkeeper may clear a flag that holds no payment.
    r = _review(biz, keeper, flag["id"], "reject", "Rahman Trader is a separate shop; trade licence on file")
    assert r.status_code == 200 and r.json()["state"] == "cleared"
    assert _scan(biz)["flags_created"] == 0
    [again] = _flags(biz)
    assert again["id"] == flag["id"] and again["state"] == "cleared"
    rows = {r["id"]: r["status"] for r in biz.owner.get(f"{biz.fraud}/transactions?days=90").json()["items"]}
    assert rows[txn["RTR-1"]] == "posted"


def test_lookalike_by_folded_characters_is_high_severity():
    biz = _setup()
    _post(biz, [(biz.d(-30), "Delta Supplies", -500_000, "DS-1"), (biz.d(-1), "De1ta Supp1ies", -500_000, "DS-2")])
    _scan(biz)
    look = [f for f in _flags(biz) if f["rule"] == "lookalike_payee"]
    assert len(look) == 1 and look[0]["severity"] == "high" and look[0]["evidence"]["match"] == "characters"


# --- the other rules, each with a non-trigger ----------------------------------------------------------------------


def test_unusual_amount_split_payments_and_paid_without_settlement():
    biz = _setup()
    bill_a, bill_b, bill_c = _bill(biz, "GP-9", "Green Packaging", 400_000), _bill(biz, "OF-1", "Orbit Foods", 250_000), _bill(biz, "OF-2", "Orbit Foods", 180_000)
    txn = _post(biz, [
        (biz.d(-50), "Green Packaging", -100_000, "GP-1"), (biz.d(-35), "Green Packaging", -120_000, "GP-2"), (biz.d(-20), "Green Packaging", -100_000, "GP-3"),
        (biz.d(-3), "Green Packaging", -400_000, "GP-4"),                                     # 4x the median 1,000.00 -> unusual
        (biz.d(-40), "Blue Ink", -100_000, "BI-1"), (biz.d(-30), "Blue Ink", -110_000, "BI-2"), (biz.d(-5), "Blue Ink", -900_000, "BI-3"),  # only 2 earlier: no flag
        (biz.d(-6), "Quick Movers", -3_500_000, "QM-1"), (biz.d(-5), "Quick Movers", -3_000_000, "QM-2"),   # 70% + 60% of the 50,000.00 limit
        (biz.d(-6), "Slow Movers", -2_400_000, "SM-1"), (biz.d(-5), "Slow Movers", -2_900_000, "SM-2"),     # 48% is not near the limit: no flag
        (biz.d(-9), "Orbit Foods", -180_000, "OF-2-BANK"),                                    # the bank movement behind OF-2's payment
    ])
    alloc = lambda ob, amt, days: biz.owner.post(f"{biz.base}/obligations/{ob}/allocations", json={"amount_minor": amt, "currency": "BDT", "allocation_date": biz.d(days)})
    assert alloc(bill_b, 250_000, -6).status_code == 201       # no movement of 2,500.00 anywhere -> paid without settlement
    assert alloc(bill_c, 180_000, -7).status_code == 201       # an unlinked 1,800.00 movement 2 days apart -> no flag
    assert alloc(bill_a, 50_000, -1).status_code == 201        # only 1 day old -> no flag yet
    _scan(biz)
    by_rule: dict = {}
    for f in _flags(biz):
        by_rule.setdefault(f["rule"], []).append(f)
    assert set(by_rule) == {"unusual_payee_amount", "split_near_limit", "paid_without_settlement"}

    [unusual] = by_rule["unusual_payee_amount"]
    assert unusual["subject_refs"] == [{"type": "ledger_transaction", "id": txn["GP-4"]}] and unusual["evidence"]["median_minor"] == 100_000
    assert {r["id"] for r in unusual["compared_refs"]} == {txn["GP-1"], txn["GP-2"], txn["GP-3"]} and "about 4x" in unusual["reason"]

    [split] = by_rule["split_near_limit"]
    assert {r["id"] for r in split["subject_refs"]} == {txn["QM-1"], txn["QM-2"]} and split["amount_minor"] == 6_500_000
    assert split["evidence"]["limit_minor"] == 5_000_000 and "৳ 50,000.00" in split["reason"]

    [unsettled] = by_rule["paid_without_settlement"]
    assert unsettled["subject_refs"][0]["type"] == "allocation" and unsettled["compared_refs"] == [{"type": "obligation", "id": bill_b}]
    assert "OF-1" in unsettled["reason"] and unsettled["amount_minor"] == 250_000


# --- destination changes: hold until independently verified; redacted audit evidence (required case 4) ------------


def test_destination_change_holds_payments_until_independently_verified_and_evidence_is_redacted():
    biz = _setup()
    keeper, owner2 = biz.member("bookkeeper"), biz.member("owner")
    bill = _bill(biz, "DS-700", "Delta Supplies", 2_000_000)
    party = next(p for p in biz.owner.get(f"{biz.fraud}/payee-destinations").json()["parties"] if p["name"] == "Delta Supplies")["party_id"]
    dest = lambda client, **kw: client.post(f"{biz.fraud}/payee-destinations", json={"party_id": party, "kind": "bank", "institution": "Dutch-Bangla Bank",
                                                                                      "account_number": "1234 5678 9012", "change_source": "onboarding"} | kw)
    first = dest(keeper)
    assert first.status_code == 201, first.text
    assert first.json()["is_change"] is False and first.json()["flag_id"] is None and first.json()["masked"] == "••••9012"
    assert dest(keeper).status_code == 409                                         # the same destination again is not a change

    p = _pay(biz, biz.owner, bill, 2_000_000).json()
    assert p["fraud_holds"] == []
    change = dest(keeper, institution="City Bank", account_number="9999-8888-7777", change_source="supplier_email", note="New account letter by email")
    assert change.status_code == 201, change.text
    change = change.json()
    assert change["is_change"] is True and change["flag_id"] and change["scan"]["holds_added"] == 1 and change["masked"] == "••••7777"
    held = biz.owner.get(f"{biz.approvals}/{p['id']}").json()
    assert [h["rule"] for h in held["fraud_holds"]] == ["destination_change"] and held["can"]["approve"] is False

    flag = biz.owner.get(f"{biz.fraud}/flags/{change['flag_id']}").json()
    assert flag["severity"] == "high" and flag["holding"] and [h["proposal_id"] for h in flag["holds"]] == [p["id"]]
    assert "••••9012" in flag["reason"] and "••••7777" in flag["reason"] and flag["can"]["verification_required"] is True
    assert flag["cash_outcomes"]["available"] is False and "no verified balance" in flag["cash_outcomes"]["reason"]   # no anchor: not bounded

    # A later payment proposal to the same payee is held by the same flag.
    q = _pay(biz, owner2, _bill(biz, "DS-701", "Delta Supplies", 500_000), 500_000).json()
    assert [h["flag_id"] for h in q["fraud_holds"]] == [change["flag_id"]]

    # Clearing needs an owner, who proposed none of the held payments, did not record the change, and verified on an old contact.
    clear = lambda client, **kw: _review(biz, client, change["flag_id"], "reject", "Called Delta on the file number; change confirmed", **kw)
    assert clear(keeper, verification=_verification(biz)).status_code == 403
    assert clear(biz.owner, verification=_verification(biz)).status_code == 409                # proposed a held payment
    assert clear(owner2, verification=_verification(biz)).status_code == 409                   # proposed DS-701
    owner3 = biz.member("owner")
    assert clear(owner3).status_code == 422                                                    # no verification
    assert clear(owner3, verification=_verification(biz, contact_predates_change=False)).status_code == 422   # contact from the change request
    assert clear(owner3, verification=_verification(biz, method="written_confirmation")).status_code == 422
    assert clear(owner3, verification=_verification(biz, checked_on=biz.d(1))).status_code == 422
    assert clear(owner3, verification=_verification(biz, contact_source="Called 0171 2345 6789")).status_code == 422
    deferred = _review(biz, owner3, change["flag_id"], "defer", "Waiting for Delta to call back", defer_until=biz.d(2))
    assert deferred.status_code == 200 and deferred.json()["state"] == "deferred" and deferred.json()["holding"] is True
    ok = clear(owner3, verification=_verification(biz))
    assert ok.status_code == 200, ok.text
    assert ok.json()["state"] == "cleared" and ok.json()["reviews"][-1]["verification"]["method"] == "called_known_contact"
    assert biz.owner.get(f"{biz.approvals}/{p['id']}").json()["fraud_holds"] == []

    # The recorder of a change can never verify it, even as an owner.
    later = dest(owner3, institution="BRAC Bank", account_number="5555 4444 3333", change_source="phone_call_received").json()
    r = _review(biz, owner3, later["flag_id"], "reject", "I recorded it myself", verification=_verification(biz))
    assert r.status_code == 409 and "someone else" in r.json()["detail"]

    # Redacted evidence: the full numbers are nowhere in the audit trail, the API, or the stored rows; the last four digits are.
    full = ("123456789012", "999988887777", "555544443333")
    events = _events(biz)
    rec = [e for e in events if e["action"] == "payees.destination_record"]
    assert len(rec) == 3 and {e["details"]["account_number"] for e in rec} == {"••••9012", "••••7777", "••••3333"}
    listing = biz.owner.get(f"{biz.fraud}/payee-destinations").text + biz.owner.get(f"{biz.fraud}/flags").text
    with get_engine().connect() as conn:
        stored = conn.execute(text("SELECT coalesce(string_agg(to_jsonb(d)::text, ''), '') FROM payee_destination d WHERE business_id = :b"), {"b": biz.id}).scalar_one()
        stored += conn.execute(text("SELECT coalesce(string_agg(to_jsonb(f)::text, ''), '') FROM fraud_flag f WHERE business_id = :b"), {"b": biz.id}).scalar_one()
    for blob in (str([e["details"] for e in events]), listing, stored):
        assert not any(n in blob.replace(" ", "").replace("-", "") for n in full)
    assert "7777" in listing and "7777" in stored


def test_destination_validation():
    biz = _setup()
    _bill(biz, "DS-1", "Delta Supplies", 100_000)
    party = biz.owner.get(f"{biz.fraud}/payee-destinations").json()["parties"][0]["party_id"]
    post = lambda **kw: biz.owner.post(f"{biz.fraud}/payee-destinations", json={"party_id": party, "kind": "bank", "institution": "City Bank",
                                                                                "account_number": "1234567890", "change_source": "onboarding"} | kw)
    assert post(account_number="12AB34").status_code == 422
    assert post(account_number="12345").status_code == 422                          # too short for a bank account
    assert post(kind="mobile_wallet", account_number="0121234567").status_code == 422
    assert post(kind="mobile_wallet", account_number="017 1234 5678").status_code == 201
    assert post(institution="=HYPERLINK(1)").status_code == 422 and post(institution="City Bank 1234 5678 90").status_code == 422
    assert post(note="Acct 1234 5678 9012").status_code == 422 and post(change_source="fax").status_code == 422
    assert post(party_id=str(uuid.uuid4())).status_code == 422 and post(extra=1).status_code == 422
    other = _setup()
    _bill(other, "X-1", "Other Supplier", 100_000)
    other_party = other.owner.get(f"{other.fraud}/payee-destinations").json()["parties"][0]["party_id"]
    assert post(party_id=other_party).status_code == 422                            # another business's payee
    with get_engine().connect() as conn, pytest.raises(Exception, match="check"):
        conn.execute(text("""INSERT INTO payee_destination (business_id, party_id, kind, institution, last4, number_hmac, change_source, note, recorded_by, recorded_role)
                             SELECT business_id, party_id, kind, institution, last4, number_hmac, change_source, 'acct 1234 5678 9012', recorded_by, recorded_role
                             FROM payee_destination WHERE business_id = :b LIMIT 1"""), {"b": biz.id})


# --- review rules, access, isolation, preserved originals, database guards ----------------------------------------


def test_review_validation_access_and_isolation():
    biz, other = _setup(), _setup()
    staff, keeper = biz.member("staff"), biz.member("bookkeeper")
    _post(biz, [(biz.d(-30), "Delta Supplies", -500_000, "DS-1"), (biz.d(-1), "Delta Suppliez", -500_000, "DS-2")])
    _scan(biz)
    [flag] = _flags(biz)
    url = f"{biz.fraud}/flags/{flag['id']}"
    for path in ("/flags", "/overview", "/transactions", "/rules", "/payee-destinations", f"/flags/{flag['id']}"):
        assert staff.get(biz.fraud + path).status_code == 403, path
    assert staff.post(f"{biz.fraud}/scans").status_code == 403 and _review(biz, staff, flag["id"], "confirm").status_code == 403
    for path in ("/flags", f"/flags/{flag['id']}"):
        assert other.owner.get(biz.fraud + path).status_code == 404
    assert other.owner.get(f"{other.fraud}/flags/{flag['id']}").status_code == 404
    assert _review(other, other.owner, flag["id"], "confirm").status_code == 404
    assert other.owner.get(f"{other.fraud}/flags").json()["items"] == []

    assert _review(biz, keeper, flag["id"], "confirm", "no").status_code == 422                       # reason too short
    assert _review(biz, keeper, flag["id"], "confirm", "See http://x.example").status_code == 422
    assert _review(biz, keeper, flag["id"], "defer").status_code == 422                               # no date
    assert _review(biz, keeper, flag["id"], "defer", defer_until=biz.today.isoformat()).status_code == 422
    assert _review(biz, keeper, flag["id"], "defer", defer_until=biz.d(31)).status_code == 422
    assert _review(biz, keeper, flag["id"], "confirm", defer_until=biz.d(3)).status_code == 422
    assert _review(biz, keeper, flag["id"], "confirm", verification=_verification(biz)).status_code == 422
    assert _review(biz, keeper, flag["id"], "approve").status_code == 422 and _review(biz, keeper, flag["id"], "confirm", extra=1).status_code == 422
    d = _review(biz, keeper, flag["id"], "defer", "Asking the supplier", defer_until=biz.d(7))
    assert d.status_code == 200 and d.json()["state"] == "deferred" and d.json()["review_due"] is False
    assert _review(biz, keeper, flag["id"], "confirm", "Supplier denies the second payee").json()["state"] == "confirmed"
    assert [r["decision"] for r in biz.owner.get(url).json()["reviews"]] == ["deferred", "confirmed"]

    from app.main import app
    routes = {(m, r.path) for r in app.routes if "/fraud" in getattr(r, "path", "") for m in getattr(r, "methods", ())}
    assert {m for m, _ in routes} == {"GET", "POST"}
    assert not any(seg in {"execute", "pay", "send", "submit", "release", "approve"} for _, p in routes for seg in p.split("/"))
    denied = {e["action"] for e in _events(biz) if e["outcome"] == "denied"}
    assert {"fraud.read", "fraud.run", "fraud.review"} <= denied


def test_reviews_preserve_original_records_and_database_guards():
    biz = _setup()
    owner2 = biz.member("owner")
    bill = _bill(biz, "DS-9", "Delta Supplies", 700_000)
    _post(biz, [(biz.d(-2), "Delta Supplies", -700_000, "DS-9")])
    p = _pay(biz, biz.owner, bill, 700_000).json()
    snap = lambda conn: [conn.execute(text(f"SELECT md5(coalesce(string_agg(to_jsonb(t)::text, '' ORDER BY id), '')) FROM {t} t WHERE business_id = :b"),
                                      {"b": biz.id}).scalar_one() for t in ("ledger_transaction", "obligation", "allocation", "action_proposal")]
    with get_engine().connect() as conn:
        before = snap(conn)
    [flag] = _flags(biz)
    assert _review(biz, owner2, flag["id"], "defer", "Checking the bank advice", defer_until=biz.d(1)).status_code == 200
    assert _review(biz, owner2, flag["id"], "reject", "Bank advice shows it was for DS-8").status_code == 200
    with get_engine().connect() as conn:
        assert snap(conn) == before                                               # originals untouched
        assert biz.owner.get(f"{biz.approvals}/{p['id']}").json()["integrity"]["payload_hash_matches"] is True
        for sql, match in (("UPDATE fraud_flag SET severity = 'medium' WHERE business_id = :b", "append-only"),
                           ("DELETE FROM fraud_review WHERE business_id = :b", "append-only"),
                           ("DELETE FROM fraud_flag_hold WHERE business_id = :b", "append-only"),
                           ("UPDATE fraud_scan SET flags_created = 0 WHERE business_id = :b", "append-only"),
                           ("""INSERT INTO fraud_review (business_id, flag_id, decision, reason, reviewer_id, reviewer_role)
                               SELECT business_id, flag_id, 'confirmed', 'late', reviewer_id, 'owner' FROM fraud_review WHERE business_id = :b LIMIT 1""", "final review")):
            with pytest.raises(Exception, match=match):
                conn.execute(text(sql), {"b": biz.id})
            conn.rollback()


def test_database_refuses_unverified_or_self_verified_destination_clearance():
    biz = _setup()
    keeper = biz.member("bookkeeper")
    _bill(biz, "DS-2", "Delta Supplies", 100_000)
    party = biz.owner.get(f"{biz.fraud}/payee-destinations").json()["parties"][0]["party_id"]
    body = {"party_id": party, "kind": "bank", "institution": "City Bank", "change_source": "onboarding"}
    assert keeper.post(f"{biz.fraud}/payee-destinations", json=body | {"account_number": "11112222"}).status_code == 201
    flag_id = keeper.post(f"{biz.fraud}/payee-destinations", json=body | {"account_number": "33334444", "change_source": "supplier_email"}).json()["flag_id"]
    ins = """INSERT INTO fraud_review (business_id, flag_id, decision, reason, verification, reviewer_id, reviewer_role)
             SELECT f.business_id, f.id, 'rejected', 'cleared', CAST(:v AS jsonb), :u, 'owner' FROM fraud_flag f WHERE f.id = :f"""
    with get_engine().connect() as conn:
        recorder = conn.execute(text("SELECT recorded_by FROM payee_destination d JOIN fraud_flag f ON f.destination_id = d.id WHERE f.id = :f"), {"f": flag_id}).scalar_one()
        owner = conn.execute(text("SELECT user_id FROM membership WHERE business_id = :b AND role = 'owner'"), {"b": biz.id}).scalar_one()
        for v, u, match in ((None, owner, "independent verification"), ('{"method": "written_confirmation", "contact_predates_change": true}', owner, "independent"),
                            ('{"method": "called_known_contact", "contact_predates_change": true}', recorder, "recorded the destination change")):
            with pytest.raises(Exception, match=match):
                conn.execute(text(ins), {"v": v, "u": u, "f": flag_id})
            conn.rollback()


# --- the pure rules ---------------------------------------------------------------------------------------------------


def test_rule_helpers_and_determinism():
    assert rules.payee_key("Delta Supplies Ltd.") == rules.payee_key("  delta   SUPPLIES ") == "delta supplies"
    assert rules.lookalike("delta supplies", "de1ta supplies") == "characters"
    assert rules.lookalike("rahman traders", "rahman trader") == "spelling"
    assert rules.lookalike("delta supplies", "orbit foods") is None and rules.lookalike("abc", "abd") is None
    item = lambda i, d, payee, amt, t="ledger_transaction": {"type": t, "id": i, "date": d, "payee": payee, "payee_key": rules.payee_key(payee),
                                                              "amount_minor": amt, "currency": "BDT", "reference": None, "account_id": "a", "account": "Bank",
                                                              "status": "posted", "party_id": None}
    three = [item("t1", "2026-10-01", "Delta", 500), item("t2", "2026-10-03", "Delta", 500), item("t3", "2026-10-05", "Delta", 500)]
    pairs = [c["dedup_key"] for c in rules.duplicate_payments(three)]
    assert pairs == ["duplicate_payment:t1:t2", "duplicate_payment:t2:t3"]                  # nearest earlier match only
    proposal_first = [item("p1", "2026-10-01", "Delta", 500, "action_proposal"), item("t9", "2026-10-02", "Delta", 500)]
    assert rules.duplicate_payments(proposal_first)[0]["subject_refs"] == [{"type": "action_proposal", "id": "p1"}]
    kw = dict(as_of=business_today("Asia/Dhaka"), parties=[], destinations=[], allocations=[], movements=[])
    recent = [x | {"date": (kw["as_of"] - timedelta(days=n)).isoformat()} for n, x in zip((5, 3, 1), three)]
    assert rules.evaluate(items=recent, **kw) == rules.evaluate(items=list(reversed(recent)), **kw)
    old = [x | {"date": (kw["as_of"] - timedelta(days=400 + n)).isoformat()} for n, x in enumerate(three)]
    assert rules.evaluate(items=old, **kw) == []                                          # outside the history window
