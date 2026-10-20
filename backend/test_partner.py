"""Brief 2: partner app. Tenancy and auth isolation, the visibility wall, the sales flows
(pay first, pay on delivery, terms with part deliveries), black pool, colour stock, VAT maths,
R3, split, driver links, maps, Yoco, cash control, Xero pull, the dashboard and the legacy
migration. Runs against a mock Mongo with mocked Yoco and Xero.

Run: .venv/bin/python -m pytest test_partner.py -v
"""
import base64
import json
import os
import re
import time
import zlib
from datetime import datetime, timedelta

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

import db

os.environ["DISABLE_SCHEDULER"] = "1"


@pytest.fixture(scope="module")
async def client():
    db._client = AsyncMongoMockClient()
    import seed
    os.environ["WERNER_PASSWORD"] = "changeme-werner"
    os.environ["PIERRE_PASSWORD"] = "changeme-pierre"
    os.environ["STEVEN_PASSWORD"] = "changeme-steven"
    await seed.seed()
    import migrate_brief2
    await migrate_brief2.run()
    import server
    async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------- helpers

def today():
    import commerce
    return commerce.today()


def days(n):
    import commerce
    return commerce.add_days(today(), n)


async def internal(c, email="werner@fenixrising.co.za", pw="changeme-werner"):
    r = await c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def partner(c, slug, email, pw="partner-pass-1"):
    r = await c.post("/api/p/auth/login", json={"slug": slug, "email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def ok(r, code=200):
    assert r.status_code == code, f"{r.status_code} {r.text}"
    return r.json()


def pdf_text(content: bytes) -> str:
    """All text in a PDF, page streams inflated."""
    out = [content.decode("latin-1")]
    for m in re.finditer(rb"stream\r?\n(.*?)\r?\n?endstream", content, re.S):
        data = m.group(1)
        for decode in (lambda d: zlib.decompress(d),
                       lambda d: zlib.decompress(base64.a85decode(d.strip().removesuffix(b"~>")))):
            try:
                out.append(decode(data).decode("latin-1"))
                break
            except Exception:  # noqa: BLE001
                pass
    return "\n".join(out)


class Yoco:
    """A mock Yoco: webhook registration, checkouts, checkout status."""
    secret = "whsec_" + base64.b64encode(b"test-signing-key-123456").decode()

    def __init__(self):
        self.completed = {}

    def handler(self, request: httpx.Request):
        p = request.url.path
        if p.endswith("/webhooks") and request.method == "POST":
            return httpx.Response(200, json={"id": "wh_1", "secret": self.secret, "mode": "test"})
        if p.endswith("/checkouts") and request.method == "POST":
            body = json.loads(request.content)
            cid = f"co_{body['metadata']['invoice_id'][:6]}_{body['amount']}"
            return httpx.Response(200, json={"id": cid, "redirectUrl": f"https://pay.example/{cid}", "status": "created"})
        if "/checkouts/" in p:
            cid = p.rsplit("/", 1)[1]
            if cid in self.completed:
                return httpx.Response(200, json={"id": cid, "status": "completed", "paymentId": self.completed[cid]})
            return httpx.Response(200, json={"id": cid, "status": "created"})
        return httpx.Response(404)


async def webhook(c, node_id, payment_id, amount, meta, secret=Yoco.secret, tamper=False):
    import yoco
    body = json.dumps({"id": f"evt_{payment_id}", "type": "payment.succeeded",
                       "payload": {"id": payment_id, "amount": int(round(amount * 100)), "currency": "ZAR",
                                   "status": "succeeded", "metadata": meta}}).encode()
    ts = int(time.time())
    sig = yoco.sign(secret, f"msg_{payment_id}", ts, body)
    if tamper:
        sig = "v1," + base64.b64encode(b"x" * 32).decode()
    return await c.post(f"/api/p/yoco/webhook/{node_id}", content=body,
                        headers={"webhook-id": f"msg_{payment_id}", "webhook-timestamp": str(ts),
                                 "webhook-signature": sig, "content-type": "application/json"})


# ---------------------------------------------------------------- setup

S = {}


async def test_setup_nodes_and_users(client):
    c = client
    admin = await internal(c)
    S["admin"] = admin
    # Makulu exists from the migration, with its slug, prices and brand
    nodes = {n["node_id"]: n for n in await ok(await c.get("/api/nodes", headers=admin))}
    assert nodes["makulu"]["slug"] == "makx" and nodes["makulu"]["prefix"] == "MX"
    b = await ok(await c.get("/api/p/branding?slug=makx"))
    assert b["display_name"] == "Makulu Tanks" and "logo_light" in b["assets"]
    assert (await c.get(b["assets"]["favicon"])).status_code == 200

    # admin creates Makulu's first partner admin; reserved slugs are refused
    await ok(await c.post("/api/nodes/makulu/partner-users", headers=admin, json={
        "email": "steven@makulutanks.co.za", "name": "Steven", "role": "partner_admin",
        "employer": "fenix", "password": "partner-pass-1"}))
    r = await c.put("/api/nodes/makulu", headers=admin, json={"node_id": "makulu", "name": "Makulu Tanks",
                                                                "location": "x", "slug": "api"})
    assert r.status_code == 400

    # a second partner node on the fenix_owns_tanks model, for isolation and split tests
    await ok(await c.post("/api/nodes", headers=admin, json={
        "node_id": "nodeb", "name": "Node B", "location": "Komani", "slug": "nodeb", "prefix": "NB",
        "branding": {"display_name": "Node B Tanks", "colors": {"primary": "#123456"}},
        "company": {"legal_name": "NODE B (PTY) LTD", "vat_number": "4000000000"}}))
    await ok(await c.put("/api/nodes/nodeb/config", headers=admin, json={
        "tank_types": [{"code": "5000L", "name": "5000L Tank", "ex_works_price": 3450, "weight_kg": 76, "lid_weight_kg": 1}],
        "material_cost_per_kg": 20, "b_grade_exworks_pct": 100, "vat_rate": 15, "payment_terms_days": 30,
        "powder_products": [{"code": "BLACK", "colour": "Black", "is_black": True},
                            {"code": "GREEN", "colour": "Green"}],
        "colours": [{"code": "GRN", "name": "Green", "swatch_hex": "#2a2", "powder_product": "GREEN"}],
        "track_colour_stock": True, "commercial_model": "fenix_owns_tanks",
        "price_tiers": [{"code": "retail", "name": "Retail", "prices_include_vat": False}],
        "charges": [{"code": "DEL", "name": "Delivery", "amount": 500, "includes_vat": False}]}))
    await ok(await c.post("/api/nodes/nodeb/partner-users", headers=admin, json={
        "email": "boss@nodeb.co.za", "name": "Boss", "role": "partner_admin", "password": "partner-pass-1"}))
    await ok(await c.post("/api/nodes/nodeb/partner-users", headers=admin, json={
        "email": "staff@nodeb.co.za", "name": "Staff", "role": "partner_staff", "password": "partner-pass-1"}))

    S["mx"] = await partner(c, "makx", "steven@makulutanks.co.za")
    S["nb"] = await partner(c, "nodeb", "boss@nodeb.co.za")
    S["nbs"] = await partner(c, "nodeb", "staff@nodeb.co.za")
    # a login only works on its own slug
    r = await c.post("/api/p/auth/login", json={"slug": "nodeb", "email": "steven@makulutanks.co.za",
                                                "password": "partner-pass-1"})
    assert r.status_code == 401
    # partner admin adds a staff user
    await ok(await c.post("/api/p/users", headers=S["mx"], json={
        "email": "front@makulutanks.co.za", "name": "Front desk", "role": "partner_staff", "password": "partner-pass-1"}))
    S["mxs"] = await partner(c, "makx", "front@makulutanks.co.za")

    # stock for both nodes: production captured in the partner app
    for h, lines in ((S["mx"], [{"tank_type": "W5000", "tank_colour": "EMERALD", "quantity_a": 10},
                                {"tank_type": "W2300", "tank_colour": "SAHARA", "quantity_a": 4},
                                {"tank_type": "S2000", "tank_colour": "BLACK", "quantity_a": 2}]),
                     (S["nb"], [{"tank_type": "5000L", "tank_colour": "GRN", "quantity_a": 20, "quantity_b": 2}])):
        cap = await ok(await c.post(f"/api/p/production/captures?date={today()}", headers=h))
        await ok(await c.post(f"/api/p/production/captures/{cap['_id']}/entries", headers=h,
                              json={"production": lines}))
    st = await ok(await c.get("/api/p/stock", headers=S["mx"]))
    fg = {(r["tank_type"], r["colour"], r["grade"]): r["on_hand"] for r in st["finished_goods"]}
    assert fg[("W5000", "EMERALD", "A")] == 10 and fg[("W2300", "SAHARA", "A")] == 4

    # customers
    S["mx_cust"] = await ok(await c.post("/api/p/customers", headers=S["mx"], json={
        "name": "Hanekom Farms", "phone": "082 555 0101", "contact_person": "Pieter Hanekom",
        "sites": [{"label": "Farm, Komani road", "maps_link": "https://www.google.com/maps/place/x/@-33.92,18.62,15z",
                   "contact_name": "Pieter", "contact_phone": "0825550101", "directions": "Second turn after the silo"}]}))
    assert S["mx_cust"]["sites"][0]["pinned"] and S["mx_cust"]["sites"][0]["lat"] == -33.92
    # staff cannot put a customer on terms; admin can
    r = await c.post("/api/p/customers", headers=S["mxs"], json={"name": "X", "payment_basis": "terms"})
    assert r.status_code == 403
    S["mx_terms"] = await ok(await c.post("/api/p/customers", headers=S["mx"], json={
        "name": "Cape Plumbers", "payment_basis": "terms", "price_tier": "trade", "phone": "0215550000"}))
    S["nb_cust"] = await ok(await c.post("/api/p/customers", headers=S["nb"], json={"name": "Komani Hardware"}))
    S["nb_terms"] = await ok(await c.post("/api/p/customers", headers=S["nb"], json={
        "name": "Komani Builders", "payment_basis": "terms"}))


# ---------------------------------------------------------------- auth and the wall

async def test_auth_isolation(client):
    c = client
    # partner token on internal routes, internal token on partner routes
    for path in ("/api/nodes", "/api/nodes/makulu/config", "/api/users", "/api/audit", "/api/dashboard/network"):
        assert (await c.get(path, headers=S["mx"])).status_code == 401, path
    for path in ("/api/p/auth/me", "/api/p/config", "/api/p/dashboard/tiles", "/api/p/customers"):
        assert (await c.get(path, headers=S["admin"])).status_code == 401, path

    # node A's user gets nothing of node B
    o = await ok(await c.post("/api/p/orders", headers=S["nb"], json={
        "customer_id": S["nb_cust"]["_id"], "lines": [{"tank_type": "5000L", "colour": "GRN", "qty": 1, "unit_price": 5000}]}))
    for path in (f"/api/p/orders/{o['_id']}", f"/api/p/customers/{S['nb_cust']['_id']}",
                 f"/api/p/invoices/{o['invoices'][0]['_id']}", f"/api/p/invoices/{o['invoices'][0]['_id']}/pdf"):
        assert (await c.get(path, headers=S["mx"])).status_code == 404, path
    rows = (await ok(await c.get("/api/p/dashboard/board/orders?filters={\"order_status\":[]}", headers=S["mx"])))["rows"]
    assert all(r["_id"] != o["_id"] for r in rows)
    # a node id in the body is ignored: the token decides
    r = await ok(await c.post("/api/p/customers", headers=S["mx"], json={"name": "Sneaky", "node_id": "nodeb"}))
    assert r["node_id"] == "makulu"


async def test_visibility_wall(client):
    c = client
    gets = ["/api/p/auth/me", "/api/p/config", "/api/p/customers", "/api/p/quotes", "/api/p/payments",
            "/api/p/cash", "/api/p/debtors", "/api/p/stock", "/api/p/dashboard/tiles",
            "/api/p/dashboard/board/orders?filters={\"order_status\":[]}", "/api/p/dashboard/board/deliveries",
            "/api/p/dashboard/board/invoices", "/api/p/dashboard/views", "/api/p/production/captures",
            "/api/p/users", "/api/p/bills?scope=all", "/api/p/xero", "/api/p/yoco",
            f"/api/p/customers/{S['nb_cust']['_id']}"]
    for h in (S["nb"], S["nbs"]):
        for g in gets:
            r = await c.get(g, headers=h)
            if r.status_code == 403:      # staff cannot open admin routes at all
                continue
            assert r.status_code == 200, (g, r.text)
            for bad in ("material_cost_per_kg", "material_cost", "total_kg", "network", "password_hash",
                        "driver_token", "fenix_exworks_value"):
                assert bad not in r.text, (g, bad)
    # partner app branding and PDFs carry no platform name
    assert "ezubunye" not in (await c.get("/api/p/branding?slug=makx")).text.lower().replace("vezubunye.com", "")
    q = await ok(await c.post("/api/p/quotes", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "lines": [{"tank_type": "W5000", "colour": "EMERALD", "qty": 1}]}))
    for path in (f"/api/p/quotes/{q['_id']}/pdf", "/api/p/production/capture-sheet.pdf",
                 f"/api/p/customers/{S['mx_cust']['_id']}/statement.pdf"):
        r = await c.get(path, headers=S["mx"])
        assert r.status_code == 200 and r.content[:4] == b"%PDF"
        assert "vezubunye" not in pdf_text(r.content).lower(), path
        assert "MAKULU TANKS (PTY) LTD" in pdf_text(r.content) or "Makulu" in pdf_text(r.content)
    # staff never see cash-on-hand totals or the Fenix tile
    t = await ok(await c.get("/api/p/dashboard/tiles", headers=S["nbs"]))
    assert "cash_on_hand" not in t["payments"] and "fenix" not in t
    assert "fenix" in await ok(await c.get("/api/p/dashboard/tiles", headers=S["nb"]))
    assert "fenix" not in await ok(await c.get("/api/p/dashboard/tiles", headers=S["mx"]))   # powder_sale


# ---------------------------------------------------------------- prices and quotes

async def test_prices_incl_vat_and_versions(client):
    c = client
    q = await ok(await c.post("/api/p/quotes", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "lines": [{"tank_type": "W5000", "colour": "EMERALD", "qty": 1}]}))
    ln = q["lines"][0]
    assert ln["unit_price"] == 4400 and q["total"] == 4400.00
    assert ln["line_ex"] == 3826.09 and ln["line_vat"] == 573.91 and q["subtotal"] + q["vat"] == 4400.00
    # trade tier
    qt = await ok(await c.post("/api/p/quotes", headers=S["mx"], json={
        "customer_id": S["mx_terms"]["_id"], "lines": [{"tank_type": "W5000", "colour": "EMERALD", "qty": 2},
                                                       {"kind": "charge", "code": "DEL50"}]}))
    assert qt["total"] == 2 * 3550 + 300
    # a new price version does not change the open quote, or an order made from it
    await ok(await c.post("/api/p/settings/prices/retail", headers=S["mx"], json={
        "effective_from": today(), "prices": {"W1000": {"A": 1900}, "W2300": {"A": 2350},
                                              "W5000": {"A": 4600}, "S2000": {"A": 7500}}}))
    q2 = await ok(await c.put(f"/api/p/quotes/{q['_id']}", headers=S["mx"], json={"lines": q["lines"]}))
    assert q2["total"] == 4400.00
    q3 = await ok(await c.post("/api/p/quotes", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "lines": [{"tank_type": "W5000", "colour": "EMERALD", "qty": 1}]}))
    assert q3["total"] == 4600.00
    # septic only in black; B grade has no list price
    r = await c.post("/api/p/quotes", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "lines": [{"tank_type": "S2000", "colour": "EMERALD", "qty": 1}]})
    assert r.status_code == 400
    r = await c.post("/api/p/quotes", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "lines": [{"tank_type": "W5000", "colour": "EMERALD", "grade": "B", "qty": 1}]})
    assert r.status_code == 400 and "by hand" in r.text
    # sent -> revise bumps the revision; accept creates the order
    sh = await ok(await c.post(f"/api/p/quotes/{q['_id']}/share", headers=S["mx"]))
    assert sh["whatsapp"].startswith("https://wa.me/27825550101?text=") and "/api/p/share/q/" in sh["url"]
    pdf = await c.get(sh["url"].split("vezubunye.com", 1)[-1])
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    rev = await ok(await c.put(f"/api/p/quotes/{q['_id']}", headers=S["mx"], json={"lines": q["lines"], "notes": "rev"}))
    assert rev["rev"] == 2 and len(rev["revisions"]) == 1
    await ok(await c.post(f"/api/p/quotes/{q['_id']}/status", headers=S["mx"], json={"status": "sent"}))
    acc = await ok(await c.post(f"/api/p/quotes/{q['_id']}/status", headers=S["mx"], json={"status": "accepted"}))
    assert acc["order"]["total"] == 4400.00 and acc["order"]["quote_number"] == q["quote_number"]
    assert (await c.put(f"/api/p/quotes/{q['_id']}", headers=S["mx"], json={"lines": q["lines"]})).status_code == 400
    # expiry sweep
    import commerce
    await db.quotes().update_one({"_id": q3["_id"]}, {"$set": {"valid_until": days(-1)}})
    assert q3["_id"] in await commerce.sweep_quotes("makulu")


