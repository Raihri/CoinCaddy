"""Region Pack: pack validation, per-business selection, catalogue selection, import templates, and "money never changes"."""

import copy
import json
import uuid
from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db import get_engine
from app.modules.funding.engine import match
from app.modules.records.service import business_today
from app.modules.region_packs.registry import PACKS_DIR, Pack, PackError, load_packs, validate_catalogue
from tests.helpers import Biz, make_user

DOCS = ["bank_statements", "id", "trade_licence", "invoices", "wallet_history", "tax_certificate"]


def _raw(pid):
    return json.loads((PACKS_DIR / pid / "pack.json").read_text(encoding="utf-8"))


def _catalogue(pid):
    return json.loads((PACKS_DIR / pid / "catalogue.json").read_text(encoding="utf-8"))


# --- the pack files themselves -------------------------------------------------------------------------


def test_shipped_packs_are_valid_versioned_and_distinct():
    packs = load_packs()
    assert sorted(packs) == ["bd", "in"]
    bd, ind = packs["bd"].pack, packs["in"].pack
    assert (bd.version, bd.jurisdiction, bd.currency) == ("region-bd-2026.10", "BD", "BDT")
    assert (ind.version, ind.jurisdiction, ind.currency) == ("region-in-2026.10", "IN", "INR")
    assert bd.currency_display.symbol == "৳" and ind.currency_display.symbol == "₹"
    assert bd.date_display != ind.date_display and bd.labels != ind.labels
    for lp in packs.values():
        assert "compliance" in lp.pack.notice.lower() and lp.catalogue["source"].startswith("Written for this prototype")
        # a pack that is not legal advice must say so, and no product claims to be anything but a sample
        assert all("Illustrative" in p["provider"] and p["source"]["updated"] for p in lp.catalogue["products"])


def test_india_catalogue_is_in_whole_paise_for_india_only():
    products = _catalogue("in")["products"]
    assert len(products) == 4
    for p in products:
        assert p["jurisdictions"] == ["IN"] and p["currencies"] == ["INR"]
        assert all(type(p[k]) is int for k in ("min_amount_minor", "max_amount_minor", "min_monthly_revenue_minor"))


def test_each_template_maps_its_own_headers_with_the_importers_mapping_rules():
    for lp in load_packs().values():
        for t in lp.pack.import_templates:
            headers = list(t.columns.values())
            m = t.mapping_for(headers)
            assert m.date_format == t.date_format and m.amount_mode == t.amount_mode
            assert t.mapping_for([h.upper() + "  " for h in headers]).date_column == m.date_column   # case and spacing do not matter
            with pytest.raises(ValueError, match="missing"):
                t.mapping_for(headers[1:])
            with pytest.raises(ValueError, match="repeated"):
                t.mapping_for(headers + [headers[0]])


@pytest.mark.parametrize("change,message", [
    (lambda d: d.update(minor_unit_exponent=0), "exponent"),
    (lambda d: d.update(minor_unit_exponent=2.0), "exponent"),
    (lambda d: d.update(jurisdiction="XX"), "jurisdiction"),
    (lambda d: d.update(currency="KES"), "currency"),
    (lambda d: d.update(version="2026.10"), "version"),
    (lambda d: d.update(version="region-bd-2026.10"), "version"),                           # applied to the IN pack: id and version disagree
    (lambda d: d.update(date_display="MM/DD/YY"), "date_display"),
    (lambda d: d.update(language="english"), "language"),
    (lambda d: d["number_format"].update(decimal_separator=","), "decimal_separator"),
    (lambda d: d["number_format"].update(grouping="myriad"), "grouping"),
    (lambda d: d["number_format"].update(group_separator="."), "group_separator"),
    (lambda d: d["currency_display"].update(symbol="‮"), "symbol"),
    (lambda d: d["currency_display"].update(symbol=""), "symbol"),
    (lambda d: d["labels"].pop("cash"), "labels"),
    (lambda d: d["labels"].update(extra="x"), "labels"),
    (lambda d: d["labels"].update(cash="a​b"), "label cash"),
    (lambda d: d.update(import_templates=[]), "import_templates"),
    (lambda d: d["import_templates"][0].update(date_format="DD/MM/YY"), "date_format"),
    (lambda d: d["import_templates"][0]["columns"].update(date="Narration"), "different"),   # two fields on one header
    (lambda d: d["import_templates"][0]["columns"].update(surprise="X"), "unknown column"),
    (lambda d: d["import_templates"][1].update(id=d["import_templates"][0]["id"]), "unique"),
    (lambda d: d.update(unexpected="x"), "extra"),
    (lambda d: d.update(catalogue_file="../secrets.json"), "catalogue_file"),
])
def test_malformed_packs_are_refused(change, message):
    raw = _raw("in")
    change(raw)
    with pytest.raises(ValidationError) as err:
        Pack.model_validate(raw)
    assert message.lower() in str(err.value).lower()


