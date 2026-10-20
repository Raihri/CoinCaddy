"""Financing Readiness: per-condition present/missing/uncertain with sources (pure engine), then the API and the database guards."""

import copy
import json
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import text

from app.db import get_engine
from app.modules.funding.router import CATALOGUE
from app.modules.readiness.engine import INPUT_FIELDS, ReadinessError, assess
from app.modules.records.service import business_today
from tests.helpers import Biz

AS_OF = "2026-10-20"          # window 23 Jul to 18 Oct (statements may lag two days)
PRODUCTS = {p["id"]: p for p in CATALOGUE["products"]}


def _doc(kind, ref="Scanned copy, office drive", issued=None, expires=None):
    return {"id": f"doc-{kind}-{expires}", "kind": kind, "reference": ref, "issued_on": issued, "expires_on": expires,
            "recorded_by": "Owner", "recorded_at": "2026-10-01T09:00:00+00:00"}


def _inputs(product="wc-line", coverage=(("2026-07-01", "2026-10-20"),), issues=(), classified=30_000_000, unclassified=(0, 0),
            earliest="2026-01-10", documents=None, wallet=None, receivables=(20_000_000, 2), **req):
    accounts = [{"id": "bank", "label": "Main bank", "kind": "bank", "currency": "BDT", "coverage": [{"start": s, "end": e} for s, e in coverage],
                 "issue_codes": list(issues), "has_movements_in_window": True},
                {"id": "cash", "label": "Cash box", "kind": "cash", "currency": "BDT", "coverage": [], "issue_codes": ["no_anchor"], "has_movements_in_window": False}]
    if wallet is not None:
        accounts.append(wallet)
    request = {"amount_minor": 4_000_000, "currency": "BDT", "needed_by": "2026-10-27", "purpose": "working_capital", "receiving_account_id": "bank",
               "months_trading": 18, "security_available": [], "documents_available": []} | req
    return {"as_of": AS_OF, "jurisdiction": "BD",
            "run": {"id": "run-1", "catalogue_version": CATALOGUE["version"], "catalogue_updated": CATALOGUE["updated"], "request": request},
            "product": PRODUCTS[product],
            "records": {"accounts": accounts,
                        "revenue": {"classified_receipts_minor": classified, "classified_count": 6 if classified else 0, "unclassified_inflow_minor": unclassified[1], "unclassified_inflow_count": unclassified[0]},
                        "earliest_record_date": earliest,
                        "receivables": {"eligible_minor": receivables[0], "count": receivables[1], "document_numbers": ["INV-1", "INV-2"][:receivables[1]]}},
            "documents": documents if documents is not None else [_doc("id")]}


def test_money_text_matches_the_app_format():
    from app.modules.readiness.engine import fmt
    assert [fmt(x, "BDT") for x in (5, 99_999, 10_000_000, 1_234_567_890, -12_345_600)] == ["৳ 0.05", "৳ 999.99", "৳ 1,00,000.00", "৳ 1,23,45,678.90", "-৳ 1,23,456.00"]
    assert fmt(10_000_000, "USD") == "USD 100,000.00" and fmt(1_234_567, "JPY") == "JPY 1,234,567"


def cond(res, code):
    return next(c for c in res["conditions"] if c["code"] == code)


def test_every_condition_present_is_ready_with_a_source_for_each():
    res = assess(_inputs())
    assert res["state"] == "ready" and res["counts"] == {"present": 10, "missing": 0, "uncertain": 0}
    assert [c["code"] for c in res["conditions"]] == ["jurisdiction", "currency", "amount", "purpose", "funding_time", "business_age", "revenue", "security",
                                                     "doc:bank_statements", "doc:id"]
    for c in res["conditions"]:
        assert c["status"] == "present" and c["source"] and c["verification"] and c["improvement"] is None
    assert "৳ 1,00,000.00 a month" in cond(res, "revenue")["finding"]          # 30,000,000 / 3 = 10,000,000 minor units
    assert "apply by 2026-10-24" in cond(res, "funding_time")["finding"]         # needed 27 Oct, 3 funding days
    assert "Main bank: declared statement coverage 2026-07-01 to 2026-10-20" in cond(res, "doc:bank_statements")["source"]
    assert "Scanned copy, office drive" in cond(res, "doc:id")["source"] and "lender will check the original" in cond(res, "doc:id")["source"]
    assert res["improvements"] == [] and res["application_submitted"] is False and res["data_shared"] is False


