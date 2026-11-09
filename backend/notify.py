"""Payment emails (Werner, 9 Nov 2026): when money comes in, the business is told by email.

Every payment counts, whatever brought it in: a Yoco card payment (webhook), a payment
captured in the app (EFT, cash, card machine), a quick sale, or one matched from Xero. A
sweep every minute picks up payments made since the business switched emails on and not yet
emailed, so nothing is sent for a payment that was rolled back and nothing is sent twice.

Sent through Resend's HTTP API (Railway blocks outbound SMTP below the Pro plan) from
payments@<the business's own domain>, which has to be verified in Resend. No RESEND_API_KEY,
no own domain, or no recipients: nothing is sent.
"""
from __future__ import annotations

import asyncio
import html
import os
from datetime import datetime

import httpx

import commerce
import db
import partner_pdf
import yoco
from corrections import ACTIVE

RESEND_URL = "https://api.resend.com/emails"
MAX_ATTEMPTS = 5
_transport = None          # tests swap in a mock transport
_task: asyncio.Task | None = None


def api_key() -> str:
    return os.getenv("RESEND_API_KEY", "")


def sender(node: dict) -> str | None:
    host = yoco.own_host(node)
    if not host:
        return None
    domain = host.split("//", 1)[-1].removeprefix("www.").strip("/")
    name = (node.get("branding") or {}).get("display_name") or node.get("name") or domain
    return f"{name} <payments@{domain}>"


async def send(node: dict, to: list[str], subject: str, text: str, html_body: str) -> dict:
    frm = sender(node)
    if not api_key():
        return {"sent": False, "error": "Email is not set up yet (no Resend key on the server)"}
    if not frm:
        return {"sent": False, "error": "This business has no own domain to send from"}
    if not to:
        return {"sent": False, "error": "No one to send to"}
    body = {"from": frm, "to": to, "subject": subject, "text": text, "html": html_body}
    reply = ((node.get("company") or {}).get("email") or "").strip()
    if reply:
        body["reply_to"] = reply
    async with httpx.AsyncClient(timeout=20, transport=_transport) as c:
        r = await c.post(RESEND_URL, headers={"Authorization": f"Bearer {api_key()}"}, json=body)
    if r.status_code >= 300:
        return {"sent": False, "error": f"Resend {r.status_code}: {r.text[:200]}"}
    return {"sent": True, "id": (r.json() or {}).get("id")}


def _method(p: dict) -> str:
    m = p.get("method")
    if m == "yoco":
        return "card machine" if p.get("source") == "machine" else "card (online)"
    return {"eft": "EFT", "cash": "cash"}.get(m, m or "payment")