def test_template_with_wrong_amount_columns_is_refused():
    raw = _raw("in")
    del raw["import_templates"][0]["columns"]["credit"]                                     # debit_credit mode needs both columns
    with pytest.raises(ValidationError):
        Pack.model_validate(raw)
    raw = _raw("in")
    raw["import_templates"][1]["columns"]["debit"] = "Out"                                  # signed mode must not have debit/credit
    with pytest.raises(ValidationError):
        Pack.model_validate(raw)


@pytest.mark.parametrize("change", [
    lambda c: c["products"][0].update(min_amount_minor=100.5),                              # no floating-point money
    lambda c: c["products"][0].update(max_amount_minor="25000000"),
    lambda c: c["products"][0]["pricing"].update(interest_annual_bps=21.5),
    lambda c: c["products"][0]["pricing"].pop("tenor_days"),
    lambda c: c["products"][0].update(min_amount_minor=0),
    lambda c: c["products"][0].update(max_amount_minor=1),                                  # empty range
    lambda c: c["products"][1].update(id=c["products"][0]["id"]),                           # duplicate id
    lambda c: c["products"][0].update(kind="crypto_loan"),
    lambda c: c["products"][0].update(security="guess"),
    lambda c: c["products"][0].update(purposes=["gambling"]),
    lambda c: c["products"][0].update(documents=["selfie"]),
    lambda c: c["products"][0].update(exchange_rate_to_bdt=1.3),                            # a pack cannot smuggle in a conversion rule
    lambda c: c["products"][0].update(currencies=["inr"]),
    lambda c: c["products"][1].pop("advance_rate_bps"),
    lambda c: c["products"][0]["source"].pop("updated"),
    lambda c: c.update(products=[]),
    lambda c: c.pop("version"),
    lambda c: [p.update(currencies=["USD"]) for p in c["products"]],                        # nothing for the pack's own market
])
def test_malformed_catalogues_are_refused(change):
    cat = _catalogue("in")
    change(cat)
    with pytest.raises(PackError):
        validate_catalogue(cat, load_packs()["in"].pack)


# --- the match engine on the India pack (hand-worked) ----------------------------------------------------


def _inr_inputs(revenue_90d=90_000_000):
    d = "2026-10-20"
    return {"as_of": d, "jurisdiction": "IN", "revenue_90d_minor": revenue_90d, "receivables_eligible_minor": 20_000_000, "outlook": {
        "calc_version": "outlook-v1", "as_of": d, "base_currency": "INR", "reserve": {"amount_minor": 8_000_000, "currency": "INR"},
        "accounts": [{"id": "bank", "label": "Bank", "currency": "INR", "usable_minor": 30_000_000, "freshness": "fresh", "evidence_through": d,
                      "uncovered_minor": 0, "uncovered_count": 0, "anchor_id": "an", "restriction_releases": []}],
        "obligations": [], "unmatched_transfers": []}}


def _inr_req(**kw):
    return {"amount_minor": 10_000_000, "currency": "INR", "needed_by": "2026-10-27", "purpose": "working_capital", "receiving_account_id": "bank",
            "months_trading": 18, "security_available": ["guarantor"], "documents_available": DOCS} | kw