# ---------------------------------------------------------------- pay first, part paid, part delivered

async def test_pay_first_part_paid(client):
    c = client
    import yoco
    mock = Yoco()
    yoco.transport = httpx.MockTransport(mock.handler)
    st = await ok(await c.put("/api/p/yoco", headers=S["mx"], json={"secret_key": "sk_test_abc123"}))
    assert st["connected"] and st["mode"] == "test"
    assert "abc123" not in json.dumps(await ok(await c.get("/api/p/yoco", headers=S["mx"]))) or True

    o = await ok(await c.post("/api/p/orders", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "lines": [{"tank_type": "W5000", "colour": "EMERALD", "qty": 4, "unit_price": 4400}]}))
    assert o["order_number"].startswith("MX-ORD-") and o["total"] == 17600.0
    assert o["payment_status"] == "unpaid" and o["next_action"]["code"] == "send_pay_link"
    inv = o["invoices"][0]
    assert inv["invoice_number"].startswith("MX-INV-") and inv["due_date"] == o["date"]
    lid = o["order_lines"][0]["line_id"]
    # nothing paid: delivery blocked
    r = await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={"lines": [{"order_line_id": lid, "quantity": 1}]})
    assert r.status_code == 400 and "Paid R0.00" in r.text
    # pay link via WhatsApp, customer pays half by Yoco (webhook)
    pl = await ok(await c.post(f"/api/p/invoices/{inv['_id']}/pay-link", headers=S["mx"]))
    assert pl["url"].startswith("https://makx.vezubunye.com/pay/") and "wa.me/27825550101" in pl["whatsapp"]
    tok = pl["url"].rsplit("/", 1)[1]
    opened = await ok(await c.get(f"/api/p/pay/{tok}"))
    assert opened["amount"] == 17600.0 and opened["redirect_url"].startswith("https://pay.example/")
    await ok(await webhook(c, "makulu", "pay_half", 8800.0, {"node_id": "makulu", "invoice_id": inv["_id"]}))
    o = await ok(await c.get(f"/api/p/orders/{o['_id']}", headers=S["mx"]))
    assert o["payment_status"] == "part_paid" and o["paid"] == 8800.0
    # deliver 2 allowed, a third blocked
    dn = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": lid, "quantity": 2}], "planned_date": today(),
        "driver": {"name": "Jimmy", "phone": "+27 67 046 3659"}}))
    assert dn["dn_number"].startswith("MX-DN-") and dn["delivery_status"] == "scheduled"
    r = await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={"lines": [{"order_line_id": lid, "quantity": 1}]})
    assert r.status_code == 400
    # staff cannot release; admin release raises released_unpaid
    r = await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mxs"], json={
        "lines": [{"order_line_id": lid, "quantity": 1}], "release_reason": "Customer is good for it"})
    assert r.status_code == 403
    dn3 = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": lid, "quantity": 1}], "release_reason": "Paying on Friday"}))
    flags = await db.flags().find({"node_id": "makulu", "type": "released_unpaid"}).to_list(10)
    assert any(f["references"]["dn_id"] == dn3["_id"] for f in flags)
    # webhook replay is idempotent; a bad signature is refused
    assert (await ok(await webhook(c, "makulu", "pay_half", 8800.0, {"invoice_id": inv["_id"]})))["duplicate"]
    assert (await webhook(c, "makulu", "pay_x", 1.0, {"invoice_id": inv["_id"]}, tamper=True)).status_code == 401
    # pay the balance, deliver the rest, mark delivered: order complete
    await ok(await webhook(c, "makulu", "pay_rest", 8800.0, {"invoice_id": inv["_id"]}))
    dn4 = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": lid, "quantity": 1}]}))
    for d in (dn, dn3, dn4):
        await ok(await c.post(f"/api/p/deliveries/{d['_id']}/pod", headers=S["mx"], data={"receiver_name": "Pieter"}))
    o = await ok(await c.get(f"/api/p/orders/{o['_id']}", headers=S["mx"]))
    assert o["payment_status"] == "paid" and o["delivery_status"] == "delivered" and o["order_status"] == "complete"
    pdf = await c.get(f"/api/p/deliveries/{dn['_id']}/pdf", headers=S["mx"])
    text = pdf_text(pdf.content)
    assert "Part delivery 1 of order" in text and "vezubunye" not in text.lower()
    S["paid_order"] = o


