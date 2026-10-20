"""Shared test helpers: throwaway users and businesses, and loading the Northstar fixture through the real import API."""

import uuid
from collections import defaultdict

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.fixtures.seed import load_fixture
from app.main import app
from app.modules.identity.passwords import hash_password

PASSWORD = get_settings().demo_user_password
FIXTURE = load_fixture()
FIXTURE_ACCOUNTS = {a["ref"]: a for a in FIXTURE["accounts"]}


def make_user() -> tuple[str, TestClient]:
    email = f"t-{uuid.uuid4().hex[:12]}@test.example"
    with get_engine().begin() as conn:
        conn.execute(text("INSERT INTO app_user (email, display_name, password_hash) VALUES (:e, :n, :h)"),
                     {"e": email, "n": f"User {email[2:8]}", "h": hash_password(PASSWORD)})
    client = TestClient(app)
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return email, client


class Biz:
    """A fresh business owned by a fresh user, with the three Northstar fixture accounts."""

    def __init__(self, reserve_minor: int = 8_000_000, jurisdiction: str = "BD", currency: str = "BDT", timezone: str = "Asia/Dhaka"):
        _, self.owner = make_user()
        body = self.owner.post("/api/businesses", json={
            "name": "Fixture Co", "jurisdiction": jurisdiction, "base_currency": currency, "timezone": timezone,
            "reserve": {"amount_minor": reserve_minor, "currency": currency},
            "accounts": [{"label": a["label"], "kind": a["kind"], "currency": currency} for a in FIXTURE["accounts"]],
        })
        assert body.status_code == 201, body.text
        b = body.json()
        self.id = b["id"]
        self.base = f"/api/businesses/{self.id}"
        self.account = {ref: next(x["id"] for x in b["accounts"] if x["label"] == a["label"]) for ref, a in FIXTURE_ACCOUNTS.items()}
        self.txn: dict[str, str] = {}  # fixture ref (T001...) -> ledger transaction id
        self.batch: dict[str, str] = {}  # account ref -> import batch id

    def member(self, role: str) -> TestClient:
        email, client = make_user()
        with get_engine().begin() as conn:
            conn.execute(text("INSERT INTO membership (business_id, user_id, role) SELECT :b, id, :r FROM app_user WHERE email = :e"),
                         {"b": self.id, "e": email, "r": role})
        return client

    def post_rows(self, account_ref: str, rows: list[tuple[str, str, int, str]]) -> str:
        """Import (date, description, amount_minor, reference) rows into one account and post them all."""
        lines = ["Date,Description,Reference,Amount"]
        for d, desc, amt, ref in rows:
            sign = "-" if amt < 0 else ""
            lines.append(f"{d},{desc},{ref},{sign}{abs(amt) // 100}.{abs(amt) % 100:02d}")
        data = ("\r\n".join(lines) + "\r\n").encode()
        r = self.owner.post(f"{self.base}/imports", data={"account_id": self.account[account_ref]},
                            files={"file": (f"{account_ref}-{uuid.uuid4().hex[:6]}.csv", data, "text/csv")})
        assert r.status_code == 201, r.text
        b = r.json()
        b = self.owner.put(f"{self.base}/imports/{b['id']}/mapping", json={"expected_revision": b["revision"], "mapping": {
            "date_column": 0, "date_format": "YYYY-MM-DD", "description_column": 1, "reference_column": 2,
            "amount_mode": "signed", "amount_column": 3}}).json()
        assert b["counts"]["valid"] == len(rows) and b["counts"]["pending"] == 0, b["counts"]
        r = self.owner.post(f"{self.base}/imports/{b['id']}/commit", json={"expected_revision": b["revision"], "idempotency_key": str(uuid.uuid4())})
        assert r.status_code == 200, r.text
        return b["id"]

    def post_fixture(self) -> "Biz":
        """Post all eight Northstar fixture transactions, one import per account; references are the fixture refs."""
        per_account = defaultdict(list)
        for t in FIXTURE["transactions"]:
            per_account[t["account"]].append((t["business_date"], t["memo"].replace(",", ""), t["amount_minor"], t["ref"]))
        for acc_ref, rows in per_account.items():
            self.batch[acc_ref] = self.post_rows(acc_ref, rows)
        with get_engine().connect() as conn:
            for r in conn.execute(text("SELECT id, reference FROM ledger_transaction WHERE business_id = :b"), {"b": self.id}):
                self.txn[r.reference] = str(r.id)
        return self