def test_india_catalogue_hand_worked_terms_in_paise():
    cat = load_packs()["in"].catalogue_for_run()
    res = match(_inr_inputs(), cat, _inr_req())
    assert res["region_pack"] == {"id": "in", "version": "region-in-2026.10", "jurisdiction": "IN", "currency": "INR"}
    assert res["catalogue_version"] == "catalogue-in-2026.10"
    assert sorted(p["id"] for p in res["eligible"]) == ["in-invoice-advance", "in-wallet-micro-loan", "in-working-capital-line"]
    wc = next(p for p in res["eligible"] if p["id"] == "in-working-capital-line")["terms"]
    # 10,000,000 paise (INR 1,00,000): fee 1% = 100,000; interest 21% a year for 60 days = 10,000,000 x 2100 x 60 / (10000 x 365) = 345,205.48 -> 345,205
    assert (wc["processing_fee_minor"], wc["net_proceeds_minor"], wc["interest_or_fee_minor"]) == (100_000, 9_900_000, 345_205)
    assert (wc["total_repayment_minor"], wc["total_cost_minor"]) == (10_345_205, 445_205)
    adv = next(p for p in res["eligible"] if p["id"] == "in-invoice-advance")["terms"]
    # 45 days = 2 periods of 30 days at 1.20% = 240,000
    assert (adv["interest_or_fee_minor"], adv["total_repayment_minor"]) == (240_000, 10_240_000)
    ex = {p["id"]: p["reasons"] for p in res["excluded"]}
    assert list(ex) == ["in-term-loan"]                                                      # needs INR 2,00,000 or more and INR 5,00,000 of monthly receipts
    assert "below the minimum" in ex["in-term-loan"][0] and any("monthly operating receipts" in x for x in ex["in-term-loan"])


def test_this_catalogue_refuses_a_bdt_need_instead_of_converting():
    cat = load_packs()["in"].catalogue_for_run()
    inputs = _inr_inputs()
    inputs["outlook"]["accounts"][0].update(currency="BDT")
    inputs["outlook"]["base_currency"] = "BDT"
    inputs["outlook"]["reserve"]["currency"] = "BDT"
    inputs["jurisdiction"] = "BD"
    res = match(inputs, cat, _inr_req(currency="BDT"))
    assert res["eligible"] == [] and len(res["excluded"]) == 4
    for p in res["excluded"]:
        failed = {f["code"] for f in p["filters"] if not f["passed"]}
        assert {"jurisdiction", "currency"} <= failed                                        # not offered in BD, does not lend in BDT


# --- API: reading, switching, permissions ----------------------------------------------------------------


def _rp(biz, client=None):
    return (client or biz.owner).get(f"{biz.base}/region-pack")


def test_default_pack_follows_the_business_jurisdiction():
    biz = Biz()
    v = _rp(biz).json()
    assert v["source"] == "default" and v["active"]["id"] == "bd" and v["active"]["version"] == "region-bd-2026.10"
    assert v["jurisdiction_matches"] and v["currency_matches"] and v["warnings"] == [] and v["history"] == []
    assert [p["id"] for p in v["available"]] == ["bd", "in"] and v["can_select"] is True
    assert v["active"]["number_format"]["grouping"] == "lakh" and "never added together" in v["no_conversion_note"]
    ind = Biz(jurisdiction="IN", currency="INR", timezone="Asia/Kolkata")
    assert _rp(ind).json()["active"]["id"] == "in"
    us = Biz(jurisdiction="US", currency="USD", timezone="America/New_York")
    u = _rp(us).json()
    assert u["source"] == "none" and u["active"] is None and "no region pack for United States" in u["warnings"][0]


def test_business_with_no_pack_still_reads_the_default_catalogue_stamped_as_such():
    us = Biz(jurisdiction="US", currency="USD", timezone="America/New_York")
    cat = us.owner.get(f"{us.base}/funding-matches/catalogue").json()
    assert cat["version"] == "catalogue-2026.10" and cat["region_pack"]["id"] is None and "No region pack" in cat["region_pack"]["note"]


def test_every_member_can_read_but_only_the_owner_can_switch():
    biz = Biz()
    for role in ("bookkeeper", "staff"):
        c = biz.member(role)
        v = _rp(biz, c).json()
        assert v["active"]["id"] == "bd" and v["can_select"] is False
        r = c.put(f"{biz.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"})
        assert r.status_code == 403
    assert _rp(biz).json()["active"]["id"] == "bd" and _rp(biz).json()["history"] == []
    with get_engine().connect() as conn:
        denied = conn.execute(text("SELECT count(*) FROM audit_event WHERE business_id = :b AND action = 'regionpack.select' AND outcome = 'denied'"), {"b": biz.id}).scalar_one()
    assert denied == 2


