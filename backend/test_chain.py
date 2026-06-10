"""End-to-end smoke test of the powder-to-cash chain against a mock Mongo.

Run: .venv/bin/python -m pytest test_chain.py -v
"""
import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import db
from mongomock_motor import AsyncMongoMockClient


@pytest.fixture(scope="module")
async def client():
    db._client = AsyncMongoMockClient()
    import seed
    await seed.seed()
    import server
    transport = ASGITransport(app=server.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


async def login(c, email, password):
    r = await c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def test_full_chain(client):
    c = client
    admin = await login(c, "werner@fenixrising.co.za", "changeme-werner")
    controller = await login(c, "pierre@fenixrising.co.za", "changeme-pierre")
    capturer = await login(c, "steven@fenixrising.co.za", "changeme-steven")

    # wrong password rejected
    r = await c.post("/api/auth/login", json={"email": "werner@fenixrising.co.za", "password": "nope"})
    assert r.status_code == 401

    # config: admin sees material cost, capturer does not
    r = await c.get("/api/nodes/gogreen/config", headers=admin)
    assert r.json()["material_cost_per_kg"] == 20.0
    r = await c.get("/api/nodes/gogreen/config", headers=capturer)
    assert "material_cost_per_kg" not in r.json()

    # blank capture sheet PDF
    r = await c.get("/api/nodes/gogreen/capture-sheet.pdf", headers=capturer)
    assert r.status_code == 200 and r.content[:4] == b"%PDF"

    # ---- day 1: clean capture (powder balances exactly) ----
    r = await c.post("/api/nodes/gogreen/captures?date=2026-06-01", headers=capturer)
    cap1 = r.json()["_id"]
    # 2x2500 A + 1x2500 B + 1x2500 reject = 4x36 = 144; 2x5000 A = 150 -> drawn 294
    r = await c.post(f"/api/captures/{cap1}/entries", headers=capturer, json={
        "powder_in_kg": 1000, "powder_drawn_kg": 294,
        "production": [
            {"tank_type": "2500L", "quantity_a": 2, "quantity_b": 1, "quantity_reject": 1},
            {"tank_type": "5000L", "quantity_a": 2, "quantity_b": 0, "quantity_reject": 0},
        ]})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "reconciled" and r.json()["flags_raised"] == []

    # powder balance = 1000 - 294
    r = await c.get("/api/nodes/gogreen/powder", headers=capturer)
    assert r.json()["balance"] == 706.0

    # finished goods: rejects never enter
    r = await c.get("/api/nodes/gogreen/finished-goods", headers=capturer)
    on_hand = {(o["tank_type"], o["grade"]): o["quantity"] for o in r.json()["on_hand"]}
    assert on_hand == {("2500L", "A"): 2, ("2500L", "B"): 1, ("5000L", "A"): 2}

    # scrap log has the reject, cost admin-only
    r = await c.get("/api/nodes/gogreen/scrap", headers=admin)
    assert r.json()[0]["kg_lost"] == 36.0 and r.json()[0]["material_cost_lost"] == 720.0
    r = await c.get("/api/nodes/gogreen/scrap", headers=capturer)
    assert "material_cost_lost" not in r.json()[0]

    # ---- day 2: powder gap -> flag, zero tolerance ----
    r = await c.post("/api/nodes/gogreen/captures?date=2026-06-02", headers=capturer)
    cap2 = r.json()["_id"]
    r = await c.post(f"/api/captures/{cap2}/entries", headers=capturer, json={
        "powder_drawn_kg": 100,
        "production": [{"tank_type": "2500L", "quantity_a": 2, "quantity_b": 0, "quantity_reject": 0}]})
    assert r.json()["status"] == "captured" and len(r.json()["flags_raised"]) == 1
    r = await c.get("/api/nodes/gogreen/flags?status=open", headers=controller)
    assert any(f["type"] == "powder_variance" for f in r.json())

    # capturer cannot resolve flags (separation of duties)
    flag_id = r.json()[0]["_id"]
    r = await c.post(f"/api/flags/{flag_id}/resolve", headers=capturer,
                     json={"resolution_note": "x"})
    assert r.status_code == 403
    # controller resolves with a note; empty note rejected
    r = await c.post(f"/api/flags/{flag_id}/resolve", headers=controller,
                     json={"resolution_note": "  "})
    assert r.status_code == 400
    r = await c.post(f"/api/flags/{flag_id}/resolve", headers=controller,
                     json={"resolution_note": "Operator re-counted: 28kg spill cleaned and re-bagged."})
    assert r.status_code == 200

    # ---- delivery note ----
    r = await c.post("/api/nodes/gogreen/delivery-notes", headers=capturer, json={
        "date": "2026-06-03", "client_name": "Buffalo Builders",
        "client_details": "12 Main Rd, Queenstown",
        "lines": [{"tank_type": "2500L", "grade": "A", "quantity": 2},
                  {"tank_type": "2500L", "grade": "B", "quantity": 1}]})
    assert r.status_code == 200, r.text
    dn = r.json()
    assert dn["dn_number"] == "GG-DN-0001"
    r = await c.get(f"/api/delivery-notes/{dn['_id']}/pdf", headers=capturer)
    assert r.content[:4] == b"%PDF"

    # over-delivery breaks the FG ledger -> flag
    r = await c.post("/api/nodes/gogreen/delivery-notes", headers=capturer, json={
        "date": "2026-06-03", "client_name": "Test", "lines":
        [{"tank_type": "5000L", "grade": "A", "quantity": 99}]})
    r = await c.get("/api/nodes/gogreen/flags?status=open", headers=controller)
    assert any(f["type"] == "finished_goods_mismatch" for f in r.json())

    # ---- recon sweep flags DN without invoice ----
    r = await c.post("/api/nodes/gogreen/recon/sweep", headers=controller)
    assert len(r.json()["delivery_without_invoice"]) == 2

    # ---- invoice: B-grade line carries reduced price ----
    r = await c.post("/api/nodes/gogreen/invoices", headers=capturer, json={
        "date": "2026-06-03", "client_name": "Buffalo Builders",
        "client_details": "12 Main Rd, Queenstown",
        "lines": [{"tank_type": "2500L", "grade": "A", "quantity": 2, "unit_price": 2200},
                  {"tank_type": "2500L", "grade": "B", "quantity": 1, "unit_price": 1500}],
        "delivery_note_ids": [dn["_id"]]})
    assert r.status_code == 200, r.text
    inv = r.json()
    assert inv["invoice_number"] == "GG-INV-0001"
    assert inv["subtotal"] == 5900.0 and inv["vat"] == 885.0 and inv["total"] == 6785.0
    r = await c.get(f"/api/invoices/{inv['_id']}/pdf", headers=admin)
    assert r.content[:4] == b"%PDF"
    # linking the invoice resolved that DN's flag
    r = await c.get("/api/nodes/gogreen/flags?status=open", headers=controller)
    assert not any(f["type"] == "delivery_without_invoice"
                   and f["references"].get("delivery_note_id") == dn["_id"] for f in r.json())

    # capturer cannot create payments or match (separation of duties)
    r = await c.post("/api/nodes/gogreen/payments", headers=capturer,
                     json={"date": "2026-06-05", "amount": 6785, "bank_reference": "GG-INV-0001"})
    assert r.status_code == 403

    # ---- payment + match + split ----
    r = await c.post("/api/nodes/gogreen/payments", headers=controller,
                     json={"date": "2026-06-05", "amount": 6785, "bank_reference": "GG-INV-0001"})
    pay = r.json()
    r = await c.post(f"/api/payments/{pay['_id']}/match", headers=controller,
                     json={"invoice_id": inv["_id"]})
    assert r.status_code == 200, r.text
    m = r.json()
    # fenix: 2 x 1620 (A) + 1 x 1620 x 100% (B at launch pct) = 4860
    assert m["split"]["fenix_exworks_value"] == 4860.0
    assert m["split"]["partner_balance"] == 6785.0 - 4860.0
    assert m["invoice_status"] == "paid"

    # short payment flags
    r = await c.post("/api/nodes/gogreen/invoices", headers=capturer, json={
        "date": "2026-06-04", "client_name": "Short Payer",
        "lines": [{"tank_type": "5000L", "grade": "A", "quantity": 1, "unit_price": 4000}]})
    inv2 = r.json()
    r = await c.post("/api/nodes/gogreen/payments", headers=controller,
                     json={"date": "2026-06-06", "amount": 1000, "bank_reference": "partial"})
    pay2 = r.json()
    r = await c.post(f"/api/payments/{pay2['_id']}/match", headers=controller,
                     json={"invoice_id": inv2["_id"]})
    assert r.json()["invoice_status"] == "part_paid"
    r = await c.get("/api/nodes/gogreen/flags?status=open", headers=controller)
    assert any(f["type"] == "short_paid" for f in r.json())

    # ---- physical count variance ----
    r = await c.post("/api/nodes/gogreen/counts", headers=controller, json={
        "date": "2026-06-07", "powder_kg_counted": 600,
        "finished_goods_counted": [{"tank_type": "5000L", "grade": "A", "quantity": 2}]})
    assert r.status_code == 200
    assert len(r.json()["flags_raised"]) >= 1  # powder 600 vs 606 system

    # ---- recon dashboard ----
    r = await c.get("/api/nodes/gogreen/recon?month=2026-06", headers=controller)
    days = {d["date"]: d["status"] for d in r.json()["days"]}
    assert days["2026-06-01"] == "clear"

    # ---- reports + network kg ----
    r = await c.get("/api/nodes/gogreen/reports/monthly?month=2026-06", headers=admin)
    rep = r.json()
    # day1: 4x36 + 2x75 = 294; day2: 2x36 = 72 -> 366
    assert rep["kg_through_plant"] == 366.0
    assert rep["scrap_material_cost"] == 720.0
    r = await c.get("/api/nodes/gogreen/reports/monthly?month=2026-06", headers=controller)
    assert "scrap_material_cost" not in r.json()

    r = await c.get("/api/network/kg", headers=capturer)
    assert r.json()["total_kg"] == 366.0

    # audit log captured the writes, admin-only
    r = await c.get("/api/audit", headers=admin)
    assert len(r.json()) > 10
    r = await c.get("/api/audit", headers=controller)
    assert r.status_code == 403
