"""Uploaded files go to the private Blob store, photos shrunk; PDFs untouched; records written
before the store still read. Blob is faked in memory.

Run: .venv/bin/python -m pytest test_files.py -v
"""
import base64
import hashlib
import io

import pytest

import files


def photo(w=4032, h=3024) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.effect_noise((w, h), 60).convert("RGB").save(out, format="JPEG", quality=95)
    return out.getvalue()


@pytest.fixture
def blob(monkeypatch):
    store = {}

    async def put(pathname, data, content_type):
        store[pathname] = bytes(data)

    async def get(pathname):
        return store[pathname]
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", "vercel_blob_rw_test")
    monkeypatch.setattr(files, "_blob_put", put)
    monkeypatch.setattr(files, "_blob_get", get)
    return store


async def test_photo_shrinks_and_goes_to_blob(blob):
    big = photo()
    rec = await files.store("pod/makulu", big, "MX-DN-0001.jpg", "image/jpeg")
    assert rec["storage"] == "blob" and rec["blob_pathname"].startswith("pod/makulu/")
    assert "content_b64" not in rec
    assert rec["original_size"] == len(big) and rec["size"] < len(big) / 2
    assert rec["sha256"] == hashlib.sha256(big).hexdigest()       # evidence of the upload
    from PIL import Image
    stored = await files.load(rec)
    assert max(Image.open(io.BytesIO(stored)).size) == files.SHRINK_MAX_PX


async def test_pdf_and_small_photo_stored_untouched(blob):
    pdf = b"%PDF-1.4 an invoice"
    rec = await files.store("bills/makulu", pdf, "inv.pdf", "application/pdf")
    assert await files.load(rec) == pdf and "original_size" not in rec
    small = photo(400, 300)
    if len(small) <= files.SHRINK_KEEP_BELOW:
        rec = await files.store("deposits/makulu", small, "slip.jpg", "image/jpeg")
        assert await files.load(rec) == small


async def test_without_a_store_files_stay_inline_and_old_records_read(monkeypatch):
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    rec = await files.store("pod/x", b"%PDF-1.4 x", "a.pdf", "application/pdf")
    assert rec["storage"] == "inline" and await files.load(rec) == b"%PDF-1.4 x"
    old = {"content_type": "image/jpeg", "content_b64": base64.b64encode(b"old").decode()}
    assert await files.load(old) == b"old"
    assert await files.load({"photo_b64": base64.b64encode(b"cap").decode()}, "photo_b64") == b"cap"


async def test_pod_photo_and_bill_through_the_api(blob, monkeypatch):
    """End to end on the partner app: proof of delivery and a bill scan land in Blob."""
    import os
    from httpx import ASGITransport, AsyncClient
    from mongomock_motor import AsyncMongoMockClient
    import db
    db._client = AsyncMongoMockClient()
    os.environ["DISABLE_SCHEDULER"] = "1"
    import seed
    import migrate_brief2
    await seed.seed()
    await migrate_brief2.run()
    import server
    async with AsyncClient(transport=ASGITransport(app=server.app), base_url="http://t") as c:
        r = await c.post("/api/auth/login", json={"email": "werner@fenixrising.co.za", "password": os.getenv("WERNER_PASSWORD") or "changeme-werner"})
        admin = {"Authorization": f"Bearer {r.json()['token']}"}
        await c.post("/api/nodes/makulu/partner-users", headers=admin, json={
            "email": "p@fenixrising.co.za", "name": "Pierre", "role": "partner_admin", "employer": "fenix", "password": "waterislife1"})
        r = await c.post("/api/p/auth/login", json={"slug": "makx", "email": "p@fenixrising.co.za", "password": "waterislife1"})
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        import commerce
        cap = (await c.post(f"/api/p/production/captures?date={commerce.today()}", headers=h)).json()
        await c.post(f"/api/p/production/captures/{cap['_id']}/entries", headers=h,
                     json={"production": [{"tank_type": "W1000", "tank_colour": "EMERALD", "quantity_a": 2}]})
        cust = (await c.post("/api/p/customers", headers=h, json={"name": "Walk-in"})).json()
        o = (await c.post("/api/p/orders", headers=h, json={"customer_id": cust["_id"], "lines": [
            {"tank_type": "W1000", "colour": "EMERALD", "qty": 1, "unit_price": 1800}]})).json()
        await c.post("/api/p/payments", headers=h, json={"method": "cash", "amount": 1800,
                                                         "invoice_id": o["invoices"][0]["_id"], "customer_id": cust["_id"]})
        dn = (await c.post(f"/api/p/orders/{o['_id']}/deliveries", headers=h, json={
            "lines": [{"order_line_id": o["order_lines"][0]["line_id"], "quantity": 1}]})).json()
        big = photo()
        r = await c.post(f"/api/p/deliveries/{dn['_id']}/pod", headers=h, data={"receiver_name": "Thandi"},
                         files={"file": ("pod.jpg", big, "image/jpeg")})
        assert r.status_code == 200, r.text
        got = await c.get(f"/api/p/deliveries/{dn['_id']}/pod/photo", headers=h)
        assert got.status_code == 200 and 0 < len(got.content) < len(big) / 2
        assert any(k.startswith("pod/makulu/") for k in blob)
        assert "content_b64" not in (await db.pod_files().find_one({}))
        bill = (await c.post("/api/p/bills", headers=h)).json()
        r = await c.post(f"/api/p/bills/{bill['_id']}/upload", headers=h, files={"file": ("slip.jpg", big, "image/jpeg")})
        assert r.status_code == 200, r.text
        doc = await c.get(f"/api/p/bills/files/{r.json()['_id']}", headers=h)
        assert doc.status_code == 200 and len(doc.content) < len(big) / 2
        assert any(k.startswith("bills/makulu/") for k in blob)