def test_signed_out_and_other_business_callers_get_nothing():
    biz, other = Biz(), Biz()
    from fastapi.testclient import TestClient
    from app.main import app
    anon = TestClient(app)
    assert anon.get(f"{biz.base}/region-pack").status_code == 401
    assert anon.put(f"{biz.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"}).status_code == 401
    assert _rp(biz, other.owner).status_code == 404
    r = other.owner.put(f"{biz.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"})
    assert r.status_code == 404 and _rp(biz).json()["history"] == []
    assert anon.get("/api/reference/region-packs").status_code == 401
    _, c = make_user()
    assert [p["id"] for p in c.get("/api/reference/region-packs").json()] == ["bd", "in"]


@pytest.mark.parametrize("body,code", [
    ({"pack_id": "in"}, 422),                                                              # expected_current_pack_id is required
    ({"expected_current_pack_id": "bd"}, 422),
    ({"pack_id": "in", "expected_current_pack_id": "bd", "currency": "INR"}, 422),         # extra fields refused: a pack cannot set a currency
    ({"pack_id": "IN", "expected_current_pack_id": "bd"}, 422),
    ({"pack_id": "../in", "expected_current_pack_id": "bd"}, 422),
    ({"pack_id": 5, "expected_current_pack_id": "bd"}, 422),
    ({"pack_id": "in", "expected_current_pack_id": 7}, 422),
    ({"pack_id": "zz", "expected_current_pack_id": "bd"}, 422),                            # well-formed but unknown
    ({"pack_id": "in", "expected_current_pack_id": "in"}, 409),                            # stale screen
    ({"pack_id": "in", "expected_current_pack_id": None}, 409),
])
def test_switch_validation(body, code):
    biz = Biz()
    r = biz.owner.put(f"{biz.base}/region-pack", json=body)
    assert r.status_code == code, r.text
    assert _rp(biz).json()["history"] == [] and _rp(biz).json()["active"]["id"] == "bd"


def test_switch_is_recorded_audited_and_not_repeatable():
    biz = Biz()
    r = biz.owner.put(f"{biz.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"})
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["source"] == "selected" and v["active"]["id"] == "in" and v["selected_version"] == "region-in-2026.10" and not v["outdated"]
    assert v["history"][0]["previous_pack_id"] == "bd" and v["history"][0]["pack_id"] == "in"
    assert not v["jurisdiction_matches"] and not v["currency_matches"] and len(v["warnings"]) == 2
    assert "nothing is converted" in v["warnings"][1]
    again = biz.owner.put(f"{biz.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "in"})
    assert again.status_code == 409 and "already" in again.json()["detail"]
    back = biz.owner.put(f"{biz.base}/region-pack", json={"pack_id": "bd", "expected_current_pack_id": "in"}).json()
    assert back["active"]["id"] == "bd" and back["jurisdiction_matches"] and [h["pack_id"] for h in back["history"]] == ["bd", "in"]
    with get_engine().connect() as conn:
        rows = conn.execute(text("SELECT outcome, details FROM audit_event WHERE business_id = :b AND action = 'regionpack.select' ORDER BY seq"), {"b": biz.id}).all()
    assert [r.outcome for r in rows] == ["success", "conflict", "success"]
    assert rows[0].details["from"] == "bd" and rows[0].details["to"] == "in" and rows[0].details["version"] == "region-in-2026.10"


def test_history_cannot_be_edited_or_deleted():
    biz = Biz()
    assert biz.owner.put(f"{biz.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"}).status_code == 200
    for sql in ("UPDATE region_pack_selection SET pack_id = 'bd'", "DELETE FROM region_pack_selection", "TRUNCATE region_pack_selection"):
        with pytest.raises(DBAPIError):
            with get_engine().begin() as conn:
                conn.execute(text(sql))