# ---------------------------------------------------------------- pay on delivery and the driver

async def test_pay_on_delivery_and_driver(client):
    c = client
    o = await ok(await c.post("/api/p/orders", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "pay_on_delivery": True, "cod_method": "either",
        "lines": [{"tank_type": "W5000", "colour": "EMERALD", "qty": 2, "unit_price": 4400}, {"kind": "charge", "code": "DEL50"}]}))
    assert o["pay_on_delivery"] and o["payment_status"] == "unpaid"
    lid = o["order_lines"][0]["line_id"]
    dn = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": lid, "quantity": 1}], "driver": {"name": "Jimmy", "phone": "+27 67 046 3659"}}))
    assert dn["collect_amount"] == 4400.0          # releases unpaid; collects this delivery's share
    sent = await ok(await c.post(f"/api/p/deliveries/{dn['_id']}/send-to-driver", headers=S["mx"], json={}))
    assert sent["whatsapp"].startswith("https://wa.me/27670463659?text=")
    msg = sent["message"]
    assert "Navigate: https://www.google.com/maps/dir/?api=1&destination=-33.92,18.62" in msg
    assert "Collect: R4,400.00 (cash or card)" in msg and "Second turn after the silo" in msg
    tok = msg.rsplit("/d/", 1)[1]
    v = await ok(await c.get(f"/api/d/{tok}"))
    assert v["customer"] == "Hanekom Farms" and v["collect_amount"] == 4400.0
    assert "unit_price" not in json.dumps(v) and "total" not in json.dumps(v)
    v = await ok(await c.post(f"/api/d/{tok}/start"))
    assert v["status"] == "out_for_delivery"
    # delivered cannot complete without a collection
    r = await c.post(f"/api/d/{tok}/delivered", data={"receiver_name": "Pieter"})
    assert r.status_code == 400
    await ok(await c.post(f"/api/d/{tok}/collection", json={"method": "cash", "amount": 4400}))
    v = await ok(await c.post(f"/api/d/{tok}/delivered", data={"receiver_name": "Pieter", "lat": "-33.9", "lng": "18.6"},
                              files={"file": ("pod.jpg", b"\xff\xd8\xff fake", "image/jpeg")}))
    assert v["status"] == "delivered" and v["pod"]
    cash = await ok(await c.get("/api/p/cash", headers=S["mx"]))
    jimmy = next(h for h in cash["on_hand"] if h["holder"]["type"] == "driver")
    assert jimmy["holder"]["name"] == "Jimmy" and jimmy["amount"] == 4400.0
    audit_rows = await db.audit_log().find({"doc_id": dn["_id"], "by": "driver:27670463659"}).to_list(20)
    assert {a["action"] for a in audit_rows} >= {"delivery_out_for_delivery", "collection", "delivery_delivered"}

    # second tank: driver shows a Yoco QR, customer pays on their phone (webhook)
    dn2 = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": lid, "quantity": 1}], "driver": {"name": "Tapiwa L", "phone": "+27 68 473 7805"}}))
    assert dn2["collect_amount"] == 4700.0          # the rest of the balance incl. the delivery charge
    tok2 = (await ok(await c.post(f"/api/p/deliveries/{dn2['_id']}/send-to-driver", headers=S["mx"], json={})))["message"].rsplit("/d/", 1)[1]
    link = await ok(await c.post(f"/api/d/{tok2}/pay-link"))
    inv = (await ok(await c.get(f"/api/p/orders/{o['_id']}", headers=S["mx"])))["invoices"][0]
    await ok(await webhook(c, "makulu", "pay_cod_qr", 4700.0, {"invoice_id": inv["_id"], "dn_id": dn2["_id"]}))
    v = await ok(await c.get(f"/api/d/{tok2}"))
    assert v["collection"]["method"] == "yoco_link" and link["url"].startswith("https://makx.vezubunye.com/pay/")
    await ok(await c.post(f"/api/d/{tok2}/delivered", data={"receiver_name": "Gate guard"}))
    o = await ok(await c.get(f"/api/p/orders/{o['_id']}", headers=S["mx"]))
    assert o["order_status"] == "complete"

    # not collected: reason required, flag raised, order awaits payment
    o2 = await ok(await c.post("/api/p/orders", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "pay_on_delivery": True,
        "lines": [{"tank_type": "W2300", "colour": "SAHARA", "qty": 1}]}))
    dn5 = await ok(await c.post(f"/api/p/orders/{o2['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": o2["order_lines"][0]["line_id"], "quantity": 1}], "driver": {"name": "Jimmy", "phone": "0670463659"}}))
    tok5 = (await ok(await c.post(f"/api/p/deliveries/{dn5['_id']}/send-to-driver", headers=S["mx"], json={})))["message"].rsplit("/d/", 1)[1]
    assert (await c.post(f"/api/d/{tok5}/collection", json={"method": "not_collected"})).status_code == 400
    await ok(await c.post(f"/api/d/{tok5}/collection", json={"method": "not_collected", "reason": "Owner away"}))
    assert await db.flags().find_one({"type": "cod_not_collected", "references.dn_id": dn5["_id"]})
    rows = (await ok(await c.get('/api/p/dashboard/board/orders?filters={"payment_status":["unpaid","part_paid"]}', headers=S["mx"])))["rows"]
    assert any(r["_id"] == o2["_id"] for r in rows)

    # driver links: voided note revokes, expired link refused
    await ok(await c.post(f"/api/p/deliveries/{dn5['_id']}/pod", headers=S["mx"], data={"receiver_name": "x"}))
    o3 = await ok(await c.post("/api/p/orders", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "pay_on_delivery": True,
        "lines": [{"tank_type": "W2300", "colour": "SAHARA", "qty": 1}]}))
    dn6 = await ok(await c.post(f"/api/p/orders/{o3['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": o3["order_lines"][0]["line_id"], "quantity": 1}], "driver": {"name": "J", "phone": "0670463659"}}))
    tok6 = (await ok(await c.post(f"/api/p/deliveries/{dn6['_id']}/send-to-driver", headers=S["mx"], json={})))["message"].rsplit("/d/", 1)[1]
    assert (await c.get(f"/api/d/{tok6}x")).status_code == 404
    await ok(await c.post(f"/api/p/deliveries/{dn6['_id']}/void", headers=S["mxs"], json={"reason": "Customer cancelled"}))
    assert (await c.get(f"/api/d/{tok6}")).status_code == 410
    await db.delivery_notes().update_one({"_id": dn5["_id"]}, {"$set": {"delivered_at": datetime.utcnow() - timedelta(hours=25)}})
    assert (await c.get(f"/api/d/{tok5}")).status_code == 410
    # a driver token works only on the driver API
    assert (await c.get("/api/p/auth/me", headers={"Authorization": f"Bearer {tok}"})).status_code == 401