def test_missing_documents_cannot_be_reported_as_ready():
    res = assess(_inputs(documents=[]))                                     # no ID in the register, not declared
    c = cond(res, "doc:id")
    assert c["status"] == "missing" and res["state"] == "not_ready" and "record it in the document register" in c["improvement"]
    declared = assess(_inputs(documents=[], documents_available=["id", "bank_statements"]))   # a declaration alone is not evidence
    assert cond(declared, "doc:id")["status"] == "uncertain" and declared["state"] == "needs_verification"
    expired = assess(_inputs(documents=[_doc("id", expires="2026-09-30")]))
    assert cond(expired, "doc:id")["status"] == "missing" and "expired on 2026-09-30" in cond(expired, "doc:id")["finding"] and expired["state"] == "not_ready"
    no_statements = assess(_inputs(coverage=()))
    c = cond(no_statements, "doc:bank_statements")
    assert c["status"] == "missing" and "2026-07-23 to 2026-10-18" in c["finding"] and no_statements["state"] == "not_ready"
    assert "Import Main bank statements for 2026-07-23 to 2026-10-18" in c["improvement"]
    for res in (res, declared, expired, no_statements):
        assert res["state"] != "ready"


def test_uncertain_revenue_cannot_be_reported_as_ready():
    unclassified = assess(_inputs(unclassified=(2, 500_000)))                 # meets the minimum, but 2 inflows are not classified
    c = cond(unclassified, "revenue")
    assert c["status"] == "uncertain" and "2 inflow(s) totalling ৳ 5,000.00 are not classified" in c["finding"]
    assert unclassified["state"] == "needs_verification" and cond(unclassified, "doc:bank_statements")["status"] == "present"
    assert "Classify the 2 unclassified inflow(s)" in c["improvement"]

    gap = assess(_inputs(coverage=(("2026-07-01", "2026-07-31"), ("2026-08-11", "2026-10-20"))))  # 1-10 Aug not covered
    assert cond(gap, "revenue")["status"] == "uncertain" and "Main bank (2026-08-01 to 2026-08-10)" in cond(gap, "revenue")["finding"]
    assert cond(gap, "doc:bank_statements")["status"] == "missing" and gap["state"] == "not_ready"

    could_meet = assess(_inputs(classified=12_000_000, unclassified=(3, 6_000_000)))   # 4,000,000 a month; 6,000,000 if the inflows are sales
    assert cond(could_meet, "revenue")["status"] == "uncertain" and could_meet["state"] != "ready"
    below = assess(_inputs(classified=12_000_000))                             # complete records, 4,000,000 < 5,000,000 a month
    c = cond(below, "revenue")
    assert c["status"] == "missing" and "৳ 10,000.00 a month below the minimum" in c["finding"] and "rise by ৳ 10,000.00 a month" in c["improvement"]
    nothing = assess(_inputs(classified=0))
    assert cond(nothing, "revenue")["status"] == "missing" and "No operating receipts" in cond(nothing, "revenue")["finding"]


