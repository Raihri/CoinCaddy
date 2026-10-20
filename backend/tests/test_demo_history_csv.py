"""The demo history file (fixtures/imports/northstar_bank_history_jul_sep.csv) imported through the real API makes Funding Matches eligible.

The file's dates are fixed (Jul-Sep 2026). The test shifts them by the same number of days so they sit in the same place relative
to today, so the 90-day revenue window keeps working however old the file gets.
"""

import csv
import io
import uuid
from datetime import date, timedelta

from sqlalchemy import text

from app.config import REPO_ROOT
from app.db import get_engine
from app.modules.records.service import business_today
from tests.helpers import Biz

FILE = REPO_ROOT / "fixtures" / "imports" / "northstar_bank_history_jul_sep.csv"
FILE_REFERENCE_DAY = date(2026, 10, 20)   # the day the file was written: its newest row is 23 days before this
DOCS = ["bank_statements", "id", "trade_licence", "invoices", "wallet_history"]


def shifted_csv(today: date) -> tuple[bytes, list[dict]]:
    rows = list(csv.DictReader(io.StringIO(FILE.read_text(encoding="utf-8"))))
    shift = today - FILE_REFERENCE_DAY
    for r in rows:
        r["Date"] = (date.fromisoformat(r["Date"]) + shift).isoformat()
    out = io.StringIO()
    w = csv.DictWriter(out, fieldnames=["Date", "Description", "Reference", "Debit", "Credit"], lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    return out.getvalue().encode(), rows


def import_file(biz: Biz, data: bytes) -> dict:
    r = biz.owner.post(f"{biz.base}/imports", data={"account_id": biz.account["ACC-BANK"]}, files={"file": (f"hist-{uuid.uuid4().hex[:6]}.csv", data, "text/csv")})
    assert r.status_code == 201, r.text
    b = r.json()
    b = biz.owner.put(f"{biz.base}/imports/{b['id']}/mapping", json={"expected_revision": b["revision"], "mapping": {
        "date_column": 0, "date_format": "YYYY-MM-DD", "description_column": 1, "reference_column": 2,
        "amount_mode": "debit_credit", "debit_column": 3, "credit_column": 4}}).json()
    assert b["counts"]["valid"] == 13 and b["counts"]["pending"] == 0, b["counts"]          # every row parses; none rejected or duplicated
    r = biz.owner.post(f"{biz.base}/imports/{b['id']}/commit", json={"expected_revision": b["revision"], "idempotency_key": str(uuid.uuid4())})
    assert r.status_code == 200, r.text
    return b


def match(biz: Biz, **kw):
    body = {"amount_minor": 2_000_000, "currency": "BDT", "needed_by": (biz.today + timedelta(days=10)).isoformat(), "purpose": "working_capital",
            "receiving_account_id": biz.account["ACC-BANK"], "months_trading": 18, "security_available": ["guarantor"], "documents_available": DOCS} | kw
    r = biz.owner.post(f"{biz.base}/funding-matches", json=body)
    assert r.status_code == 201, r.text
    return r.json()["results"]


def test_demo_history_file_makes_funding_matches_eligible():
    biz = Biz()
    biz.today = business_today("Asia/Dhaka")
    data, rows = shifted_csv(biz.today)
    assert len(rows) == 13 and sum(float(r["Credit"].replace(",", "")) for r in rows if r["Credit"]) == 369_000.0
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": biz.account["ACC-BANK"], "anchor_date": biz.today.isoformat(), "balance_minor": 5_000_000,
                                                            "currency": "BDT", "source_kind": "bank_statement", "source_reference": "Today"}).status_code == 201
    import_file(biz, data)

    # Imported but not yet classified: no revenue is visible, so nothing is eligible and the reason says why.
    before = match(biz)
    assert before["eligible"] == []
    assert "records show 0 over the last 90 days" in next(p for p in before["excluded"] if p["id"] == "wc-line")["reasons"][0]

    with get_engine().connect() as conn:
        receipts = conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b AND amount_minor > 0"), {"b": biz.id}).all()
    assert len(receipts) == 6
    r = biz.owner.post(f"{biz.base}/classifications", json={"items": [{"txn_id": str(x.id), "kind": "operating_receipt", "category": "sales"} for x in receipts]})
    assert r.status_code == 200, r.text

    after = match(biz)
    # 369,000.00 BDT of receipts in the last 90 days = 36,900,000 minor units; 12,300,000 a month.
    run = biz.owner.get(f"{biz.base}/funding-matches").json()[0]
    with get_engine().connect() as conn:
        inputs_revenue = conn.execute(text("SELECT inputs->>'revenue_90d_minor' FROM funding_match_run WHERE id = :i"), {"i": run["id"]}).scalar_one()
    assert inputs_revenue == "36900000"
    assert sorted(p["id"] for p in after["eligible"]) == ["term-loan", "wallet-microloan", "wc-line"]
    ex = {p["id"]: p["reasons"] for p in after["excluded"]}
    assert set(ex) == {"invoice-advance", "secured-bank-loan", "ke-trade-line"}
    assert any("outstanding, undisputed invoices" in x for x in ex["invoice-advance"])         # no invoices in this history: honest exclusion
    assert not any("monthly operating receipts" in x for x in ex["invoice-advance"])             # revenue itself is no longer the problem
    assert all(p["label"].startswith("Illustrative") for p in after["eligible"])