async def payment_email(node: dict, p: dict) -> tuple[str, str, str]:
    """(subject, text, html) for one payment."""
    nid = node["node_id"]
    biz = (node.get("branding") or {}).get("display_name") or node["name"]
    cust = await db.customers().find_one({"_id": p.get("customer_id")}) or {}
    order = None
    docs = []
    for a in p.get("allocations") or []:
        inv = await db.invoices().find_one({"_id": a["invoice_id"]})
        if inv:
            docs.append(inv.get("display_number") or "")
            if inv.get("order_id") and not order:
                order = await db.orders().find_one({"_id": inv["order_id"]})
    q = await db.quotes().find_one({"_id": p["quote_id"]}) if p.get("quote_id") else None
    if q:
        docs.insert(0, q["quote_number"])
        if not order and q.get("order_id"):
            order = await db.orders().find_one({"_id": q["order_id"]})
    docs = [d for d in dict.fromkeys(docs) if d]
    amount = partner_pdf.money(p["amount"])
    who = cust.get("name") or "a customer"
    subject = f"Payment received: {amount} by {_method(p)} from {who}" + (f" ({docs[0]})" if docs else "")
    lines = [("Amount", amount), ("Paid by", _method(p)), ("Customer", who)]
    if docs:
        lines.append(("For", ", ".join(docs)))
    if p.get("reference"):
        lines.append(("Reference", p["reference"]))
    link = None
    if order:
        view = await commerce.order_view(nid, order)
        lines.append(("Order", order.get("order_number") or ""))
        lines.append(("Balance", partner_pdf.money(view["balance"]) if view["balance"] > commerce.PAY_TOL else "paid in full"))
        lines.append(("Next", view["next_action"]["label"]))
        link = f"{yoco.app_host(node)}/orders/{order['_id']}"
    elif q:
        lines.append(("Note", "Held on the proforma until it is paid in full or invoiced"))
        link = f"{yoco.app_host(node)}/quotes/{q['_id']}"
    by = p.get("captured_by") or ""
    lines.append(("Recorded by", "Yoco" if by.startswith("yoco") else "Xero" if by.startswith("xero") else by))
    text = "\n".join(f"{k}: {v}" for k, v in lines) + (f"\n\nOpen in MakX: {link}" if link else "") + f"\n\n{biz}"
    rows = "".join(f"<tr><td style='padding:4px 12px 4px 0;color:#667'>{html.escape(k)}</td>"
                   f"<td style='padding:4px 0;font-weight:600'>{html.escape(str(v))}</td></tr>" for k, v in lines)
    btn = (f"<p style='margin:20px 0'><a href='{link}' style='background:#11845A;color:#fff;padding:10px 16px;"
           f"border-radius:6px;text-decoration:none;font-weight:600'>Open in MakX</a></p>") if link else ""
    html_body = (f"<div style='font-family:Arial,sans-serif;font-size:15px;color:#0A252D'>"
                 f"<h2 style='margin:0 0 12px'>Payment received: {html.escape(amount)}</h2>"
                 f"<table style='border-collapse:collapse'>{rows}</table>{btn}"
                 f"<p style='color:#667;font-size:13px'>{html.escape(biz)}</p></div>")
    return subject, text, html_body


def recipients(cfg: dict) -> list[str]:
    return [e.strip() for e in cfg.get("payment_notify_emails") or [] if e and "@" in e]


async def sweep() -> dict:
    """Email every payment not yet emailed, made since the business switched emails on."""
    if not api_key():
        return {"skipped": "no key"}
    out = {}
    async for cfg in db.node_config().find({"payment_notify_emails.0": {"$exists": True}}):
        to = recipients(cfg)
        since = cfg.get("payment_notify_since")
        node = await db.nodes().find_one({"node_id": cfg["node_id"]})
        if not to or not since or not node or not sender(node):
            continue
        sent = 0
        async for p in db.payments().find({"node_id": cfg["node_id"], "created_at": {"$gte": since},
                                           "notified_at": None, "notify_attempts": {"$not": {"$gte": MAX_ATTEMPTS}},
                                           **ACTIVE}).sort("created_at", 1).limit(20):
            try:
                subject, text, body = await payment_email(node, p)
                res = await send(node, to, subject, text, body)
            except Exception as e:  # noqa: BLE001  (one bad payment never blocks the rest)
                res = {"sent": False, "error": str(e)[:200]}
            if res.get("sent"):
                await db.payments().update_one({"_id": p["_id"]}, {"$set": {"notified_at": datetime.utcnow()}})
                sent += 1
            else:
                await db.payments().update_one({"_id": p["_id"]}, {"$inc": {"notify_attempts": 1},
                                                                    "$set": {"notify_error": res.get("error")}})
        out[cfg["node_id"]] = sent
    return out


async def _loop():
    while True:
        await asyncio.sleep(60)
        try:
            await sweep()
        except Exception as e:  # noqa: BLE001
            print(f"[notify] sweep failed: {e}")


def start():
    global _task
    if os.getenv("DISABLE_SCHEDULER") or _task is not None:
        return
    try:
        _task = asyncio.get_event_loop().create_task(_loop())
    except RuntimeError:
        pass