def test_business_age_needs_records_or_a_licence_not_just_a_declaration():
    tl_docs = [_doc("id"), _doc("trade_licence", expires="2027-06-30")]
    p = assess(_inputs("term-loan", earliest="2026-07-15", months_trading=18, documents=tl_docs))  # 3 months of records; 18 declared; needs 12
    c = cond(p, "business_age")
    assert c["status"] == "uncertain" and "18 months declared, but records show only 3 months" in c["finding"]
    lic = assess(_inputs("term-loan", earliest="2026-07-15", documents=[_doc("id"), _doc("trade_licence", issued="2024-03-01", expires="2027-06-30")]))
    assert cond(lic, "business_age")["status"] == "present" and "trade licence issued 2024-03-01 (31 months" in cond(lic, "business_age")["source"]
    short = assess(_inputs("term-loan", earliest="2026-07-15", months_trading=4, documents=tl_docs))
    assert cond(short, "business_age")["status"] == "missing"


def test_security_needs_a_registered_document_or_enough_invoices():
    base = [_doc("id"), _doc("trade_licence", issued="2024-03-01", expires="2027-06-30")]
    declared = assess(_inputs("term-loan", documents=base, security_available=["guarantor"]))
    assert cond(declared, "security")["status"] == "uncertain" and cond(declared, "security")["label"] == "Guarantor letter"
    letter = assess(_inputs("term-loan", documents=base + [_doc("guarantor_letter", ref="Signed letter, safe")], security_available=["guarantor"]))
    assert cond(letter, "security")["status"] == "present"
    adv = assess(_inputs("invoice-advance", documents=[_doc("id")], receivables=(4_000_000, 2)))   # 80% of 4,000,000 = 3,200,000 < 4,000,000
    c = cond(adv, "security")
    assert c["status"] == "missing" and "Reduce the amount to ৳ 32,000.00 or less" in c["improvement"] and "INV-1, INV-2" in c["source"]
    assert cond(assess(_inputs("invoice-advance", documents=[_doc("id")])), "security")["status"] == "present"
    assert cond(assess(_inputs("invoice-advance", documents=[_doc("id")], receivables=(0, 0))), "doc:invoices")["status"] == "missing"


def test_wallet_history_and_reconciliation():
    no_wallet = assess(_inputs("wallet-microloan", amount_minor=2_000_000))
    assert cond(no_wallet, "doc:wallet_history")["status"] == "missing" and "no mobile wallet account" in cond(no_wallet, "doc:wallet_history")["finding"]
    wallet = {"id": "w", "label": "bKash", "kind": "mobile_wallet", "currency": "BDT", "coverage": [{"start": "2026-07-01", "end": "2026-10-19"}],
              "issue_codes": ["residual"], "has_movements_in_window": True}
    res = assess(_inputs("wallet-microloan", amount_minor=2_000_000, wallet=wallet))
    assert cond(res, "doc:wallet_history")["status"] == "uncertain" and "do not fully reconcile" in cond(res, "doc:wallet_history")["finding"]
    assert cond(assess(_inputs(issues=("residual",))), "doc:bank_statements")["status"] == "uncertain"


@pytest.mark.parametrize("expires, status, fragment", [
    ("2026-10-25", "missing", "before the funds are needed"),
    ("2026-11-15", "uncertain", "within 30 days after"),
    (None, "uncertain", "without an expiry date"),
    ("2027-06-30", "present", "Recorded and valid"),
])
def test_document_expiry_rules(expires, status, fragment):
    docs = [_doc("id"), _doc("trade_licence", issued="2024-03-01", expires=expires)]
    c = cond(assess(_inputs("term-loan", documents=docs + [_doc("guarantor_letter")])), "doc:trade_licence")
    assert c["status"] == status and fragment in c["finding"]


