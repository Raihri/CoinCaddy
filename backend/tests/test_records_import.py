"""Records Import tests (separate test database and storage; see conftest.py)."""

import io
import json
import threading
import uuid
import zipfile
from datetime import date

import openpyxl
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.config import REPO_ROOT, get_settings
from app.db import get_engine
from app.fixtures.seed import seed
from app.main import app
from app.modules.identity.passwords import hash_password
from app.modules.records import storage
from app.modules.records.files import MAX_FILE_BYTES, MAX_ROWS
from app.modules.records.normalize import Mapping, normalize_row

PASSWORD = get_settings().demo_user_password
PART1 = (REPO_ROOT / "fixtures" / "imports" / "northstar_bank_oct_part1.csv").read_bytes()
PART2 = (REPO_ROOT / "fixtures" / "imports" / "northstar_bank_oct_part2.csv").read_bytes()
DDMM = {"date_column": 0, "date_format": "DD/MM/YYYY", "description_column": 1, "reference_column": 2,
        "amount_mode": "debit_credit", "debit_column": 3, "credit_column": 4}
BUSINESS = {"name": "Import Test Co", "jurisdiction": "BD", "base_currency": "BDT", "timezone": "Asia/Dhaka",
            "reserve": {"amount_minor": 0, "currency": "BDT"},
            "accounts": [{"label": "Bank", "kind": "bank", "currency": "BDT"},
                         {"label": "Wallet", "kind": "mobile_wallet", "currency": "BDT"}]}


@pytest.fixture(autouse=True)
def _seed():
    seed()


def make_user() -> tuple[str, TestClient]:
    email = f"imp-{uuid.uuid4().hex[:10]}@test.example"
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO app_user (email, display_name, password_hash) VALUES (:e, :n, :h)"),
                     {"e": email, "n": f"User {email[:8]}", "h": hash_password(PASSWORD)})
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return email, client


def add_member(business_id: str, email: str, role: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO membership (business_id, user_id, role) SELECT :b, id, :r FROM app_user WHERE email = :e"),
                     {"b": business_id, "e": email, "r": role})


class Biz:
    def __init__(self):
        _, self.owner = make_user()
        body = self.owner.post("/api/businesses", json=BUSINESS).json()
        self.id = body["id"]
        self.bank = body["accounts"][0]["id"]
        self.wallet = body["accounts"][1]["id"]
        self.base = f"/api/businesses/{self.id}"

    def member(self, role: str) -> TestClient:
        email, client = make_user()
        add_member(self.id, email, role)
        return client

    def upload(self, data: bytes, name: str = "statement.csv", client: TestClient | None = None, account: str | None = None):
        return (client or self.owner).post(f"{self.base}/imports", data={"account_id": account or self.bank},
                                           files={"file": (name, data, "application/octet-stream")})

    def map(self, batch: dict, mapping: dict = DDMM, client: TestClient | None = None):
        return (client or self.owner).put(f"{self.base}/imports/{batch['id']}/mapping",
                                          json={"expected_revision": batch["revision"], "mapping": mapping})

    def decide(self, batch: dict, row: int, decision: str, client: TestClient | None = None):
        return (client or self.owner).patch(f"{self.base}/imports/{batch['id']}/rows/{row}",
                                            json={"expected_revision": batch["revision"], "decision": decision})

    def commit(self, batch: dict, key: str | None = None, client: TestClient | None = None):
        return (client or self.owner).post(f"{self.base}/imports/{batch['id']}/commit",
                                           json={"expected_revision": batch["revision"], "idempotency_key": key or str(uuid.uuid4())})

    def rows(self, batch: dict, view: str = "all") -> list[dict]:
        return self.owner.get(f"{self.base}/imports/{batch['id']}/rows?view={view}&limit=500").json()["rows"]

    def ledger(self) -> list[dict]:
        return self.owner.get(f"{self.base}/ledger?limit=200").json()["transactions"]

    def staged_part1(self) -> dict:
        b = self.upload(PART1).json()
        return self.map(b).json()


@pytest.fixture
def biz() -> Biz:
    return Biz()