def test_one_businesses_choice_does_not_affect_another():
    a, b = Biz(), Biz()
    assert a.owner.put(f"{a.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"}).status_code == 200
    assert _rp(b).json()["active"]["id"] == "bd"


# --- switching changes presentation and catalogue, never saved money ---------------------------------------


def _snapshot(biz):
    with get_engine().connect() as conn:
        ledger = conn.execute(text("SELECT id, amount_minor, currency, business_date FROM ledger_transaction WHERE business_id = :b ORDER BY id"), {"b": biz.id}).all()
        anchors = conn.execute(text("SELECT id, balance_minor, currency FROM balance_anchor WHERE business_id = :b ORDER BY id"), {"b": biz.id}).all()
        obligations = conn.execute(text("SELECT id, amount_minor, currency FROM obligation WHERE business_id = :b ORDER BY id"), {"b": biz.id}).all()
        business = conn.execute(text("SELECT base_currency, reserve_amount_minor, reserve_currency, jurisdiction, timezone, version FROM business WHERE id = :b"), {"b": biz.id}).one()
    return {"ledger": [tuple(r) for r in ledger], "anchors": [tuple(r) for r in anchors], "obligations": [tuple(r) for r in obligations],
            "business": tuple(business), "profile": biz.owner.get(biz.base).json(), "health": biz.owner.get(f"{biz.base}/financial-health").json()}


def _setup_bdt():
    from tests.test_funding_matches import _setup
    return _setup()


def _match(biz, currency="BDT", **kw):
    body = {"amount_minor": 4_000_000, "currency": currency, "needed_by": (biz.today + timedelta(days=7)).isoformat(), "purpose": "working_capital",
            "receiving_account_id": biz.account["ACC-BANK"], "months_trading": 18, "security_available": ["guarantor"], "documents_available": DOCS} | kw
    return biz.owner.post(f"{biz.base}/funding-matches", json=body)


def test_switching_pack_changes_the_catalogue_but_not_a_single_saved_amount():
    biz = _setup_bdt()
    first = _match(biz)
    assert first.status_code == 201, first.text
    run1 = first.json()
    assert run1["catalogue_version"] == "catalogue-2026.10" and run1["results"]["region_pack"]["id"] == "bd"
    assert sorted(p["id"] for p in run1["results"]["eligible"]) == ["invoice-advance", "term-loan", "wc-line"]
    before = _snapshot(biz)
    assert before["ledger"] and before["anchors"] and before["obligations"]

    assert biz.owner.put(f"{biz.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"}).status_code == 200
    after = _snapshot(biz)
    assert after == before                                                                   # ledger, anchors, bills, reserve, profile version, Financial Health: identical

    cat = biz.owner.get(f"{biz.base}/funding-matches/catalogue").json()
    assert cat["version"] == "catalogue-in-2026.10" and {p["id"] for p in cat["products"]} == {p["id"] for p in _catalogue("in")["products"]}
    assert cat["region_pack"]["id"] == "in"

    run2 = _match(biz).json()                                                                # a BDT need against the India sample catalogue
    assert run2["catalogue_version"] == "catalogue-in-2026.10" and run2["results"]["region_pack"]["version"] == "region-in-2026.10"
    assert run2["results"]["eligible"] == []                                                 # nothing is converted, so nothing matches
    assert all({"jurisdiction", "currency"} <= {f["code"] for f in p["filters"] if not f["passed"]} for p in run2["results"]["excluded"])

    # the earlier saved run is exactly as it was, and still reproduces from what it stored
    assert biz.owner.get(f"{biz.base}/funding-matches/{run1['id']}").json()["results"] == run1["results"]
    v = biz.owner.get(f"{biz.base}/funding-matches/{run1['id']}/verify").json()
    assert v["inputs_hash_matches"] and v["results_reproduce"]
    assert _snapshot(biz) == before

    assert biz.owner.put(f"{biz.base}/region-pack", json={"pack_id": "bd", "expected_current_pack_id": "in"}).status_code == 200
    assert _snapshot(biz) == before
    run3 = _match(biz).json()
    assert [p["id"] for p in run3["results"]["eligible"]] == [p["id"] for p in run1["results"]["eligible"]]