def test_product_conditions_and_concrete_improvements_in_order():
    res = assess(_inputs("ke-trade-line"))
    assert [cond(res, k)["status"] for k in ("jurisdiction", "currency", "amount")] == ["missing", "missing", "missing"]
    late = assess(_inputs("secured-bank-loan", amount_minor=30_000_000, needed_by="2026-10-25", purpose="equipment"))
    t = cond(late, "funding_time")
    assert t["status"] == "missing" and "sooner than the typical 21" in t["finding"] and "at least 21 days ahead" in t["improvement"]
    passed = assess(_inputs(needed_by="2026-10-19"))
    assert cond(passed, "funding_time")["status"] == "missing" and "has passed" in cond(passed, "funding_time")["finding"]
    statuses = [i["status"] for i in late["improvements"]]
    assert statuses == sorted(statuses, key=lambda s: s != "missing") and all(i["step"] for i in late["improvements"])
    assert len(late["improvements"]) == late["counts"]["missing"] + late["counts"]["uncertain"]


def test_no_probability_score_rank_or_personal_traits_and_deterministic():
    inputs = _inputs()
    before = copy.deepcopy(inputs)
    res = assess(inputs)
    assert inputs == before and assess(inputs) == res
    blob = json.dumps({k: v for k, v in res.items() if k != "notice"}).lower()   # the notice itself says it is not a probability or score
    for word in ("probability", "likelihood", "score", "rank", "chance", "approved"):
        assert word not in blob
    assert "not an approval, a probability, or a score" in res["notice"] and "separate from the match ranking" in res["notice"]
    assert set(INPUT_FIELDS) == {"as_of", "jurisdiction", "run", "product", "records", "documents"}
    with pytest.raises(ReadinessError):
        assess(inputs | {"owner_gender": "x"})          # no personal trait can be passed in


# --- API -----------------------------------------------------------------------------------------------


def _setup():
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    biz.day = lambda n: (biz.today - timedelta(days=n)).isoformat()
    bank = biz.account["ACC-BANK"]
    biz.post_rows("ACC-BANK", [(biz.day(60), "Sales A", 10_000_000, "S1"), (biz.day(40), "Sales B", 10_000_000, "S2"), (biz.day(20), "Sales C", 10_000_000, "S3")])
    with get_engine().connect() as conn:
        ids = [str(r.id) for r in conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b"), {"b": biz.id})]
    assert biz.owner.post(f"{biz.base}/classifications", json={"items": [{"txn_id": i, "kind": "operating_receipt", "category": "sales"} for i in ids]}).status_code == 200
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": bank, "anchor_date": biz.today.isoformat(), "balance_minor": 10_000_000, "currency": "BDT",
                                                            "source_kind": "bank_statement", "source_reference": "Today"}).status_code == 201
    r = biz.owner.post(f"{biz.base}/funding-matches", json={
        "amount_minor": 4_000_000, "currency": "BDT", "needed_by": (biz.today + timedelta(days=7)).isoformat(), "purpose": "working_capital",
        "receiving_account_id": bank, "months_trading": 18, "security_available": [], "documents_available": ["bank_statements", "id"]})
    assert r.status_code == 201, r.text
    biz.run = r.json()
    assert "wc-line" in [p["id"] for p in biz.run["results"]["eligible"]]
    return biz


def _check(biz, client=None, product="wc-line"):
    return (client or biz.owner).post(f"{biz.base}/financing-readiness", json={"run_id": biz.run["id"], "product_id": product})


