"""Yoco online payments, one merchant account per node. Money goes to the partner, never
through Fenix.

Which API: the Checkout API (POST https://payments.yoco.com/api/checkouts, Bearer secret key,
payment.succeeded webhook). Yoco does not document how long a checkout's redirectUrl stays
valid, so the customer is never sent one. They get the node's own stable link,
https://<slug>.vezubunye.com/pay/<token>; opening it creates a checkout for the invoice's
balance at that moment (or reuses a fresh one) and redirects. A link sent days ahead keeps
working, and a part payment by another method changes what the next tap charges. The
Payment Links API was not used: it needs separate scoped OAuth tokens and its expiry and
webhook format are not published.

Webhooks follow Standard Webhooks: HMAC-SHA256 over "{webhook-id}.{webhook-timestamp}.{raw body}"
keyed with the base64 part of the `whsec_` secret, compared against each "v1,<sig>" in the
webhook-signature header, with a 3-minute timestamp tolerance. Idempotent on the Yoco payment id.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from datetime import datetime
from uuid import uuid4

import httpx
from fastapi import HTTPException

import audit
import commerce
import crypto
import db
import partner_pdf
from corrections import ACTIVE

API = os.getenv("YOCO_API_BASE", "https://payments.yoco.com/api")
WEBHOOK_BASE = os.getenv("PUBLIC_API_URL", "https://api.vezubunye.com")
TOLERANCE_S = 180
YOCO_ACTOR = {"email": "yoco:webhook", "role": "system", "employer": None}

# Tests swap this for an httpx.MockTransport.
transport: httpx.AsyncBaseTransport | None = None


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=API, timeout=20, transport=transport)


async def _secret(node_id: str) -> dict | None:
    return await db.secrets().find_one({"node_id": node_id, "kind": "yoco"})


async def configured(node_id: str) -> bool:
    return bool(await _secret(node_id))


async def online(node_id: str) -> bool:
    """Card payment links are offered only once the business confirms Yoco takes online
    payments for it (Settings > Yoco). Off by default: until Yoco activates online payments,
    its checkout tells the customer the business is not ready, so we send EFT details instead."""
    s = await _secret(node_id)
    return bool(s and s.get("online"))


async def set_online(node_id: str, on: bool, user: dict) -> dict:
    if not await _secret(node_id):
        raise HTTPException(400, "Connect Yoco first")
    await db.secrets().update_one({"node_id": node_id, "kind": "yoco"}, {"$set": {"online": bool(on)}})
    await audit.log(user, node_id, "card_links_on" if on else "card_links_off", "yoco", None, after={"online": bool(on)})
    return await status(node_id)


async def status(node_id: str) -> dict:
    s = await _secret(node_id)
    if not s:
        return {"connected": False, "online": False}
    return {"connected": True, "mode": s.get("mode"), "webhook_registered": bool(s.get("webhook_id")),
            "key_hint": s.get("key_hint"), "updated_at": s.get("updated_at"), "online": bool(s.get("online"))}


async def save_key(node_id: str, key: str, user: dict) -> dict:
    key = (key or "").strip()
    if not (key.startswith("sk_test_") or key.startswith("sk_live_")):
        raise HTTPException(400, "A Yoco secret key starts sk_test_ or sk_live_")
    url = f"{WEBHOOK_BASE}/api/p/yoco/webhook/{node_id}"
    async with _client() as c:
        r = await c.post("/webhooks", headers={"Authorization": f"Bearer {key}"},
                         json={"name": f"node-{node_id}-{uuid4().hex[:6]}", "url": url})
    if r.status_code >= 300:
        raise HTTPException(400, f"Yoco rejected the key or the webhook registration ({r.status_code})")
    body = r.json()
    doc = {"node_id": node_id, "kind": "yoco", "secret_key_enc": crypto.encrypt(key),
           "mode": "live" if key.startswith("sk_live_") else "test", "key_hint": key[:8] + "…" + key[-4:],
           "webhook_id": body.get("id"), "webhook_secret_enc": crypto.encrypt(body.get("secret", "")),
           "webhook_url": url, "updated_at": datetime.utcnow(), "updated_by": user["email"],
           "online": bool((await _secret(node_id) or {}).get("online"))}
    await db.secrets().replace_one({"node_id": node_id, "kind": "yoco"}, doc, upsert=True)
    await audit.log(user, node_id, "connect", "yoco", None, after={"mode": doc["mode"], "webhook_url": url})
    return await status(node_id)


async def disconnect(node_id: str, user: dict):
    await db.secrets().delete_one({"node_id": node_id, "kind": "yoco"})
    await audit.log(user, node_id, "disconnect", "yoco", None)


# ---------- pay links ---------- #

# Pay links on the partner's own website (verified with Yoco), proxied to the partner app. A
# node's own pay_host wins. Makulu: www.makulutanks.co.za/pay/* (9 Nov 2026), because Yoco only
# verifies public sites and <slug>.vezubunye.com opens on the login page.
PAY_HOSTS = {"makx": "https://www.makulutanks.co.za"}


def public_url(node: dict, token: str) -> str:
    host = node.get("pay_host") or PAY_HOSTS.get(node.get("slug") or "") or \
        (f"https://{node['slug']}.vezubunye.com" if node.get("slug") else "")
    return f"{host}/pay/{token}"


async def pay_link(node: dict, invoice: dict, user: dict, dn_id: str | None = None, mark_sent: bool = False) -> dict:
    """The stable link for an open invoice. Reused while the invoice is open."""
    if not await configured(node["node_id"]):
        raise HTTPException(400, "Yoco is not connected for this business")
    if not await online(node["node_id"]):
        raise HTTPException(400, "Card payment links are switched off (Settings > Yoco). Send the EFT details instead.")
    if invoice.get("void") or invoice.get("balance", invoice["total"]) <= commerce.PAY_TOL:
        raise HTTPException(400, "Nothing due on this invoice")
    pl = await db.pay_links().find_one({"invoice_id": invoice["_id"], "status": {"$in": ["open", "sent"]},
                                        **({"dn_id": dn_id} if dn_id else {})})
    if not pl:
        pl = {"_id": uuid4().hex, "node_id": node["node_id"], "invoice_id": invoice["_id"], "dn_id": dn_id,
              "token": commerce.driver_token(), "amount": invoice.get("balance", invoice["total"]),
              "status": "open", "sent_at": None, "checkouts": [], "created_by": user["email"],
              "created_at": datetime.utcnow(), "date": commerce.today()}
        await db.pay_links().insert_one(pl)
    upd = {"amount": invoice.get("balance", invoice["total"])}
    if mark_sent:
        upd.update(status="sent", sent_at=pl.get("sent_at") or datetime.utcnow())
    await db.pay_links().update_one({"_id": pl["_id"]}, {"$set": upd})
    pl.update(upd)
    return {**pl, "url": public_url(node, pl["token"])}


async def quote_pay_link(node: dict, quote: dict, user: dict, mark_sent: bool = False) -> dict | None:
    """The stable card link for an open proforma, or None when Yoco is off or nothing is due.
    Card money paid here is held on the proforma until its tax invoice exists."""
    if not await online(node["node_id"]):
        return None
    if quote["status"] not in ("draft", "sent") or not commerce.is_proforma(await commerce.get_cfg(node["node_id"])):
        return None
    balance = await commerce.proforma_balance(quote)
    if balance <= commerce.PAY_TOL:
        return None
    pl = await db.pay_links().find_one({"quote_id": quote["_id"], "invoice_id": None,
                                        "status": {"$in": ["open", "sent"]}})
    if not pl:
        pl = {"_id": uuid4().hex, "node_id": node["node_id"], "invoice_id": None, "quote_id": quote["_id"],
              "dn_id": None, "token": commerce.driver_token(), "amount": balance, "status": "open",
              "sent_at": None, "checkouts": [], "created_by": user["email"], "created_at": datetime.utcnow(),
              "date": commerce.today()}
        await db.pay_links().insert_one(pl)
    upd = {"amount": balance}
    if mark_sent:
        upd.update(status="sent", sent_at=pl.get("sent_at") or datetime.utcnow())
    await db.pay_links().update_one({"_id": pl["_id"]}, {"$set": upd})
    pl.update(upd)
    return {**pl, "url": public_url(node, pl["token"])}


async def existing_quote_link(node: dict, quote: dict) -> str | None:
    if not await online(node["node_id"]):
        return None
    pl = await db.pay_links().find_one({"quote_id": quote["_id"], "invoice_id": None,
                                        "status": {"$in": ["open", "sent"]}})
    return public_url(node, pl["token"]) if pl else None


async def _link_target(pl: dict) -> dict:
    """What a link charges right now: its invoice, or its proforma until the invoice exists."""
    if pl.get("invoice_id"):
        inv = await db.invoices().find_one({"_id": pl["invoice_id"]})
        if not inv:
            return {"number": "", "total": None, "balance": 0.0, "closed": True, "meta": {}}
        return {"number": inv["display_number"], "total": inv["total"],
                "balance": inv.get("balance", inv["total"]), "closed": bool(inv.get("void")),
                "meta": {"invoice_id": inv["_id"]}}
    q = await db.quotes().find_one({"_id": pl.get("quote_id")})
    if not q:
        return {"number": "", "total": None, "balance": 0.0, "closed": True, "meta": {}}
    return {"number": q["quote_number"], "total": q["total"], "balance": await commerce.proforma_balance(q),
            "closed": q["status"] not in ("draft", "sent"), "meta": {"quote_id": q["_id"]}}


async def open_link(token: str) -> dict:
    """Customer tapped the link: create a checkout for the balance due now, return where to go."""
    pl = await db.pay_links().find_one({"token": token})
    if not pl:
        raise HTTPException(404, "This payment link is not valid")
    tgt = await _link_target(pl)
    node = await commerce.get_node(pl["node_id"])
    base = {"business": (node.get("branding") or {}).get("display_name") or node["name"],
            "invoice": tgt["number"], "total": tgt["total"]}
    if tgt["closed"] or pl["status"] in ("paid", "cancelled"):
        return {**base, "state": "closed", "amount": 0.0}
    balance = tgt["balance"]
    if balance <= commerce.PAY_TOL:
        await db.pay_links().update_one({"_id": pl["_id"]}, {"$set": {"status": "paid"}})
        return {**base, "state": "paid", "amount": 0.0}
    # reuse a checkout made in the last 20 minutes for the same amount
    for co in reversed(pl.get("checkouts") or []):
        if co["amount"] == balance and time.time() - co["ts"] < 1200 and co.get("redirect_url"):
            return {**base, "state": "open", "amount": balance, "redirect_url": co["redirect_url"]}
    s = await _secret(pl["node_id"])
    eft = {**base, "state": "eft", "amount": balance, "reference": tgt["number"],
           "bank": partner_pdf._bank_lines(node, tgt["number"])}
    if not s or not s.get("online"):
        return eft
    done = f"{public_url(node, token)}?done=1"
    async with _client() as c:
        r = await c.post("/checkouts", headers={
            "Authorization": f"Bearer {crypto.decrypt(s['secret_key_enc'])}",
            "Idempotency-Key": f"{pl['_id']}-{int(balance * 100)}-{int(time.time() // 600)}"},
            json={"amount": int(round(balance * 100)), "currency": "ZAR",
                  "successUrl": done, "cancelUrl": public_url(node, token), "failureUrl": public_url(node, token),
                  "externalId": tgt["number"],
                  "metadata": {"node_id": pl["node_id"], **tgt["meta"], "pay_link_id": pl["_id"],
                               "dn_id": pl.get("dn_id") or ""}})
    if r.status_code >= 300:
        return {**eft, "card_error": True}
    co = r.json()
    await db.pay_links().update_one({"_id": pl["_id"]}, {"$push": {"checkouts": {
        "id": co.get("id"), "amount": balance, "ts": time.time(), "redirect_url": co.get("redirectUrl")}}})
    return {**base, "state": "open", "amount": balance, "redirect_url": co.get("redirectUrl")}


# ---------- webhook ---------- #

def verify(secret: str, headers: dict, body: bytes, now: float | None = None) -> bool:
    wid = headers.get("webhook-id")
    ts = headers.get("webhook-timestamp")
    sig = headers.get("webhook-signature")
    if not (wid and ts and sig and secret):
        return False
    try:
        if abs((now or time.time()) - int(ts)) > TOLERANCE_S:
            return False
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except Exception:  # noqa: BLE001
        return False
    expected = base64.b64encode(hmac.new(key, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()
    for part in sig.split():
        v = part.split(",", 1)[1] if "," in part else part
        if hmac.compare_digest(v, expected):
            return True
    return False


def sign(secret: str, wid: str, ts: int, body: bytes) -> str:
    """Used by tests and the local simulator."""
    key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    return "v1," + base64.b64encode(hmac.new(key, f"{wid}.{ts}.".encode() + body, hashlib.sha256).digest()).decode()


async def handle_webhook(node_id: str, headers: dict, body: bytes) -> dict:
    s = await _secret(node_id)
    if not s or not verify(crypto.decrypt(s["webhook_secret_enc"]), headers, body):
        raise HTTPException(401, "Bad signature")
    event = json.loads(body or b"{}")
    if event.get("type") != "payment.succeeded":
        return {"ignored": event.get("type")}
    payload = event.get("payload") or {}
    meta = payload.get("metadata") or {}
    return await record_yoco_payment(node_id, payload.get("id"), (payload.get("amount") or 0) / 100.0, meta)


async def record_yoco_payment(node_id: str, yoco_payment_id: str, amount: float, meta: dict) -> dict:
    if not yoco_payment_id:
        raise HTTPException(400, "No payment id")
    existing = await db.payments().find_one({"node_id": node_id, "external_id": yoco_payment_id})
    if existing:
        return {"duplicate": True, "payment_id": existing["_id"]}
    if not meta.get("invoice_id") and meta.get("quote_id"):
        return await _record_on_proforma(node_id, yoco_payment_id, amount, meta)
    inv = await db.invoices().find_one({"_id": meta.get("invoice_id"), "node_id": node_id})
    if not inv:
        raise HTTPException(400, "Invoice in metadata not on this node")
    alloc = [{"invoice_id": inv["_id"], "amount": round(min(amount, max(inv.get("balance", inv["total"]), 0)), 2)}] \
        if not inv.get("void") else []
    p = await commerce.record_payment(node_id, {
        "amount": amount, "method": "yoco", "source": "link", "reference": yoco_payment_id,
        "external_id": yoco_payment_id, "customer_id": inv.get("customer_id"), "date": commerce.today()},
        YOCO_ACTOR, allocations=alloc)
    inv2 = await db.invoices().find_one({"_id": inv["_id"]})
    if inv2 and inv2.get("balance", 0) <= commerce.PAY_TOL:
        await db.pay_links().update_many({"invoice_id": inv["_id"], "status": {"$in": ["open", "sent"]}},
                                         {"$set": {"status": "paid", "paid_at": datetime.utcnow()}})
    if meta.get("dn_id"):
        dn = await db.delivery_notes().find_one({"_id": meta["dn_id"], **ACTIVE})
        if dn and dn.get("collect_amount") is not None and not dn.get("collection"):
            coll = {"method": "yoco_link", "amount": amount, "payment_id": p["_id"], "at": datetime.utcnow(),
                    "by": YOCO_ACTOR["email"]}
            await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {"collection": coll}})
    await commerce.refresh_order_status(inv["order_id"])
    try:
        import xero_mt
        await xero_mt.queue_payment(node_id, p["_id"])
    except Exception:  # noqa: BLE001  (posting retries from the queue; never lose the payment)
        pass
    return {"payment_id": p["_id"], "allocated": alloc}


async def _record_on_proforma(node_id: str, yoco_payment_id: str, amount: float, meta: dict) -> dict:
    """Card money on a proforma. Before the tax invoice exists it is held on the proforma; paid
    in full, the proforma becomes its order and tax invoice at once (Werner, 6 Nov 2026) and
    the money moves onto the invoice. If Steven already made the invoice, it goes there."""
    q = await db.quotes().find_one({"_id": meta["quote_id"], "node_id": node_id})
    if not q:
        raise HTTPException(400, "Proforma in metadata not on this node")
    if q["status"] == "accepted" and q.get("order_id"):
        inv = await db.invoices().find_one({"order_id": q["order_id"], "dn_id": None, **ACTIVE})
        if inv:
            return await record_yoco_payment(node_id, yoco_payment_id, amount,
                                             {**meta, "invoice_id": inv["_id"], "quote_id": ""})
    p = await commerce.record_payment(node_id, {
        "amount": amount, "method": "yoco", "source": "link", "reference": yoco_payment_id,
        "external_id": yoco_payment_id, "customer_id": q["customer_id"], "date": commerce.today(),
        "quote_id": q["_id"]}, YOCO_ACTOR, allocations=[], flag_unallocated=False)
    await commerce.refresh_proforma(q["_id"])
    if q["status"] not in ("draft", "sent"):
        # money arrived on a cancelled or declined proforma
        await commerce.release_held(node_id, q, q["status"])
        return {"payment_id": p["_id"], "held": False, "refund_due": True}
    out = {"payment_id": p["_id"], "held": True}
    if await commerce.proforma_balance(q) <= commerce.PAY_TOL:
        node = await commerce.get_node(node_id)
        res = await commerce.invoice_proforma(node, q, YOCO_ACTOR, None, {"pay_on_delivery": False})
        out.update(held=False, invoice_id=res["invoice_id"], order_id=res["order_id"])
        try:
            import xero_mt
            await xero_mt.post_invoice(res["invoice_id"])
            for pid in res["held_payment_ids"]:
                await xero_mt.queue_payment(node_id, pid)
        except Exception as e:  # noqa: BLE001  (stays queued; nothing is lost)
            print(f"[xero] proforma invoice or payment deferred: {e}")
    return out


async def sweep_links(node_id: str) -> list[str]:
    """Catch up on missed webhooks: ask Yoco about every open link's checkouts."""
    s = await _secret(node_id)
    if not s:
        return []
    caught = []
    key = crypto.decrypt(s["secret_key_enc"])
    async with _client() as c:
        async for pl in db.pay_links().find({"node_id": node_id, "status": {"$in": ["open", "sent"]}}):
            for co in pl.get("checkouts") or []:
                r = await c.get(f"/checkouts/{co['id']}", headers={"Authorization": f"Bearer {key}"})
                if r.status_code != 200:
                    continue
                body = r.json()
                if body.get("status") == "completed" and body.get("paymentId"):
                    res = await record_yoco_payment(node_id, body["paymentId"], co["amount"],
                                                    {"invoice_id": pl.get("invoice_id"), "quote_id": pl.get("quote_id"),
                                                     "dn_id": pl.get("dn_id")})
                    if not res.get("duplicate"):
                        caught.append(res["payment_id"])
    return caught