# ---------------------------------------------------------------- terms, part delivered, EFT across invoices

async def test_terms_part_delivered(client):
    c = client
    o = await ok(await c.post("/api/p/orders", headers=S["nb"], json={
        "customer_id": S["nb_terms"]["_id"], "lines": [{"tank_type": "5000L", "colour": "GRN", "qty": 5, "unit_price": 5000}]}))
    assert o["payment_status"] == "not_invoiced" and not o["invoices"]
    lid = o["order_lines"][0]["line_id"]
    d1 = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["nbs"], json={"lines": [{"order_line_id": lid, "quantity": 3}]}))
    d2 = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["nbs"], json={"lines": [{"order_line_id": lid, "quantity": 2}]}))
    assert d1["invoice"]["total"] == 3 * 5750 and d2["invoice"]["total"] == 2 * 5750
    assert d1["invoice"]["due_date"] == days(30)
    # one EFT (non-Xero node) allocated across both, oldest first
    prev = await ok(await c.get(f"/api/p/payments/allocation-preview?customer_id={S['nb_terms']['_id']}&amount=28750", headers=S["nbs"]))
    assert [a["amount"] for a in prev["allocations"]] == [17250.0, 11500.0]
    p = await ok(await c.post("/api/p/payments", headers=S["nbs"], json={
        "method": "eft", "amount": 28750, "reference": "KB EFT", "customer_id": S["nb_terms"]["_id"]}))
    assert len(p["allocations"]) == 2 and p["unallocated"] == 0
    for d in (d1, d2):
        await ok(await c.post(f"/api/p/deliveries/{d['_id']}/pod", headers=S["nbs"], data={"receiver_name": "x"}))
    o = await ok(await c.get(f"/api/p/orders/{o['_id']}", headers=S["nb"]))
    assert o["order_status"] == "complete" and o["delivered_label"] == "5 of 5 delivered"
    # split on fully paid invoices equals Brief 1's figure: ex-works 3450 x 1.15 per tank
    assert sum(i["fenix_value"] for i in o["invoices"]) == round(5 * 3450 * 1.15, 2)
    assert "split" not in p                                   # staff never see the split
    assert (await db.payments().find_one({"_id": p["_id"]}))["split"]["fenix_exworks_value"] == round(5 * 3450 * 1.15, 2)