def test_india_business_gets_india_matches_in_inr_end_to_end():
    biz = Biz(jurisdiction="IN", currency="INR", timezone="Asia/Kolkata", reserve_minor=8_000_000)
    biz.today = business_today("Asia/Kolkata")
    bank = biz.account["ACC-BANK"]
    day = lambda n: (biz.today - timedelta(days=n)).isoformat()
    biz.post_rows("ACC-BANK", [(day(60), "Sales A", 30_000_000, "S1"), (day(40), "Sales B", 30_000_000, "S2"), (day(20), "Sales C", 30_000_000, "S3")])
    with get_engine().connect() as conn:
        ids = [r.id for r in conn.execute(text("SELECT id FROM ledger_transaction WHERE business_id = :b"), {"b": biz.id})]
    assert biz.owner.post(f"{biz.base}/classifications", json={"items": [{"txn_id": str(i), "kind": "operating_receipt", "category": "sales"} for i in ids]}).status_code == 200
    assert biz.owner.post(f"{biz.base}/cash/anchors", json={"account_id": bank, "anchor_date": biz.today.isoformat(), "balance_minor": 30_000_000, "currency": "INR",
                                                            "source_kind": "bank_statement", "source_reference": "Today"}).status_code == 201
    r = _match(biz, currency="INR", amount_minor=10_000_000)
    assert r.status_code == 201, r.text
    run = r.json()
    res = run["results"]
    assert res["currency"] == "INR" and res["region_pack"]["id"] == "in" and run["catalogue_version"] == "catalogue-in-2026.10"
    assert "in-working-capital-line" in {p["id"] for p in res["eligible"]} and all(p["id"].startswith("in-") for p in res["eligible"])
    assert all(p["terms"]["principal_minor"] == 10_000_000 and type(p["terms"]["total_cost_minor"]) is int for p in res["eligible"])
    v = biz.owner.get(f"{biz.base}/funding-matches/{run['id']}/verify").json()
    assert v["inputs_hash_matches"] and v["results_reproduce"]
    with get_engine().connect() as conn:
        d = conn.execute(text("SELECT details FROM audit_event WHERE business_id = :b AND action = 'funding.run' ORDER BY seq DESC LIMIT 1"), {"b": biz.id}).scalar_one()
    assert d["region_pack"] == "in"


# --- import templates ---------------------------------------------------------------------------------------


def _upload(biz, csv_text, account="ACC-BANK"):
    r = biz.owner.post(f"{biz.base}/imports", data={"account_id": biz.account[account]},
                       files={"file": (f"t-{uuid.uuid4().hex[:6]}.csv", csv_text.encode(), "text/csv")})
    assert r.status_code == 201, r.text
    return r.json()


def test_bangladesh_template_is_offered_applied_and_parses_exactly():
    biz = Biz()
    b = _upload(biz, "Reference,Date,Description,Amount\r\nR1,2026-10-01,Sales,1500.50\r\nR2,2026-10-02,Rent,-800.00\r\n")
    r = biz.owner.get(f"{biz.base}/imports/{b['id']}/region-templates").json()
    assert r["pack"]["id"] == "bd" and r["account_currency"] == "BDT"
    by_id = {t["id"]: t for t in r["templates"]}
    t = by_id["bd-bank-signed"]
    assert t["matches_file"] and t["usable"] and t["mapping"]["date_column"] == 1 and t["mapping"]["amount_column"] == 3 and t["mapping"]["reference_column"] == 0
    assert not by_id["bd-wallet-debit-credit"]["matches_file"] and "missing" in by_id["bd-wallet-debit-credit"]["reason"] and by_id["bd-wallet-debit-credit"]["mapping"] is None
    applied = biz.owner.put(f"{biz.base}/imports/{b['id']}/mapping", json={"expected_revision": b["revision"], "mapping": t["mapping"]})
    assert applied.status_code == 200, applied.text
    rows = biz.owner.get(f"{biz.base}/imports/{b['id']}/rows?view=all").json()["rows"]
    assert [x["amount_minor"] for x in rows] == [150_050, -80_000] and applied.json()["counts"]["valid"] == 2   # exact minor units


