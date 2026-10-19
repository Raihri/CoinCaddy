"""Trusted Cash Reconstruction tests. Expected values: fixtures/cash_reconstruction_expected.md."""

from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
from app.fixtures.seed import seed
from app.modules.cash.service import find_gaps, merge_intervals
from tests.helpers import Biz

AS_OF = "2026-10-16"
D = date.fromisoformat


@pytest.fixture(autouse=True)
def _seed():
    seed()


def anchor(biz, acc, d, bal, kind="bank_statement", client=None):
    return (client or biz.owner).post(f"{biz.base}/cash/anchors", json={
        "account_id": biz.account[acc], "anchor_date": d, "balance_minor": bal, "currency": "BDT",
        "source_kind": kind, "source_reference": f"Statement {d}"})


def coverage(biz, acc, start, end, **extra):
    body = {"account_id": biz.account[acc], "period_start": start, "period_end": end, "source_kind": "statement", **extra}
    return biz.owner.post(f"{biz.base}/cash/coverage", json=body)


def position(biz, as_of=AS_OF, client=None):
    r = (client or biz.owner).get(f"{biz.base}/cash/position?as_of={as_of}")
    assert r.status_code == 200, r.text
    return r.json()


def acct(pos, label):
    return next(a for a in pos["accounts"] if a["account"]["label"] == label)


@pytest.fixture
def setup() -> Biz:
    """The full scenario from the expected-values document, without the transfer link."""
    biz = Biz().post_fixture()
    for d, bal in (("2026-10-01", 18_500_000), ("2026-10-15", 16_224_975)):
        assert anchor(biz, "ACC-BANK", d, bal).status_code == 201
    for d, bal in (("2026-10-01", 4_250_050), ("2026-10-15", 6_300_035)):
        assert anchor(biz, "ACC-WALLET", d, bal, "wallet_statement").status_code == 201
    assert anchor(biz, "ACC-CASH", "2026-09-20", 1_200_000, "cash_count").status_code == 201
    r = coverage(biz, "ACC-BANK", "2026-10-02", "2026-10-15", source_kind="import_batch", import_batch_id=biz.batch["ACC-BANK"])
    assert r.status_code == 201, r.text
    assert coverage(biz, "ACC-WALLET", "2026-10-02", "2026-10-09").status_code == 201
    r = biz.owner.post(f"{biz.base}/cash/restrictions", json={
        "account_id": biz.account["ACC-BANK"], "amount_minor": 2_000_000, "currency": "BDT",
        "reason": "Security deposit held by bank", "effective_from": "2026-10-01"})
    assert r.status_code == 201, r.text
    return biz


def link(biz, out_ref=None, in_ref=None):
    body = {}
    if out_ref:
        body["out_txn_id"] = biz.txn[out_ref]
    if in_ref:
        body["in_txn_id"] = biz.txn[in_ref]
    return biz.owner.post(f"{biz.base}/cash/transfers", json=body)


# --- the four required cases --------------------------------------------------------------------------


def test_balanced_period(setup):
    bank = acct(position(setup), "Operating bank account")
    [p] = bank["periods"]
    assert (p["opening_minor"], p["movements_minor"], p["movement_count"], p["closing_minor"], p["residual_minor"]) == (
        18_500_000, -2_275_025, 4, 16_224_975, 0)
    assert p["status"] == "balanced" and p["coverage_gaps"] == []
    assert sorted(p["transaction_ids"]) == sorted(setup.txn[r] for r in ("T001", "T002", "T004", "T007"))
    assert (bank["evidence_through"], bank["age_days"], bank["freshness"]) == ("2026-10-15", 1, "fresh")
    assert (bank["verified_balance_minor"], bank["restricted_minor"], bank["usable_minor"]) == (16_224_975, 2_000_000, 14_224_975)