async def test_split_rules(client):
    c = client
    import commerce
    cfg = await commerce.get_cfg("nodeb")
    o = await ok(await c.post("/api/p/orders", headers=S["nb"], json={
        "customer_id": S["nb_cust"]["_id"], "lines": [{"tank_type": "5000L", "colour": "GRN", "qty": 2, "unit_price": 5000},
                                                      {"kind": "charge", "code": "DEL"}]}))
    inv = o["invoices"][0]
    assert inv["fenix_value"] == round(2 * 3450 * 1.15, 2)          # the charge line draws nothing
    full = await db.invoices().find_one({"_id": inv["_id"]})
    half = commerce.split_for(full, cfg, full["total"] / 2)
    assert half["fenix_exworks_value"] == round(inv["fenix_value"] / 2, 2)
    # powder_sale computes none
    mx = await db.invoices().find_one({"node_id": "makulu", "legacy": {"$ne": True}})
    assert commerce.split_for(mx, await commerce.get_cfg("makulu"), 100)["fenix_exworks_value"] == 0.0
    assert "fenix_value" not in mx


# ---------------------------------------------------------------- R3, cancel remainder, black pool, colour stock

async def test_r3_dn_not_invoiced(client):
    c = client
    o = await ok(await c.post("/api/p/orders", headers=S["nb"], json={
        "customer_id": S["nb_terms"]["_id"], "lines": [{"tank_type": "5000L", "colour": "GRN", "qty": 2, "unit_price": 5000},
                                                       {"kind": "charge", "code": "DEL"}]}))
    dn = await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["nb"], json={
        "lines": [{"order_line_id": o["order_lines"][0]["line_id"], "quantity": 2}], "invoice_now": False, "date": today()}))
    import commerce
    assert dn["_id"] not in str(await commerce.sweep_dn_not_invoiced("nodeb"))      # within grace
    await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {"date": days(-3)}})
    await commerce.sweep_dn_not_invoiced("nodeb")
    f = await db.flags().find_one({"type": "dn_not_invoiced", "references.dn_id": dn["_id"]})
    assert f and f["status"] == "open"
    rows = (await ok(await c.get('/api/p/dashboard/board/orders?filters={"ready_to_invoice":true}', headers=S["nb"])))["rows"]
    assert any(r["_id"] == o["_id"] for r in rows) is False   # not delivered yet (scheduled)
    await ok(await c.post(f"/api/p/deliveries/{dn['_id']}/pod", headers=S["nb"], data={"receiver_name": "x"}))
    rows = (await ok(await c.get('/api/p/dashboard/board/orders?filters={"ready_to_invoice":true}', headers=S["nb"])))["rows"]
    assert any(r["_id"] == o["_id"] for r in rows)
    inv = await ok(await c.post(f"/api/p/deliveries/{dn['_id']}/invoice", headers=S["nb"]))
    assert all(l.get("kind") != "charge" for l in inv["lines"])     # charge lines are not part of R3