def test_readiness_through_the_api_from_not_ready_to_ready_without_touching_the_match():
    biz = _setup()
    first = _check(biz)
    assert first.status_code == 201, first.text
    res = first.json()["results"]
    st = {c["code"]: c["status"] for c in res["conditions"]}
    # Eligible in Funding Matches (owner declared both documents), yet NOT ready: readiness is separate from matching.
    assert first.json()["state"] == "not_ready" and st["doc:bank_statements"] == "missing" and st["doc:id"] == "uncertain"
    assert st["revenue"] == "uncertain"            # no statement coverage, so the 100,000.00 a month is not confirmed
    assert st["business_age"] == "uncertain"       # records start 60 days ago; 18 months is only declared

    assert biz.owner.post(f"{biz.base}/cash/coverage", json={"account_id": biz.account["ACC-BANK"], "period_start": biz.day(100), "period_end": biz.today.isoformat(),
                                                             "source_kind": "statement", "note": "Bank statements Jul-Oct"}).status_code == 201
    assert biz.owner.post(f"{biz.base}/business-documents", json={"kind": "id", "reference": "Passport copy, office drive"}).status_code == 201
    second = _check(biz).json()
    st = {c["code"]: c["status"] for c in second["results"]["conditions"]}
    assert st["doc:bank_statements"] == "present" and st["revenue"] == "present" and st["doc:id"] == "present"
    assert second["state"] == "needs_verification" and [i["code"] for i in second["results"]["improvements"]] == ["business_age"]

    tl = biz.owner.post(f"{biz.base}/business-documents", json={"kind": "trade_licence", "reference": "Framed, front office", "issued_on": "2024-01-15",
                                                                "expires_on": (biz.today + timedelta(days=200)).isoformat()})
    assert tl.status_code == 201, tl.text
    third = _check(biz).json()
    assert third["state"] == "ready" and third["results"]["counts"]["missing"] == third["results"]["counts"]["uncertain"] == 0

    # Voiding the licence makes it uncertain again; the older saved check is unchanged.
    assert biz.owner.post(f"{biz.base}/business-documents/{tl.json()['id']}/void", json={"reason": "Wrong year"}).status_code == 200
    assert _check(biz).json()["state"] == "needs_verification"
    assert biz.owner.get(f"{biz.base}/financing-readiness/{third['id']}").json()["state"] == "ready"
    v = biz.owner.get(f"{biz.base}/financing-readiness/{third['id']}/verify").json()
    assert v["inputs_hash_matches"] and v["results_reproduce"]
    listed = biz.owner.get(f"{biz.base}/financing-readiness?run_id={biz.run['id']}").json()
    assert len(listed) == 4 and listed[0]["product_name"] == "Working capital line" and "results" not in listed[0]
    assert biz.owner.get(f"{biz.base}/funding-matches/{biz.run['id']}").json()["results"] == biz.run["results"]   # the match is untouched
    excluded = _check(biz, product="secured-bank-loan").json()       # an excluded product can still be checked
    assert excluded["state"] == "not_ready" and excluded["results"]["product"]["name"] == "Secured bank loan"


def test_document_register_validation_and_minimal_data():
    biz = _setup()
    add = lambda **kw: biz.owner.post(f"{biz.base}/business-documents", json={"kind": "id", "reference": "Copy in office drive"} | kw)
    assert add(reference="NID 1990 1234 5678").status_code == 422          # an ID number is refused
    assert add(reference="12-34-56-78").status_code == 422
    assert add(reference="=HYPERLINK(\"x\")").status_code == 422 and add(reference="  ").status_code == 422
    assert add(kind="passport_number").status_code == 422 and add(owner_gender="x").status_code == 422
    assert add(issued_on="2025-01-01", expires_on="2024-12-31").status_code == 422
    assert add(issued_on=(biz.today + timedelta(days=1)).isoformat()).status_code == 422
    ok = add(reference="Licence no. 2024/77 copy, drive")
    assert ok.status_code == 201 and ok.json()["voided"] is False
    void = lambda: biz.owner.post(f"{biz.base}/business-documents/{ok.json()['id']}/void", json={"reason": "Duplicate"})
    assert void().status_code == 200 and void().status_code == 409
    assert [d["voided"] for d in biz.owner.get(f"{biz.base}/business-documents").json()] == [True]
    with get_engine().connect() as conn:
        for sql in ("UPDATE business_document SET reference = 'x' WHERE business_id = :b", "DELETE FROM business_document WHERE business_id = :b"):
            with pytest.raises(Exception):
                conn.execute(text(sql), {"b": biz.id})
            conn.rollback()
        with pytest.raises(Exception, match="business_document_reference_check|check"):
            conn.execute(text("INSERT INTO business_document (business_id, kind, reference, created_by) SELECT :b, 'id', 'NID 19901234567', created_by "
                              "FROM funding_match_run WHERE business_id = :b LIMIT 1"), {"b": biz.id})