def test_missing_period_zero_residual_is_not_complete(setup):
    wallet = acct(position(setup), "Merchant mobile wallet")
    [p] = wallet["periods"]
    assert p["residual_minor"] == 0 and p["movements_minor"] == 2_049_985
    assert p["status"] == "balanced_incomplete"
    assert p["coverage_gaps"] == [{"start": "2026-10-10", "end": "2026-10-15"}]
    assert wallet["status"] == "needs_review"
    assert any(i["code"] == "coverage_gap" and "not proven" in i["message"] for i in wallet["issues"])
    assert (wallet["verified_balance_minor"], wallet["freshness"]) == (6_300_035, "fresh")


def test_stale_anchor_is_not_summed(setup):
    pos = position(setup)
    cash = acct(pos, "Cash box")
    assert (cash["evidence_through"], cash["age_days"], cash["freshness"], cash["status"]) == ("2026-09-20", 26, "stale", "stale")
    assert cash["verified_balance_minor"] == 1_200_000
    assert cash["uncovered_movements"] == {"count": 1, "sum_minor": -350_000, "transaction_ids": [setup.txn["T006"]]}
    [bdt] = pos["totals"]
    assert [s["label"] for s in bdt["stale"]] == ["Cash box"] and bdt["missing"] == []
    assert "Cash box" not in [i["label"] for i in bdt["included"]]


def test_unmatched_transfer_then_matched(setup):
    pos = position(setup)
    assert pos["transfer_suggestions"] == [{"out_txn_id": setup.txn["T004"], "in_txn_id": setup.txn["T005"],
                                            "amount_minor": 1_000_000, "currency": "BDT"}]
    r = link(setup, out_ref="T004")
    assert r.status_code == 201
    pos = position(setup)
    [tr] = pos["transfers"]
    assert (tr["status"], tr["missing_side"], tr["amount_minor"]) == ("unmatched", "incoming", 1_000_000)
    bank = acct(pos, "Operating bank account")
    assert bank["status"] == "needs_review" and any(i["code"] == "unmatched_transfer" for i in bank["issues"])
    # Fix it: void the one-sided declaration and link both sides.
    assert link(setup, "T004", "T005").status_code == 409  # T004 is still in the unmatched record
    assert setup.owner.post(f"{setup.base}/cash/transfers/{tr['id']}/void", json={"reason": "Found the wallet side"}).status_code == 200
    assert link(setup, "T004", "T005").status_code == 201
    pos = position(setup)
    [tr] = pos["transfers"]
    assert tr["status"] == "matched" and pos["transfer_suggestions"] == []
    assert acct(pos, "Operating bank account")["status"] == "verified"


def test_spendable_total_per_currency(setup):
    assert link(setup, "T004", "T005").status_code == 201
    [bdt] = position(setup)["totals"]
    assert bdt["currency"] == "BDT"
    assert (bdt["spendable_minor"], bdt["restricted_minor"]) == (20_525_010, 2_000_000)
    assert sorted(i["label"] for i in bdt["included"]) == ["Merchant mobile wallet", "Operating bank account"]


def test_residual_and_no_anchor_cases():
    biz = Biz().post_fixture()
    # Wrong closing balance -> a non-zero residual is reported exactly.
    anchor(biz, "ACC-BANK", "2026-10-01", 18_500_000)
    anchor(biz, "ACC-BANK", "2026-10-15", 16_200_000)
    coverage(biz, "ACC-BANK", "2026-10-02", "2026-10-15")
    pos = position(biz)
    [p] = acct(pos, "Operating bank account")["periods"]
    assert (p["residual_minor"], p["status"]) == (24_975, "residual")
    wallet = acct(pos, "Merchant mobile wallet")
    assert (wallet["status"], wallet["verified_balance_minor"], wallet["usable_minor"]) == ("missing", None, None)
    assert [m["label"] for m in pos["totals"][0]["missing"]] == ["Merchant mobile wallet", "Cash box"]