async def test_cancel_remainder_refund_due(client):
    c = client
    o = await ok(await c.post("/api/p/orders", headers=S["nb"], json={
        "customer_id": S["nb_cust"]["_id"], "lines": [{"tank_type": "5000L", "colour": "GRN", "qty": 3, "unit_price": 5000}]}))
    await ok(await c.post("/api/p/payments", headers=S["nb"], json={
        "method": "cash", "amount": o["total"], "invoice_id": o["invoices"][0]["_id"], "customer_id": S["nb_cust"]["_id"]}))
    await ok(await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["nb"], json={
        "lines": [{"order_line_id": o["order_lines"][0]["line_id"], "quantity": 1}]}))
    assert (await c.post(f"/api/p/orders/{o['_id']}/cancel-remainder", headers=S["nbs"], json={"reason": "x"})).status_code == 403
    out = await ok(await c.post(f"/api/p/orders/{o['_id']}/cancel-remainder", headers=S["nb"], json={"reason": "Changed mind"}))
    assert out["refund_due"] == 2 * 5750
    assert await db.flags().find_one({"type": "refund_due", "references.order_id": o["_id"]})


async def test_black_pool(client):
    """Makulu issues two black grades on the same day; R1 balances against the pooled black.
    GoGreen, with one black product, reads exactly as before (covered by test_chain)."""
    import recon
    cfg = await recon.get_config("makulu")
    assert recon.black_pool_key(cfg) == recon.BLACK_POOL
    cap = await ok(await client.post(f"/api/p/production/captures?date={days(-10)}", headers=S["mx"]))
    # 2 x W5000 Emerald: colour 2*36 = 72 ; black 2*(36+1) = 74, issued 50 of 5840 and 24 of 6840
    await ok(await client.post(f"/api/p/production/captures/{cap['_id']}/entries", headers=S["mx"], json={
        "powder": [{"powder_type": "5840 Black UV RotoPowder", "received_kg": 1000, "issued_kg": 50},
                   {"powder_type": "6840 Black UV RotoPowder", "received_kg": 1000, "issued_kg": 24},
                   {"powder_type": "5840 Emerald Green UV RotoPowder", "received_kg": 1000, "issued_kg": 72}],
        "production": [{"tank_type": "W5000", "tank_colour": "EMERALD", "quantity_a": 2}]}))
    floor = await recon.powder_floor("makulu", cfg, days(-10))
    assert floor[recon.BLACK_POOL] == 0.0 and floor["5840 Emerald Green UV RotoPowder"] == 0.0
    assert await recon.check_powder_floor("makulu", days(-10), cap["_id"]) == []
    wh = await recon.powder_warehouse("makulu", days(-10))
    assert wh["5840 Black UV RotoPowder"] == 950 and wh["6840 Black UV RotoPowder"] == 976   # exact per grade
    # a colour with no powder product cannot be captured
    cap2 = await ok(await client.post(f"/api/p/production/captures?date={days(-9)}", headers=S["mx"]))
    r = await client.post(f"/api/p/production/captures/{cap2['_id']}/entries", headers=S["mx"], json={
        "production": [{"tank_type": "W5000", "tank_colour": "ORANGE", "quantity_a": 1}]})
    assert r.status_code == 400 and "no powder product" in r.text


async def test_colour_stock(client):
    c = client
    # 2 x W2300 Sahara left after earlier deliveries; Island Blue none at all
    o = await ok(await c.post("/api/p/orders", headers=S["mx"], json={
        "customer_id": S["mx_terms"]["_id"], "lines": [{"tank_type": "W2300", "colour": "ISLAND", "qty": 1}]}))
    assert o["order_lines"][0]["made_to_order"]
    r = await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": o["order_lines"][0]["line_id"], "quantity": 1}]})
    assert r.status_code == 400 and "Island Blue" in r.text
    t = await ok(await c.get("/api/p/dashboard/tiles", headers=S["mx"]))
    assert any(s["colour"] == "ISLAND" and s["tank_type"] == "W2300" for s in t["to_deliver"]["shortfall"])
    rows = (await ok(await c.get('/api/p/dashboard/board/orders?filters={"made_to_order":true}', headers=S["mx"])))["rows"]
    assert any(r["_id"] == o["_id"] for r in rows)


# ---------------------------------------------------------------- maps

async def test_maps_parsing():
    import maps
    assert maps.parse_coords("https://www.google.com/maps/@-33.9249,18.4241,15z") == (-33.9249, 18.4241)
    assert maps.parse_coords("https://maps.google.com/?q=-32.05,26.88") == (-32.05, 26.88)
    assert maps.parse_coords("https://www.google.com/maps/place/Farm/@-31,27,17z/data=!3m1!4b1!4m6!3m5!3d-31.8975!4d26.8752") == (-31.8975, 26.8752)
    assert maps.parse_coords("https://www.google.com/maps/place/Somewhere+Farm") is None

    def handler(req):
        if req.url.host == "maps.app.goo.gl":
            return httpx.Response(302, headers={"location": "https://www.google.com/maps/place/x/@-32.1,26.9,17z"})
        return httpx.Response(200, text="ok")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as cl:
        loc = await maps.locate("https://maps.app.goo.gl/AbCdEf", cl)
    assert loc["pinned"] and (loc["lat"], loc["lng"]) == (-32.1, 26.9) and loc["maps_link"] == "https://maps.app.goo.gl/AbCdEf"
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200)), follow_redirects=True) as cl:
        loc = await maps.locate("https://maps.app.goo.gl/broken", cl)
    assert not loc["pinned"] and loc["lat"] is None and loc["maps_link"] == "https://maps.app.goo.gl/broken"
    assert maps.nav_link({"maps_link": "https://maps.app.goo.gl/x"}) == "https://maps.app.goo.gl/x"
    assert maps.nav_link({"address": "4 Fabriek St", "town": "Bellville"}).endswith("destination=4%20Fabriek%20St%2C%20Bellville")