def test_permissions_isolation_audit_constraints_and_no_application_route():
    biz, other = _setup(), _setup()
    staff, keeper = biz.member("staff"), biz.member("bookkeeper")
    assert _check(biz, staff).status_code == 403 and staff.get(f"{biz.base}/financing-readiness").status_code == 403
    assert staff.get(f"{biz.base}/business-documents").status_code == 403
    assert keeper.post(f"{biz.base}/business-documents", json={"kind": "id", "reference": "Copy"}).status_code == 403   # only the owner records documents
    assert keeper.get(f"{biz.base}/business-documents").status_code == 200
    cid = _check(biz, keeper).json()["id"]
    assert biz.owner.post(f"{biz.base}/financing-readiness", json={"run_id": other.run["id"], "product_id": "wc-line"}).status_code == 404
    assert _check(biz, product="no-such-product").status_code == 422
    assert biz.owner.post(f"{biz.base}/financing-readiness", json={"run_id": biz.run["id"], "product_id": "wc-line", "submit": True}).status_code == 422
    for url in (f"{biz.base}/financing-readiness", f"{biz.base}/financing-readiness/{cid}", f"{biz.base}/financing-readiness/{cid}/verify", f"{biz.base}/business-documents"):
        assert other.owner.get(url).status_code == 404
    doc = biz.owner.post(f"{biz.base}/business-documents", json={"kind": "id", "reference": "Copy"}).json()
    assert other.owner.post(f"{other.base}/business-documents/{doc['id']}/void", json={"reason": "x"}).status_code == 404
    events = biz.owner.get(f"{biz.base}/audit-events?limit=100").json()["events"]
    assert any(e["action"] == "readiness.run" and e["outcome"] == "success" and e["details"]["state"] == "not_ready" for e in events)
    assert any(e["action"] == "readiness.run" and e["outcome"] == "denied" for e in events)
    assert any(e["action"] == "documents.write" and e["outcome"] == "denied" for e in events)
    assert any(e["action"] == "documents.write" and e["outcome"] == "success" and e["details"]["kind"] == "id" and "reference" not in e["details"] for e in events)
    from app.main import app
    routes = {(m, r.path) for r in app.routes if any(k in getattr(r, "path", "") for k in ("financing-readiness", "business-documents")) for m in getattr(r, "methods", ())}
    assert {m for m, _ in routes} == {"GET", "POST"}
    assert not any(p.rsplit("/", 1)[-1] in {"apply", "submit", "share", "approve", "send"} for _, p in routes)
    with get_engine().connect() as conn:
        with pytest.raises(Exception, match="append-only"):
            conn.execute(text("UPDATE readiness_check SET state = 'ready' WHERE business_id = :b"), {"b": biz.id})
        conn.rollback()
        # The database itself refuses a 'ready' check that has a missing or uncertain condition.
        for counts in ((5, 1, 0), (5, 0, 1)):
            with pytest.raises(Exception, match="check"):
                conn.execute(text(
                    """INSERT INTO readiness_check (business_id, run_id, product_id, calc_version, as_of, inputs, inputs_sha256, results, state,
                                                    present_count, missing_count, uncertain_count, created_by)
                       SELECT business_id, id, 'wc-line', 'readiness-v1', as_of, '{}'::jsonb, repeat('0', 64), '{}'::jsonb, 'ready', :p, :m, :u, created_by
                       FROM funding_match_run WHERE business_id = :b LIMIT 1"""), {"b": biz.id, "p": counts[0], "m": counts[1], "u": counts[2]})
            conn.rollback()
    assert uuid.UUID(cid)