def test_uncovered_movements_after_the_anchor_are_not_verified():
    biz = Biz().post_fixture()
    anchor(biz, "ACC-BANK", "2026-10-01", 18_500_000)
    bank = acct(position(biz), "Operating bank account")
    # No coverage after 1 Oct: the four posted October movements are shown but not added to the verified balance.
    assert bank["verified_balance_minor"] == 18_500_000 and bank["evidence_through"] == "2026-10-01"
    assert bank["uncovered_movements"]["count"] == 4 and bank["uncovered_movements"]["sum_minor"] == -2_275_025
    assert bank["freshness"] == "stale"


def test_as_of_limits_what_is_counted(setup):
    bank = acct(position(setup, "2026-10-05"), "Operating bank account")
    # Only the 1 Oct anchor exists by 5 Oct; coverage up to 5 Oct is counted.
    assert bank["periods"] == [] and bank["evidence_through"] == "2026-10-05"
    assert bank["verified_balance_minor"] == 18_500_000 + 6_500_000 - 3_275_025
    assert setup.owner.get(f"{setup.base}/cash/position?as_of=2999-01-01").status_code == 422


# --- validation --------------------------------------------------------------------------------------


@pytest.mark.parametrize("change, status", [
    ({"anchor_date": "2999-01-01"}, 422),
    ({"anchor_date": "1999-12-31"}, 422),
    ({"currency": "USD"}, 422),
    ({"balance_minor": 100.5}, 422),
    ({"balance_minor": "100"}, 422),
    ({"balance_minor": 10**15 + 1}, 422),
    ({"source_kind": "rumour"}, 422),
    ({"source_reference": "  "}, 422),
    ({"extra": 1}, 422),
])
def test_invalid_anchors(change, status):
    biz = Biz()
    body = {"account_id": biz.account["ACC-BANK"], "anchor_date": "2026-10-01", "balance_minor": 1, "currency": "BDT",
            "source_kind": "bank_statement", "source_reference": "S1", **change}
    assert biz.owner.post(f"{biz.base}/cash/anchors", json=body).status_code == status


def test_one_anchor_per_day_and_void_to_replace():
    biz = Biz()
    first = anchor(biz, "ACC-BANK", "2026-10-01", 100)
    assert anchor(biz, "ACC-BANK", "2026-10-01", 200).status_code == 409
    aid = first.json()["created_id"]
    assert biz.owner.post(f"{biz.base}/cash/anchors/{aid}/void", json={"reason": "Typo"}).status_code == 200
    assert biz.owner.post(f"{biz.base}/cash/anchors/{aid}/void", json={"reason": "Again"}).status_code == 409
    assert anchor(biz, "ACC-BANK", "2026-10-01", 200).status_code == 201
    assert biz.owner.post(f"{biz.base}/cash/anchors/{aid}/void", json={"reason": ""}).status_code == 422


def test_invalid_coverage():
    biz = Biz().post_fixture()
    assert coverage(biz, "ACC-BANK", "2026-10-05", "2026-10-01").status_code == 422
    assert coverage(biz, "ACC-BANK", "2026-10-01", "2999-01-01").status_code == 422
    assert coverage(biz, "ACC-BANK", "2024-01-01", "2026-10-01").status_code == 422  # longer than a year
    assert coverage(biz, "ACC-BANK", "2026-10-01", "2026-10-02", source_kind="import_batch").status_code == 422
    # The bank import's rows run 2-10 Oct, so the period must include them, and it must be for the same account.
    r = coverage(biz, "ACC-BANK", "2026-10-03", "2026-10-15", source_kind="import_batch", import_batch_id=biz.batch["ACC-BANK"])
    assert r.status_code == 422 and "2026-10-02" in r.text
    r = coverage(biz, "ACC-WALLET", "2026-10-01", "2026-10-15", source_kind="import_batch", import_batch_id=biz.batch["ACC-BANK"])
    assert r.status_code == 422