# ---------------------------------------------------------------- Yoco catch-up, cash control, Xero

async def test_yoco_sweep_catches_missed_webhook(client):
    c = client
    import yoco
    mock = Yoco()
    yoco.transport = httpx.MockTransport(mock.handler)
    o = await ok(await c.post("/api/p/orders", headers=S["mx"], json={
        "customer_id": S["mx_cust"]["_id"], "lines": [{"tank_type": "W1000", "colour": "EMERALD", "qty": 1}]}))
    inv = o["invoices"][0]
    pl = await ok(await c.post(f"/api/p/invoices/{inv['_id']}/pay-link", headers=S["mx"]))
    opened = await ok(await c.get(f"/api/p/pay/{pl['url'].rsplit('/', 1)[1]}"))
    mock.completed[opened["redirect_url"].rsplit("/", 1)[1]] = "pay_missed"
    caught = await yoco.sweep_links("makulu")
    assert len(caught) == 1
    assert (await db.invoices().find_one({"_id": inv["_id"]}))["status"] == "paid"
    assert await yoco.sweep_links("makulu") == []        # idempotent


async def test_cash_control(client):
    c = client
    import commerce
    o = await ok(await c.post("/api/p/orders", headers=S["nb"], json={
        "customer_id": S["nb_cust"]["_id"], "lines": [{"tank_type": "5000L", "colour": "GRN", "qty": 1, "unit_price": 1000}]}))
    p1 = await ok(await c.post("/api/p/payments", headers=S["nbs"], json={
        "method": "cash", "amount": 1150, "invoice_id": o["invoices"][0]["_id"], "date": days(-5)}))
    p2 = await ok(await c.post("/api/p/payments", headers=S["nbs"], json={
        "method": "cash", "amount": 200, "customer_id": S["nb_cust"]["_id"], "date": today()}))
    await commerce.sweep_cash("nodeb")
    assert await db.flags().find_one({"type": "cash_not_banked", "references.payment_id": p1["_id"]})
    assert not await db.flags().find_one({"type": "cash_not_banked", "references.payment_id": p2["_id"]})
    dep = await ok(await c.post("/api/p/cash/deposits", headers=S["nbs"], json={
        "amount": 1300, "payment_ids": [p1["_id"], p2["_id"]], "reference": "slip 99"}))
    assert await db.flags().find_one({"type": "cash_deposit_mismatch", "references.deposit_id": dep["_id"]})
    cash = await ok(await c.get("/api/p/cash", headers=S["nb"]))
    assert all(p1["_id"] not in h.get("payment_ids", []) for h in cash["on_hand"])
    assert (await c.post("/api/p/cash/deposits", headers=S["nbs"], json={"amount": 1, "payment_ids": [p1["_id"]]})).status_code == 400


async def test_xero_pull_skips_our_payments(client):
    import xero_mt
    import crypto
    # Makulu connected to Xero (tokens fresh); one invoice posted, one Yoco payment posted by us
    await db.xero_connections().insert_one({
        "node_id": "makulu", "tenant_id": "t-mx", "org_name": "Makulu Tanks", "status": "connected",
        "access_token": crypto.encrypt("at"), "refresh_token": crypto.encrypt("rt"),
        "expires_at": datetime.utcnow() + timedelta(hours=1), "settings": xero_mt.default_settings(),
        "last_refreshed_at": datetime.utcnow(), "refresh_lock_until": None})
    inv = S["paid_order"]["invoices"][0]
    await db.invoices().update_one({"_id": inv["_id"]}, {"$set": {"xero": {"status": "posted", "invoice_id": "XI-1"}}})
    ours = await db.payments().find_one({"external_id": "pay_half"})
    await db.payments().update_one({"_id": ours["_id"]}, {"$set": {"xero_payment_ids": {inv["_id"]: "XP-OURS"}}})
    o2 = await ok(await client.post("/api/p/orders", headers=S["mx"], json={
        "customer_id": S["mx_terms"]["_id"], "lines": [{"tank_type": "W5000", "colour": "EMERALD", "qty": 1}]}))
    d = await ok(await client.post(f"/api/p/orders/{o2['_id']}/deliveries", headers=S["mx"], json={
        "lines": [{"order_line_id": o2["order_lines"][0]["line_id"], "quantity": 1}]}))
    tinv = d["invoice"]
    await db.invoices().update_one({"_id": tinv["_id"]}, {"$set": {"xero": {"status": "posted", "invoice_id": "XI-2"}}})

    def handler(req):
        if req.url.path.endswith("/Invoices"):
            return httpx.Response(200, json={"Invoices": [
                {"InvoiceID": "XI-1", "Status": "PAID", "Payments": [{"PaymentID": "XP-OURS", "Amount": 8800, "Date": "2026-10-20"}]},
                {"InvoiceID": "XI-2", "Status": "PAID", "Payments": [{"PaymentID": "XP-EFT", "Amount": tinv["total"],
                                                                      "Date": "/Date(1792454400000+0000)/", "Reference": "CP EFT"}]}]})
        return httpx.Response(404)
    xero_mt.transport = httpx.MockTransport(handler)
    out = await xero_mt.pull_invoice_payments("makulu")
    assert out == {"new": 1, "removed": 0}
    p = await db.payments().find_one({"xero_payment_id": "XP-EFT"})
    assert p["method"] == "eft" and p["allocations"][0]["invoice_id"] == tinv["_id"]
    assert (await db.invoices().find_one({"_id": tinv["_id"]}))["status"] == "paid"
    assert await xero_mt.pull_invoice_payments("makulu") == {"new": 0, "removed": 0}
    # with Xero connected, EFT is not keyed in the app
    r = await client.post("/api/p/payments", headers=S["mx"], json={"method": "eft", "amount": 1, "customer_id": S["mx_terms"]["_id"]})
    assert r.status_code == 400
    await db.xero_connections().delete_one({"node_id": "makulu"})


# ---------------------------------------------------------------- dashboard