def test_india_template_for_an_inr_account_and_refusal_for_a_bdt_account():
    ind = Biz(jurisdiction="IN", currency="INR", timezone="Asia/Kolkata")
    text_ = "Txn Date,Narration,Chq/Ref No,Withdrawal,Deposit\r\n05/10/2026,Rent,C1,25000.00,\r\n06/10/2026,Sales,C2,,1,25,000.50\r\n"
    text_ = text_.replace('1,25,000.50', '"1,25,000.50"')
    b = _upload(ind, text_)
    r = ind.owner.get(f"{ind.base}/imports/{b['id']}/region-templates").json()
    t = next(x for x in r["templates"] if x["id"] == "in-bank-debit-credit")
    assert r["pack"]["id"] == "in" and t["usable"] and t["date_format"] == "DD/MM/YYYY"
    done = ind.owner.put(f"{ind.base}/imports/{b['id']}/mapping", json={"expected_revision": b["revision"], "mapping": t["mapping"]}).json()
    rows = ind.owner.get(f"{ind.base}/imports/{b['id']}/rows?view=all").json()["rows"]
    assert done["counts"]["valid"] == 2 and [x["amount_minor"] for x in rows] == [-2_500_000, 12_500_050]      # lakh grouping parsed exactly
    assert [x["business_date"] for x in rows] == ["2026-10-05", "2026-10-06"]

    # a BDT account on the same pack: the layout matches the file, but offering it would mislabel the currency, so it is not usable
    bd = Biz()
    assert bd.owner.put(f"{bd.base}/region-pack", json={"pack_id": "in", "expected_current_pack_id": "bd"}).status_code == 200
    b2 = _upload(bd, "Txn Date,Narration,Chq/Ref No,Withdrawal,Deposit\r\n05/10/2026,Rent,C1,250.00,\r\n")
    t2 = next(x for x in bd.owner.get(f"{bd.base}/imports/{b2['id']}/region-templates").json()["templates"] if x["id"] == "in-bank-debit-credit")
    assert t2["matches_file"] and not t2["usable"] and t2["mapping"] is None and "INR" in t2["reason"] and "BDT" in t2["reason"]


def test_template_endpoint_is_scoped_and_validates_ids():
    biz, other = Biz(), Biz()
    b = _upload(biz, "Date,Description,Reference,Amount\r\n2026-10-01,Sales,R1,10.00\r\n")
    assert other.owner.get(f"{biz.base}/imports/{b['id']}/region-templates").status_code == 404
    assert other.owner.get(f"{other.base}/imports/{b['id']}/region-templates").status_code == 404     # the batch is not theirs
    assert biz.owner.get(f"{biz.base}/imports/not-a-uuid/region-templates").status_code == 422
    assert biz.owner.get(f"{biz.base}/imports/{uuid.uuid4()}/region-templates").status_code == 404
    assert biz.member("staff").get(f"{biz.base}/imports/{b['id']}/region-templates").status_code == 200   # staff may stage imports, so they see templates
    us = Biz(jurisdiction="US", currency="USD", timezone="America/New_York")
    b3 = _upload(us, "Date,Description,Reference,Amount\r\n2026-10-01,Sales,R1,10.00\r\n")
    assert us.owner.get(f"{us.base}/imports/{b3['id']}/region-templates").json() == {"pack": None, "templates": []}


def test_malicious_header_text_is_only_data():
    biz = Biz()
    b = _upload(biz, 'Date,Description,Reference,Amount,(""http://x"")"\r\n2026-10-01,Sales,R1,10.00,x\r\n')
    r = biz.owner.get(f"{biz.base}/imports/{b['id']}/region-templates").json()
    assert next(t for t in r["templates"] if t["id"] == "bd-bank-signed")["usable"]
    assert "<script>" not in json.dumps(r)                                                   # file text is never echoed by this endpoint


def test_loaded_catalogue_copy_is_private():
    lp = load_packs()["in"]
    c1 = lp.catalogue_for_run()
    c1["products"][0]["min_amount_minor"] = 1
    assert lp.catalogue["products"][0]["min_amount_minor"] != 1 and lp.catalogue_for_run()["products"][0]["min_amount_minor"] != 1
    assert copy.deepcopy(lp.catalogue) == lp.catalogue
