"""End-to-end smoke test of the three-warehouse stock model against a mock Mongo.

Movements: powder received/issued (per type), fittings received/issued, tanks moulded,
tanks booked to store, tanks dispatched. The app derives every balance and flag.

Run: .venv/bin/python -m pytest test_chain.py -v
"""
import os

import pytest
from httpx import ASGITransport, AsyncClient

import db
from mongomock_motor import AsyncMongoMockClient


@pytest.fixture(scope="module")
async def client():
    db._client = AsyncMongoMockClient()
    import seed  # triggers load_dotenv()
    # hermetic: force the changeme-* defaults regardless of any real passwords in .env
    os.environ["WERNER_PASSWORD"] = "changeme-werner"
    os.environ["PIERRE_PASSWORD"] = "changeme-pierre"
    os.environ["STEVEN_PASSWORD"] = "changeme-steven"
    await seed.seed()
    import server
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(c, email, password):
    r = await c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


CONFIG = {
    "tank_types": [
        {"code": "2500L", "name": "2500L Tank", "ex_works_price": 1620, "weight_kg": 36, "lid_weight_kg": 1},
        {"code": "5000L", "name": "5000L Tank", "ex_works_price": 3450, "weight_kg": 75, "lid_weight_kg": 1},
    ],
    "material_cost_per_kg": 20, "b_grade_exworks_pct": 100, "vat_rate": 15, "payment_terms_days": 30,
    "powder_products": [
        {"code": "BLACK", "colour": "Black", "is_black": True},
        {"code": "GREEN", "colour": "Green", "is_black": False},
    ],
    "fitting_types": [{"code": "OUTLET", "name": "Outlet kit"}],
    "fittings_per_tank": {"2500L": {"OUTLET": 1}, "5000L": {"OUTLET": 2}},
    "tolerances": {"powder_kg": 0, "tank_qty": 0, "fittings_qty": 0},
}