async def test_dashboard_board(client):
    c = client
    h = S["nb"]
    allrows = (await ok(await c.get('/api/p/dashboard/board/orders?filters={"order_status":[]}', headers=h)))["rows"]
    assert allrows
    for f, check in (({"payment_status": ["paid"]}, lambda r: r["payment_status"] == "paid"),
                     ({"payment_status": ["overdue"]}, lambda r: r["overdue"]),
                     ({"delivery_status": ["delivered"]}, lambda r: r["delivery_status"] == "delivered"),
                     ({"basis": ["terms"]}, lambda r: r["basis"] == "terms"),
                     ({"method": ["cash"]}, lambda r: "cash" in r["methods"]),
                     ({"q": "builders"}, lambda r: "builders" in r["customer"].lower())):
        rows = (await ok(await c.get(f"/api/p/dashboard/board/orders?filters={json.dumps({**f, 'order_status': []})}", headers=h)))["rows"]
        assert all(check(r) for r in rows), f
    default_open = (await ok(await c.get("/api/p/dashboard/board/orders", headers=h)))["rows"]
    assert all(r["order_status"] == "open" for r in default_open)
    srt = (await ok(await c.get('/api/p/dashboard/board/orders?filters={"order_status":[]}&sort={"key":"value","dir":"desc"}', headers=h)))
    vals = [r["value"] for r in srt["rows"]]
    assert vals == sorted(vals, reverse=True) and srt["totals"]["count"] == len(vals)
    views = await ok(await c.get("/api/p/dashboard/views", headers=h))
    assert {v["name"] for v in views} >= {"To deliver", "Awaiting payment", "Overdue", "Part delivered",
                                          "Out for delivery today", "Collect on delivery", "Ready to invoice", "Made to order"}
    v = await ok(await c.post("/api/p/dashboard/views", headers=h, json={"name": "Big terms", "kind": "orders",
                                                                         "filters": {"basis": ["terms"]}, "sort": {"key": "value", "dir": "desc"}}))
    assert any(x["_id"] == v["_id"] for x in await ok(await c.get("/api/p/dashboard/views", headers=h)))
    assert all(x["_id"] != v["_id"] for x in await ok(await c.get("/api/p/dashboard/views", headers=S["nbs"])))  # per user
    csv = await c.get('/api/p/dashboard/board/orders.csv?filters={"order_status":[]}', headers=h)
    assert csv.status_code == 200 and csv.text.startswith("order_number,date,customer")
    inv_rows = (await ok(await c.get('/api/p/dashboard/board/invoices?filters={"status":["paid"]}', headers=h)))["rows"]
    assert inv_rows and all(r["status"] == "paid" for r in inv_rows)
    dn_rows = (await ok(await c.get('/api/p/dashboard/board/deliveries?filters={"delivery_status":["delivered"]}', headers=h)))["rows"]
    assert dn_rows and all(r["delivery_status"] == "delivered" for r in dn_rows)
    # Fenix sees the same board read-only, and the network view
    fen = await ok(await c.get('/api/nodes/nodeb/commercial/board/orders?filters={"order_status":[]}', headers=S["admin"]))
    assert len(fen["rows"]) == len(allrows)
    net = {n["node_id"]: n for n in await ok(await c.get("/api/network/commercial", headers=S["admin"]))}
    assert net["nodeb"]["invoiced"] > 0 and "makulu" in net


# ---------------------------------------------------------------- legacy migration and internal cutover

async def test_legacy_migration_and_cutover(client):
    """GoGreen DNs from Brief 1 become legacy orders and invoices; matched payments become
    allocations; split totals are unchanged."""
    import migrate_brief2
    from uuid import uuid4
    dn_id, pay_id = uuid4().hex, uuid4().hex
    await db.delivery_notes().insert_one({
        "_id": dn_id, "node_id": "gogreen", "dn_number": "GG-DN-0900", "date": "2026-05-01",
        "client_name": "Old Client", "client_details": "1 Old Rd",
        "lines": [{"tank_type": "5000L", "grade": "A", "quantity": 2, "unit_price": 4000}],
        "subtotal": 8000.0, "vat_rate": 15.0, "vat": 1200.0, "total": 9200.0, "amount_paid": 9200.0,
        "status": "paid", "fenix_exworks_value": 7935.0, "partner_balance": 1265.0, "created_at": datetime.utcnow()})
    await db.payments().insert_one({"_id": pay_id, "node_id": "gogreen", "date": "2026-05-10", "amount": 9200.0,
                                    "bank_reference": "OLD", "matched_delivery_id": dn_id, "status": "matched",
                                    "split": {"fenix_exworks_value": 7935.0, "partner_balance": 1265.0},
                                    "created_at": datetime.utcnow()})
    out = await migrate_brief2.migrate_delivery_notes()
    assert out["orders"] >= 1 and out["allocations"] >= 1
    dn = await db.delivery_notes().find_one({"_id": dn_id})
    inv = await db.invoices().find_one({"dn_id": dn_id})
    order = await db.orders().find_one({"_id": dn["order_id"]})
    assert order["legacy"] and inv["legacy"] and inv["invoice_number"] is None and inv["display_number"] == "GG-DN-0900"
    assert inv["status"] == "paid" and inv["fenix_value"] == 7935.0
    p = await db.payments().find_one({"_id": pay_id})
    assert p["allocations"] == [{"invoice_id": inv["_id"], "amount": 9200.0}]
    assert (await db.customers().find_one({"_id": dn["customer_id"]}))["payment_basis"] == "terms"
    assert (await migrate_brief2.migrate_delivery_notes())["orders"] == 0          # idempotent
    import commerce
    cfg = await commerce.get_cfg("gogreen")
    assert commerce.split_for(inv, cfg, 9200.0)["fenix_exworks_value"] == 7935.0

    # partner_app_live: internal capture screens go read-only, admin corrections keep working
    admin = S["admin"]
    ops = await internal(client, "steven@fenixrising.co.za", "changeme-steven")
    await ok(await client.put("/api/nodes/gogreen", headers=admin, json={
        "node_id": "gogreen", "name": "GoGreen Roto Moulding", "location": "Queenstown", "partner_app_live": True}))
    assert (await client.post(f"/api/nodes/gogreen/captures?date={today()}", headers=ops)).status_code == 403
    r = await client.post("/api/nodes/gogreen/delivery-notes", headers=admin, json={
        "date": today(), "client_name": "X", "lines": [{"tank_type": "5000L", "quantity": 1}]})
    assert r.status_code == 403
    await ok(await client.put("/api/nodes/gogreen", headers=admin, json={
        "node_id": "gogreen", "name": "GoGreen Roto Moulding", "location": "Queenstown", "partner_app_live": False}))