def test_invalid_restrictions():
    biz = Biz()
    base = {"account_id": biz.account["ACC-BANK"], "amount_minor": 100, "currency": "BDT", "reason": "Lien", "effective_from": "2026-10-01"}
    for change in ({"amount_minor": 0}, {"amount_minor": -5}, {"currency": "USD"}, {"reason": ""},
                   {"effective_to": "2026-09-01"}, {"effective_from": "2999-01-01"}):
        assert biz.owner.post(f"{biz.base}/cash/restrictions", json={**base, **change}).status_code == 422, change


def test_invalid_transfer_links():
    biz = Biz().post_fixture()
    assert link(biz).status_code == 422                       # neither side
    assert link(biz, "T001").status_code == 422               # T001 is money in, not out
    assert link(biz, None, "T002").status_code == 422         # T002 is money out, not in
    assert link(biz, "T002", "T003").status_code == 422       # different amounts
    assert link(biz, "T002", "T001").status_code == 422       # same account
    assert link(biz, "T004", "T005").status_code == 201
    assert link(biz, "T004").status_code == 409               # T004 already used in a transfer
    assert link(biz, None, "T005").status_code == 409         # and so is T005


# --- permissions, isolation, immutability ----------------------------------------------------------------


def test_staff_can_read_but_not_manage():
    biz = Biz().post_fixture()
    staff = biz.member("staff")
    assert anchor(biz, "ACC-BANK", "2026-10-01", 1, client=staff).status_code == 403
    assert staff.post(f"{biz.base}/cash/transfers", json={"out_txn_id": biz.txn["T004"]}).status_code == 403
    assert position(biz, client=staff)["accounts"]
    assert anchor(biz, "ACC-BANK", "2026-10-01", 1, client=biz.member("bookkeeper")).status_code == 201


def test_other_business_cannot_read_or_write():
    biz, other = Biz().post_fixture(), Biz()
    a = anchor(biz, "ACC-BANK", "2026-10-01", 1).json()["created_id"]
    assert other.owner.get(f"{biz.base}/cash/position").status_code == 404
    # Own business path, foreign account / anchor / transaction ids.
    assert other.owner.post(f"{other.base}/cash/anchors", json={
        "account_id": biz.account["ACC-BANK"], "anchor_date": "2026-10-01", "balance_minor": 1, "currency": "BDT",
        "source_kind": "bank_statement", "source_reference": "x"}).status_code == 404
    assert other.owner.post(f"{other.base}/cash/anchors/{a}/void", json={"reason": "x"}).status_code == 404
    assert other.owner.post(f"{other.base}/cash/transfers", json={"out_txn_id": biz.txn["T004"]}).status_code == 404


@pytest.mark.parametrize("stmt", [
    "UPDATE balance_anchor SET balance_minor = balance_minor + 1",
    "DELETE FROM balance_anchor",
    "UPDATE coverage_period SET period_end = period_end + 1",
    "DELETE FROM own_transfer",
])
def test_entries_can_only_be_voided(stmt):
    biz = Biz().post_fixture()
    anchor(biz, "ACC-BANK", "2026-10-01", 1)
    coverage(biz, "ACC-BANK", "2026-10-02", "2026-10-03")
    link(biz, "T004", "T005")
    with pytest.raises(DBAPIError):
        with get_engine().begin() as conn:
            conn.execute(text(stmt + " WHERE business_id = :b"), {"b": biz.id})


def test_interval_helpers():
    d = D
    merged = merge_intervals([(d("2026-10-05"), d("2026-10-07")), (d("2026-10-01"), d("2026-10-03")), (d("2026-10-04"), d("2026-10-04"))])
    assert merged == [(d("2026-10-01"), d("2026-10-07"))]  # adjacent days merge
    assert find_gaps([(d("2026-10-02"), d("2026-10-03")), (d("2026-10-06"), d("2026-10-06"))], d("2026-10-01"), d("2026-10-07")) == [
        (d("2026-10-01"), d("2026-10-01")), (d("2026-10-04"), d("2026-10-05")), (d("2026-10-07"), d("2026-10-07"))]
    assert find_gaps([], d("2026-10-01"), d("2026-10-01")) == [(d("2026-10-01"), d("2026-10-01"))]