async def test_full_chain(client):
    c = client
    admin = await login(c, "werner@fenixrising.co.za", "changeme-werner")
    audit = await login(c, "pierre@fenixrising.co.za", "changeme-pierre")
    ops = await login(c, "steven@fenixrising.co.za", "changeme-steven")

    r = await c.post("/api/auth/login", json={"email": "werner@fenixrising.co.za", "password": "nope"})
    assert r.status_code == 401

    # admin sets the Brief-2 config (powder colours, fittings, lids)
    r = await c.put("/api/nodes/gogreen/config", headers=admin, json=CONFIG)
    assert r.status_code == 200, r.text

    # admin sees material cost; operations does not
    assert (await c.get("/api/nodes/gogreen/config", headers=admin)).json()["material_cost_per_kg"] == 20
    assert "material_cost_per_kg" not in (await c.get("/api/nodes/gogreen/config", headers=ops)).json()

    # blank capture sheet PDF
    r = await c.get("/api/nodes/gogreen/capture-sheet.pdf", headers=ops)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"

    # ---- Day 1: clean full capture ----
    # mould 4x2500 (a2 b1 reject1) + 2x5000 (a2). Recipe: 2500 -> 18 colour / 19 black; 5000 -> 37.5 / 38.5.
    # consumed black = 4*19 + 2*38.5 = 153 ; colour = 4*18 + 2*37.5 = 147.  Issue exactly that -> floor 0.
    cap1 = (await c.post("/api/nodes/gogreen/captures?date=2026-06-01", headers=ops)).json()["_id"]
    r = await c.post(f"/api/captures/{cap1}/entries", headers=ops, json={
        "powder": [
            {"powder_type": "BLACK", "received_kg": 1000, "issued_kg": 153},
            {"powder_type": "GREEN", "received_kg": 1000, "issued_kg": 147},
        ],
        "fittings": [{"fitting_type": "OUTLET", "received_qty": 100, "issued_qty": 8}],
        "production": [
            {"tank_type": "2500L", "quantity_a": 2, "quantity_b": 1, "quantity_reject": 1},
            {"tank_type": "5000L", "quantity_a": 2, "quantity_b": 0, "quantity_reject": 0},
        ],
        "booked": [
            {"tank_type": "2500L", "quantity_a": 2, "quantity_b": 1},
            {"tank_type": "5000L", "quantity_a": 2, "quantity_b": 0},
        ],
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reconciled" and r.json()["flags_raised"] == []

    # powder: warehouse per type, floor pools both 0
    pw = (await c.get("/api/nodes/gogreen/powder", headers=ops)).json()
    wh = {w["powder_type"]: w["balance"] for w in pw["warehouse"]}
    assert wh == {"BLACK": 847.0, "GREEN": 853.0}
    assert pw["floor"] == {"black": 0.0, "colour": 0.0}

    # fittings: warehouse 100-8=92, issued==expected
    fit = (await c.get("/api/nodes/gogreen/fittings", headers=ops)).json()["warehouse"][0]
    assert fit["balance"] == 92 and fit["issued"] == 8 and fit["expected"] == 8 and fit["variance"] == 0

    # finished goods: booked into the store, nothing on the floor
    fg = {(p["tank_type"], p["grade"]): p for p in (await c.get("/api/nodes/gogreen/finished-goods", headers=ops)).json()["positions"]}
    assert fg[("2500L", "A")]["store"] == 2 and fg[("2500L", "B")]["store"] == 1 and fg[("5000L", "A")]["store"] == 2
    assert all(p["floor"] == 0 for p in fg.values())

    # scrap: reject consumes full powder incl. lid (37 kg), cost admin-only
    sc = (await c.get("/api/nodes/gogreen/scrap", headers=admin)).json()[0]
    assert sc["kg_lost"] == 37.0 and sc["material_cost_lost"] == 740.0
    assert "material_cost_lost" not in (await c.get("/api/nodes/gogreen/scrap", headers=ops)).json()[0]

    # ---- delivery note (document + number only; no FG deduction here) ----
    r = await c.post("/api/nodes/gogreen/delivery-notes", headers=ops, json={
        "date": "2026-06-03", "client_name": "Buffalo Builders", "client_details": "12 Main Rd",
        "lines": [{"tank_type": "2500L", "grade": "A", "quantity": 2},
                  {"tank_type": "2500L", "grade": "B", "quantity": 1}]})
    dn = r.json()
    assert dn["dn_number"] == "GG-DN-0001"
    assert (await c.get(f"/api/delivery-notes/{dn['_id']}/pdf", headers=ops)).content[:4] == b"%PDF"

    # ---- Day 3: dispatch capture deducts FG, references the DN ----
    cap3 = (await c.post("/api/nodes/gogreen/captures?date=2026-06-03", headers=ops)).json()["_id"]
    r = await c.post(f"/api/captures/{cap3}/entries", headers=ops, json={
        "dispatched": [
            {"tank_type": "2500L", "grade": "A", "quantity": 2, "dn_number": "GG-DN-0001"},
            {"tank_type": "2500L", "grade": "B", "quantity": 1, "dn_number": "GG-DN-0001"},
        ]})
    assert r.status_code == 200 and r.json()["status"] == "reconciled", r.text
    fg = {(p["tank_type"], p["grade"]): p for p in (await c.get("/api/nodes/gogreen/finished-goods", headers=ops)).json()["positions"]}
    assert fg[("2500L", "A")]["store"] == 0 and fg[("5000L", "A")]["store"] == 2

    # dispatch requires a DN number
    cap_bad = (await c.post("/api/nodes/gogreen/captures?date=2026-06-03", headers=ops)).json()["_id"]
    r = await c.post(f"/api/captures/{cap_bad}/entries", headers=ops, json={
        "dispatched": [{"tank_type": "5000L", "grade": "A", "quantity": 1, "dn_number": ""}]})
    assert r.status_code == 400

    # ---- sweep flags the DN without an invoice; invoicing resolves it ----
    assert len((await c.post("/api/nodes/gogreen/recon/sweep", headers=audit)).json()["delivery_without_invoice"]) >= 1
    r = await c.post("/api/nodes/gogreen/invoices", headers=ops, json={
        "date": "2026-06-03", "client_name": "Buffalo Builders",
        "lines": [{"tank_type": "2500L", "grade": "A", "quantity": 2, "unit_price": 2200},
                  {"tank_type": "2500L", "grade": "B", "quantity": 1, "unit_price": 1500}],
        "delivery_note_ids": [dn["_id"]]})
    inv = r.json()
    assert inv["invoice_number"] == "GG-INV-0001" and inv["total"] == 6785.0
    open_flags = (await c.get("/api/nodes/gogreen/flags?status=open", headers=audit)).json()
    assert not any(f["type"] == "delivery_without_invoice" and f["references"].get("delivery_note_id") == dn["_id"] for f in open_flags)

    # operations cannot create payments (separation of duties)
    assert (await c.post("/api/nodes/gogreen/payments", headers=ops,
                         json={"date": "2026-06-05", "amount": 6785})).status_code == 403

    # ---- payment + match + split ----
    pay = (await c.post("/api/nodes/gogreen/payments", headers=audit,
                        json={"date": "2026-06-05", "amount": 6785, "bank_reference": "GG-INV-0001"})).json()
    m = (await c.post(f"/api/payments/{pay['_id']}/match", headers=audit, json={"invoice_id": inv["_id"]})).json()
    assert m["split"]["fenix_exworks_value"] == 4860.0  # 2*1620 (A) + 1*1620 (B at 100%)
    assert m["invoice_status"] == "paid"

    # ---- Day 5: fittings variance (issue 5 outlets for 1 tank that needs 1) ----
    cap5 = (await c.post("/api/nodes/gogreen/captures?date=2026-06-05", headers=ops)).json()["_id"]
    r = await c.post(f"/api/captures/{cap5}/entries", headers=ops, json={
        "powder": [{"powder_type": "BLACK", "received_kg": 0, "issued_kg": 19},
                   {"powder_type": "GREEN", "received_kg": 0, "issued_kg": 18}],
        "fittings": [{"fitting_type": "OUTLET", "received_qty": 0, "issued_qty": 5}],
        "production": [{"tank_type": "2500L", "quantity_a": 1, "quantity_b": 0, "quantity_reject": 0}]})
    assert r.json()["flags_raised"]  # at least one flag id returned
    assert any(f["type"] == "fittings_variance" for f in (await c.get("/api/nodes/gogreen/flags?status=open", headers=audit)).json())

    # ---- Day 7: powder floor goes negative (mould a 5000 with no powder issued) ----
    cap7 = (await c.post("/api/nodes/gogreen/captures?date=2026-06-07", headers=ops)).json()["_id"]
    r = await c.post(f"/api/captures/{cap7}/entries", headers=ops, json={
        "fittings": [{"fitting_type": "OUTLET", "received_qty": 0, "issued_qty": 2}],
        "production": [{"tank_type": "5000L", "quantity_a": 1, "quantity_b": 0, "quantity_reject": 0}]})
    assert r.json()["status"] == "captured"
    open_flags = (await c.get("/api/nodes/gogreen/flags?status=open", headers=audit)).json()
    assert any(f["type"] == "powder_variance" for f in open_flags)

    # operations cannot resolve; audit resolves with a note
    pv = next(f for f in open_flags if f["type"] == "powder_variance")
    assert (await c.post(f"/api/flags/{pv['_id']}/resolve", headers=ops, json={"resolution_note": "x"})).status_code == 403
    assert (await c.post(f"/api/flags/{pv['_id']}/resolve", headers=audit, json={"resolution_note": "  "})).status_code == 400
    assert (await c.post(f"/api/flags/{pv['_id']}/resolve", headers=audit,
                         json={"resolution_note": "Re-issued 76kg that was already on the floor; corrected."})).status_code == 200

    # ---- Day 8: over-dispatch breaks the finished-goods identity ----
    await c.post("/api/nodes/gogreen/delivery-notes", headers=ops, json={
        "date": "2026-06-08", "client_name": "Test", "lines": [{"tank_type": "5000L", "grade": "A", "quantity": 99}]})
    cap8 = (await c.post("/api/nodes/gogreen/captures?date=2026-06-08", headers=ops)).json()["_id"]
    r = await c.post(f"/api/captures/{cap8}/entries", headers=ops, json={
        "dispatched": [{"tank_type": "5000L", "grade": "A", "quantity": 99, "dn_number": "GG-DN-0002"}]})
    assert r.json()["flags_raised"]
    assert any(f["type"] == "finished_goods_mismatch" for f in (await c.get("/api/nodes/gogreen/flags?status=open", headers=audit)).json())

    # ---- physical count: powder warehouse short -> count_mismatch ----
    r = await c.post("/api/nodes/gogreen/counts", headers=audit, json={
        "date": "2026-06-09",
        "powder_counted": [{"powder_type": "BLACK", "warehouse_kg": 800, "floor_kg": 0},
                           {"powder_type": "GREEN", "warehouse_kg": 835, "floor_kg": 0}],
        "fg_warehouse_counted": [], "tank_floor_counted": [],
        "fittings_counted": [{"fitting_type": "OUTLET", "warehouse_qty": 85}]})
    assert r.status_code == 200 and len(r.json()["flags_raised"]) >= 1

    # ---- recon dashboard + reports + network kg (lids included) ----
    days = {d["date"]: d["status"] for d in (await c.get("/api/nodes/gogreen/recon?month=2026-06", headers=audit)).json()["days"]}
    assert days["2026-06-01"] == "clear"

    rep = (await c.get("/api/nodes/gogreen/reports/monthly?month=2026-06", headers=admin)).json()
    # moulded: day1 6 tanks (4x2500 + 2x5000), day5 1x2500, day7 1x5000.
    # kg incl lids = (4*37 + 2*76) + 37 + 76 = 300 + 37 + 76 = 413
    assert rep["kg_through_plant"] == 413.0
    assert rep["scrap_material_cost"] == 740.0
    assert "scrap_material_cost" not in (await c.get("/api/nodes/gogreen/reports/monthly?month=2026-06", headers=audit)).json()
    assert (await c.get("/api/network/kg", headers=ops)).json()["total_kg"] == 413.0

    # audit log is admin-only
    assert len((await c.get("/api/audit", headers=admin)).json()) > 10
    assert (await c.get("/api/audit", headers=audit)).status_code == 403