def audit(business_id: str, action: str) -> list[dict]:
    with get_engine().connect() as conn:
        return [dict(r) for r in conn.execute(text("SELECT * FROM audit_event WHERE business_id = :b AND action = :a ORDER BY seq"),
                                              {"b": business_id, "a": action}).mappings()]


def xlsx(rows: list[list], *, extra_sheet: list[list] | None = None) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    if extra_sheet:
        ws2 = wb.create_sheet("Other")
        for r in extra_sheet:
            ws2.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def with_zip_entry(data: bytes, name: str, payload: bytes, compress: int = zipfile.ZIP_DEFLATED) -> bytes:
    src = zipfile.ZipFile(io.BytesIO(data))
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in src.infolist():
            z.writestr(item, src.read(item.filename))
        z.writestr(zipfile.ZipInfo(name), payload, compress_type=compress)
    return out.getvalue()


# --- whole-file rejections ------------------------------------------------------------------------------


GOOD_XLSX = xlsx([["Date", "Description", "Amount"], [date(2026, 10, 2), "Receipt", 650.5]])


@pytest.mark.parametrize("name, data, code", [
    ("statement.csv", b"", "empty_file"),
    ("statement.txt", b"a,b\n1,2\n", "unsupported_type"),
    ("statement.xls", b"a,b\n1,2\n", "unsupported_type"),
    ("statement.csv", GOOD_XLSX, "type_mismatch"),
    ("statement.xlsx", b"Date,Description,Amount\n2026-10-02,x,1\n", "type_mismatch"),
    ("statement.xlsx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 600, "encrypted_or_legacy"),
    ("statement.csv", b"Date,Amount\n2026-10-02,\x00\x01\n", "binary_content"),
    ("statement.csv", "Date,Description\n02/10/2026,Café\n".encode("latin-1"), "encoding"),
    ("statement.csv", b"a,b;c\n1,2;3\n4,5;6\n", "ambiguous_delimiter"),
    ("statement.csv", b"just one column\nvalue\n", "no_columns"),
    ("statement.csv", b"Date,date\n1,2\n", "bad_headers"),
    ("statement.csv", b"Date,,Amount\n1,2,3\n", "bad_headers"),
    ("statement.csv", b"=cmd,Amount\n1,2\n", "formulas"),
    ("statement.csv", b"Date,Amount\n", "no_data"),
    ("statement.csv", b'Date,Amount\n2026-10-02,1\n2026-10-03,"2\n', "malformed_csv"),
    ("statement.csv", ",".join(f"c{i}" for i in range(31)).encode() + b"\n" + b",".join(b"1" for _ in range(31)) + b"\n", "too_many_columns"),
    ("statement.csv", b"Date,Amount\n" + b"2026-10-02,1\n" * (MAX_ROWS + 1), "too_many_rows"),
    ("statement.xlsx", xlsx([["Date", "Amount"], ["2026-10-02", "=1+1"]]), "formulas"),
    ("statement.xlsx", with_zip_entry(GOOD_XLSX, "xl/vbaProject.bin", b"macro"), "macros"),
    ("statement.xlsx", xlsx([["Date", "Amount"], ["2026-10-02", 1]], extra_sheet=[["x", "y"], [1, 2]]), "ambiguous_sheets"),
    ("statement.xlsx", with_zip_entry(GOOD_XLSX, "xl/media/bomb.bin", b"\x00" * (20 * 1024 * 1024)), "suspicious_compression"),
    ("statement.xlsx", b"PK\x03\x04" + b"not really a zip" * 10, "corrupt_workbook"),
], ids=lambda v: v if isinstance(v, str) else "data")
def test_unsafe_or_ambiguous_files_are_rejected(biz, name, data, code):
    r = biz.upload(data, name)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["code"] == code
    assert biz.owner.get(f"{biz.base}/imports").json() == []
    [event] = audit(biz.id, "import.upload")
    assert event["outcome"] == "failed" and event["details"]["reason"] == code


def test_oversized_file_is_refused_with_413(biz):
    r = biz.upload(b"Date,Amount\n" + b"x" * MAX_FILE_BYTES)  # passes the body cap, fails the file cap
    assert r.status_code == 413 and r.json()["detail"]["code"] == "too_large"
    r = biz.upload(b"x" * (MAX_FILE_BYTES + 200 * 1024))  # stopped by the body-size middleware before parsing
    assert r.status_code == 413
    assert biz.owner.get(f"{biz.base}/imports").json() == []


def test_upload_requires_an_account_of_this_business(biz):
    other = Biz()
    assert biz.upload(PART1, account=other.bank).status_code == 404
    assert biz.upload(PART1, account=str(uuid.uuid4())).status_code == 404


# --- staging, mapping, preview ------------------------------------------------------------------------


def test_upload_stages_rows_privately_and_suggests_a_mapping(biz):
    r = biz.upload(PART1, "../../etc/Oct statement.csv")
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["status"] == "needs_mapping" and b["row_count"] == 7 and b["source_name"] == "Oct statement.csv"
    s = b["suggested_mapping"]
    assert (s["date_column"], s["description_column"], s["reference_column"], s["amount_mode"],
            s["debit_column"], s["credit_column"]) == (0, 1, 2, "debit_credit", 3, 4)
    # Every date in the file is valid as both DD/MM and MM/DD, so no format is guessed.
    assert s["date_format"] is None and s["date_format_ambiguous"] is True
    assert set(b["column_profile"][0]["date_formats"]) == {"DD/MM/YYYY", "MM/DD/YYYY"}
    assert "storage_key" not in b and "storage" not in json.dumps(b)
    # The original is kept privately, byte for byte, under a name derived from the batch, not the user.
    with get_engine().connect() as conn:
        key = conn.execute(text("SELECT storage_key FROM import_batch WHERE id = :i"), {"i": b["id"]}).scalar_one()
    assert key == f"{biz.id}/{b['id']}.csv"
    assert (storage.root() / key).read_bytes() == PART1
    # Nothing staged is in the ledger.
    assert biz.ledger() == []


def test_valid_import_preview_with_rejected_rows_and_duplicate_candidate(biz):
    b = biz.staged_part1()
    assert b["status"] == "staged"
    assert b["counts"] == {"total": 7, "valid": 5, "error": 2, "duplicates": 1, "pending": 1, "included": 4, "excluded": 0}
    rows = {r["row_number"]: r for r in biz.rows(b)}
    assert [rows[n]["amount_minor"] for n in (2, 3, 5, 6)] == [6_500_000, -3_275_025, -1_000_000, -4_500_000]
    assert rows[2]["business_date"] == "2026-10-02" and rows[2]["reference"] == "BRF-1001"
    assert rows[4]["duplicate"] == {"kind": "in_file", "row_number": 3, "transaction": None}
    assert rows[4]["decision"] == "pending"
    assert rows[7]["status"] == "error" and "formula" in rows[7]["errors"][0]["message"]
    assert rows[8]["status"] == "error" and rows[8]["errors"][0]["field"] == "date"
    # Staged rows are never official cash.
    summary = biz.owner.get(f"{biz.base}/records/summary").json()
    bank = next(a for a in summary["accounts"] if a["id"] == biz.bank)
    assert bank["posted_transactions"] == 0 and biz.ledger() == []
    assert "not official" in summary["notice"]


def test_choosing_the_other_date_format_changes_results(biz):
    b = biz.upload(PART1).json()
    b = biz.map(b, {**DDMM, "date_format": "MM/DD/YYYY"}).json()
    rows = {r["row_number"]: r for r in biz.rows(b)}
    assert rows[2]["business_date"] == "2026-02-10"
    assert rows[7]["status"] == "error"
    assert any("future" in e["message"] for e in rows[7]["errors"] + rows[8]["errors"]) or rows[8]["status"] == "error"


@pytest.mark.parametrize("mapping", [
    {**DDMM, "date_format": None},
    {**DDMM, "date_format": "DD/MM/YY"},
    {**DDMM, "debit_column": 1},
    {**DDMM, "credit_column": 9},
    {**DDMM, "date_column": "0"},
    {**DDMM, "amount_mode": "signed"},
    {k: v for k, v in DDMM.items() if k != "description_column"},
    {**DDMM, "surprise": 1},
])
def test_invalid_mappings_are_rejected(biz, mapping):
    b = biz.upload(PART1).json()
    assert biz.map(b, mapping).status_code == 422
    assert biz.owner.get(f"{biz.base}/imports/{b['id']}").json()["status"] == "needs_mapping"


def test_stale_revision_is_refused_everywhere(biz):
    b = biz.upload(PART1).json()
    staged = biz.map(b).json()
    assert biz.map(b).status_code == 409  # b carries the old revision
    assert biz.decide(b, 4, "exclude").status_code == 409
    assert biz.commit(b).status_code == 409
    assert biz.commit(staged).status_code == 409  # still has a pending duplicate
    assert biz.ledger() == []


# --- review and commit --------------------------------------------------------------------------------


def test_commit_is_blocked_until_duplicates_are_reviewed_then_posts_exactly(biz):
    b = biz.staged_part1()
    r = biz.commit(b)
    assert r.status_code == 409 and r.json()["detail"]["pending_rows"] == [4]
    b = biz.decide(b, 4, "exclude").json()
    assert b["counts"]["pending"] == 0
    r = biz.commit(b)
    assert r.status_code == 200, r.text
    assert r.json()["commit"] == {"posted_count": 4, "replayed": False}
    ledger = biz.ledger()
    assert sorted(t["amount_minor"] for t in ledger) == [-4_500_000, -3_275_025, -1_000_000, 6_500_000]
    # Matches the hand-checked bank movements in fixtures/northstar_trading_expected.md.
    assert sum(t["amount_minor"] for t in ledger) == -2_275_025
    assert {t["source_row_number"] for t in ledger} == {2, 3, 5, 6}
    assert all(t["source_batch_id"] == b["id"] and t["parser_version"] == "import-v1" and t["currency"] == "BDT" for t in ledger)
    [event] = [e for e in audit(biz.id, "import.commit") if e["outcome"] == "success"]
    assert event["details"]["posted_count"] == 4
    assert "Customer receipt" not in json.dumps([e["details"] for e in audit(biz.id, "import.upload")])


def test_retry_with_same_key_does_not_double_post(biz):
    b = biz.decide(biz.staged_part1(), 4, "exclude").json()
    key = str(uuid.uuid4())
    first = biz.commit(b, key)
    second = biz.commit(b, key)  # e.g. the browser retried after a timeout
    assert first.status_code == second.status_code == 200
    assert second.json()["commit"] == {"posted_count": 4, "replayed": True}
    assert len(biz.ledger()) == 4
    assert biz.commit(b, str(uuid.uuid4())).status_code == 409  # a different commit on a posted batch
    assert len(biz.ledger()) == 4


def test_concurrent_commits_post_once(biz):
    b = biz.decide(biz.staged_part1(), 4, "exclude").json()
    key = str(uuid.uuid4())
    results = []

    def go():
        results.append(TestClient(app, cookies=biz.owner.cookies).post(
            f"{biz.base}/imports/{b['id']}/commit", json={"expected_revision": b["revision"], "idempotency_key": key}).status_code)

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [200] * 6
    assert len(biz.ledger()) == 4


def test_same_file_uploaded_again_returns_the_same_batch(biz):
    first = biz.upload(PART1).json()
    again = biz.upload(PART1, "renamed copy.csv")
    assert again.status_code == 200 and again.json()["duplicate_upload"] is True and again.json()["id"] == first["id"]
    assert len(biz.owner.get(f"{biz.base}/imports").json()) == 1
    # The same file for a different account is a separate batch.
    assert biz.upload(PART1, account=biz.wallet).status_code == 201


def test_reupload_after_posting_cannot_double_post(biz):
    b = biz.decide(biz.staged_part1(), 4, "exclude").json()
    biz.commit(b)
    again = biz.upload(PART1)
    assert again.status_code == 200 and again.json()["status"] == "posted"
    assert len(biz.ledger()) == 4


def test_overlapping_file_flags_ledger_duplicate_and_reviewer_decides(biz):
    biz.commit(biz.decide(biz.staged_part1(), 4, "exclude").json())
    b = biz.map(biz.upload(PART2).json()).json()
    rows = {r["row_number"]: r for r in biz.rows(b)}
    dup = rows[2]["duplicate"]
    assert dup["kind"] == "in_ledger" and dup["transaction"]["amount_minor"] == -4_500_000
    assert dup["transaction"]["reference"] == "BRF-1004" and rows[2]["decision"] == "pending"
    b = biz.decide(b, 2, "exclude").json()
    assert biz.commit(b).json()["commit"]["posted_count"] == 2
    assert len(biz.ledger()) == 6


def test_legitimate_identical_payment_can_be_kept_after_review(biz):
    b = biz.staged_part1()
    b = biz.decide(b, 4, "include").json()  # reviewer confirms two real, identical supplier payments
    assert biz.commit(b).json()["commit"]["posted_count"] == 5
    assert [t["amount_minor"] for t in biz.ledger()].count(-3_275_025) == 2


def test_commit_rechecks_the_ledger_for_duplicates_posted_meanwhile(biz):
    # Two different files staged for the same account, both containing the same three payments.
    a = biz.map(biz.upload(PART2).json()).json()
    b = biz.map(biz.upload(PART2.replace(b"Electricity bill", b"Electricity bill (copy)")).json()).json()
    assert b["counts"]["pending"] == 0  # nothing was in the ledger when b was previewed
    assert biz.commit(a).json()["commit"]["posted_count"] == 3
    r = biz.commit(b)
    # Rows 2 and 4 match by description; row 3 matches by its bank reference despite the changed text.
    assert r.status_code == 409 and r.json()["detail"]["pending_rows"] == [2, 3, 4]
    fresh = biz.owner.get(f"{biz.base}/imports/{b['id']}").json()
    assert fresh["counts"]["pending"] == 3 and len(biz.ledger()) == 3


def test_error_rows_cannot_be_included_and_nothing_selected_is_refused(biz):
    b = biz.staged_part1()
    assert biz.decide(b, 7, "include").status_code == 422
    for row in (2, 3, 4, 5, 6):
        b = biz.decide(b, row, "exclude").json()
    assert biz.commit(b).status_code == 422
    assert biz.ledger() == []


def test_discard_and_upload_again(biz):
    b = biz.staged_part1()
    r = biz.owner.post(f"{biz.base}/imports/{b['id']}/discard", json={"expected_revision": b["revision"]})
    assert r.json()["status"] == "discarded"
    assert biz.commit(r.json()).status_code == 409
    assert biz.upload(PART1).status_code == 201  # a discarded batch no longer blocks the same file


def test_posted_batch_cannot_be_discarded(biz):
    b = biz.decide(biz.staged_part1(), 4, "exclude").json()
    posted = biz.commit(b).json()
    r = biz.owner.post(f"{biz.base}/imports/{b['id']}/discard", json={"expected_revision": posted["revision"]})
    assert r.status_code == 409 and len(biz.ledger()) == 4


# --- xlsx --------------------------------------------------------------------------------------------


def test_xlsx_with_native_dates_and_numbers(biz):
    data = xlsx([["Date", "Description", "Amount"], [date(2026, 10, 2), "Receipt", 650.5],
                 [date(2026, 10, 3), "Fee", -12.25], ["2026-10-04", "Text date", "1,00,000"]])
    b = biz.upload(data, "book.xlsx").json()
    assert b["file_type"] == "xlsx"
    b = biz.map(b, {"date_column": 0, "date_format": "YYYY-MM-DD", "description_column": 1,
                    "amount_mode": "signed", "amount_column": 2}).json()
    assert b["counts"]["valid"] == 3
    assert [r["amount_minor"] for r in biz.rows(b)] == [65_050, -1_225, 10_000_000]
    assert biz.commit(b).json()["commit"]["posted_count"] == 3


# --- permissions and isolation ---------------------------------------------------------------------


def test_staff_can_stage_but_not_review_or_post(biz):
    staff = biz.member("staff")
    b = biz.upload(PART1, client=staff).json()
    b = biz.map(b, client=staff).json()
    assert biz.decide(b, 4, "exclude", client=staff).status_code == 403
    assert biz.commit(b, client=staff).status_code == 403
    assert staff.post(f"{biz.base}/imports/{b['id']}/discard", json={"expected_revision": b["revision"]}).status_code == 403
    assert biz.ledger() == []
    bookkeeper = biz.member("bookkeeper")
    b = biz.decide(b, 4, "exclude", client=bookkeeper).json()
    assert biz.commit(b, client=bookkeeper).status_code == 200


def test_another_business_cannot_see_or_touch_imports_or_ledger(biz):
    b = biz.decide(biz.staged_part1(), 4, "exclude").json()
    other = Biz()
    for path in (f"/imports/{b['id']}", f"/imports/{b['id']}/rows"):
        assert other.owner.get(biz.base + path).status_code == 404
        assert other.owner.get(other.base + path).status_code == 404
    assert other.owner.post(f"{other.base}/imports/{b['id']}/commit",
                            json={"expected_revision": b["revision"], "idempotency_key": str(uuid.uuid4())}).status_code == 404
    assert other.owner.get(f"{biz.base}/ledger").status_code == 404
    biz.commit(b)
    assert other.ledger() == [] and len(biz.ledger()) == 4


def test_ledger_is_append_only(biz):
    biz.commit(biz.decide(biz.staged_part1(), 4, "exclude").json())
    for stmt in ("UPDATE ledger_transaction SET amount_minor = 1", "DELETE FROM ledger_transaction"):
        with pytest.raises(DBAPIError, match="append-only"):
            with get_engine().begin() as conn:
                conn.execute(text(stmt))
    # TRUNCATE is refused too (by the append-only trigger, or first by tables that reference the ledger).
    with pytest.raises(DBAPIError, match="append-only|referenced in a foreign key"):
        with get_engine().begin() as conn:
            conn.execute(text("TRUNCATE ledger_transaction"))
    assert len(biz.ledger()) == 4


# --- row normalization (pure) -------------------------------------------------------------------------


def _t(v):
    return {"t": "text", "v": v} if v is not None else None


SIGNED = Mapping(date_column=0, date_format="YYYY-MM-DD", description_column=1, amount_mode="signed",
                 amount_column=2, currency_column=3)
TODAY = date(2026, 10, 20)


@pytest.mark.parametrize("cells, field, needle", [
    (["2026-10-21", "x", "1", None], "date", "future"),
    (["1999-12-31", "x", "1", None], "date", "2000"),
    (["2026-13-01", "x", "1", None], "date", "does not match"),
    (["2026-10-02", "x", "0", None], "amount", "zero"),
    (["2026-10-02", "x", "1.234,50", None], "amount", "recognised"),
    (["2026-10-02", "x", "10.555", None], "amount", "decimal"),
    (["2026-10-02", "x", "1", "USD"], "currency", "no conversion"),
    (["2026-10-02", "x​hidden", "1", None], "description", "control"),
    (["2026-10-02", None, "1", None], "description", "missing"),
    (["2026-10-02", "@SUM(A1)", "1", None], "column 2", "formula"),
    (["2026-10-02", "+cmd|' /C calc'!A0", "1", None], "column 2", "formula"),
])
def test_row_level_validation(cells, field, needle):
    res = normalize_row([_t(c) for c in cells], SIGNED, "BDT", TODAY)
    assert not res.ok
    assert any(e["field"] == field and needle in e["message"] for e in res.errors), res.errors
    assert res.amount_minor is None and res.business_date is None


def test_row_normalization_accepts_common_bank_formats():
    res = normalize_row([_t("2026-10-02"), _t("  Shop   rent "), _t("(45,000.00)"), _t("bdt")], SIGNED, "BDT", TODAY)
    assert res.ok and res.amount_minor == -4_500_000 and res.description == "Shop rent"
    debit_credit = Mapping(date_column=0, date_format="DD/MM/YYYY", description_column=1, amount_mode="debit_credit",
                           debit_column=2, credit_column=3)
    both = normalize_row([_t("02/10/2026"), _t("x"), _t("5"), _t("5")], debit_credit, "BDT", TODAY)
    assert not both.ok and "exactly one" in both.errors[0]["message"]
    credit = normalize_row([_t("02/10/2026"), _t("x"), None, _t("৳ 1,00,000.50")], debit_credit, "BDT", TODAY)
    assert credit.ok and credit.amount_minor == 10_000_050
