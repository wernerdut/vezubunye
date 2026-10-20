"""Driver API: /api/d/<token>. No login. A token is signed and scoped to one delivery note;
it expires 24 hours after the delivery is done or 7 days after the note was created, and
voiding or reissuing the note revokes it. The driver sees one delivery: customer, site,
contact, directions, the load, and the amount to collect on a pay-on-delivery note. No other
prices, no other records. Every driver action is audit-logged as driver:<phone>.
"""
from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

import commerce
import db
import maps
import partner_auth
import recon
import yoco

router = APIRouter(prefix="/api/d")

LINK_DAYS = 7
AFTER_DELIVERY_HOURS = 24


async def resolve(token: str) -> tuple[dict, dict]:
    try:
        dn_id, sig = token.split(".", 1)
    except ValueError:
        raise HTTPException(404, "This link is not valid")
    dn = await db.delivery_notes().find_one({"_id": dn_id}, {"content_b64": 0})
    if not dn or not dn.get("driver_token"):
        raise HTTPException(404, "This link is not valid")
    want = hmac.new(partner_auth.PARTNER_JWT_SECRET.encode(), f"driver:{dn['_id']}:{dn['driver_token']}".encode(),
                    hashlib.sha256).hexdigest()[:20]
    if not hmac.compare_digest(sig, want):
        raise HTTPException(404, "This link is not valid")
    if dn.get("void"):
        raise HTTPException(410, "This delivery was cancelled. Check with the office.")
    now = datetime.utcnow()
    if dn.get("delivered_at") and now > dn["delivered_at"] + timedelta(hours=AFTER_DELIVERY_HOURS):
        raise HTTPException(410, "This link has expired")
    if now > dn["created_at"] + timedelta(days=LINK_DAYS):
        raise HTTPException(410, "This link has expired")
    node = await commerce.get_node(dn["node_id"])
    return dn, node


def actor(dn: dict) -> dict:
    d = dn.get("driver") or {}
    return {"email": f"driver:{maps.intl_phone(d.get('phone', '')) or 'unknown'}", "role": "driver",
            "employer": None, "name": d.get("name", "")}


async def view(dn: dict, node: dict) -> dict:
    cfg = await commerce.get_cfg(dn["node_id"])
    site = dn.get("site") or {}
    b = node.get("branding") or {}
    order = await db.orders().find_one({"_id": dn.get("order_id")}) or {}
    pay = None
    if dn.get("collect_amount") and not dn.get("collection") and await yoco.configured(dn["node_id"]) \
            and order.get("cod_method") in ("yoco", "either", None):
        pl = await db.pay_links().find_one({"dn_id": dn["_id"], "status": {"$in": ["open", "sent"]}})
        if pl:
            pay = yoco.public_url(node, pl["token"])
    return {
        "business": {"name": b.get("display_name") or node["name"], "slug": node.get("slug"),
                     "phone": (node.get("company") or {}).get("phone")},
        "dn_number": dn["dn_number"], "customer": dn["client_name"],
        "site": {k: site.get(k) for k in ("label", "address", "town", "contact_name", "contact_phone", "directions")},
        "navigate": maps.nav_link(site), "planned_date": dn.get("planned_date"),
        "load": [{"qty": ln["quantity"], "what": commerce.line_label(cfg, {**ln, "kind": "tank"})} for ln in dn["lines"]],
        "status": dn.get("delivery_status", "scheduled"),
        "collect_amount": dn.get("collect_amount"), "cod_method": order.get("cod_method"),
        "collection": {k: (dn.get("collection") or {}).get(k) for k in ("method", "amount", "reason")} if dn.get("collection") else None,
        "pay_url": pay, "yoco": await yoco.configured(dn["node_id"]),
        "pod": bool(dn.get("pod")),
    }


@router.get("/{token}")
async def get(token: str):
    dn, node = await resolve(token)
    return await view(dn, node)


@router.post("/{token}/start")
async def start(token: str):
    dn, node = await resolve(token)
    out = await commerce.set_delivery_status(dn, "out_for_delivery", actor(dn))
    return await view(out, node)


@router.post("/{token}/failed")
async def failed(token: str, payload: dict):
    dn, node = await resolve(token)
    out = await commerce.set_delivery_status(dn, "failed", actor(dn), payload.get("reason", ""))
    return await view(out, node)


@router.post("/{token}/pay-link")
async def pay_link(token: str):
    """QR for the customer to pay by card at the site, for what is due on this delivery."""
    dn, node = await resolve(token)
    if not dn.get("collect_amount"):
        raise HTTPException(400, "Nothing to collect on this delivery")
    inv = await db.invoices().find_one({"order_id": dn["order_id"], "dn_id": None, "void": {"$exists": False}})
    if not inv:
        raise HTTPException(400, "No invoice to pay")
    pl = await yoco.pay_link(node, inv, actor(dn), dn_id=dn["_id"], mark_sent=True)
    return {"url": pl["url"]}


@router.post("/{token}/collection")
async def collection(token: str, payload: dict):
    dn, node = await resolve(token)
    coll = await commerce.record_collection(node, dn, payload, actor(dn))
    if coll.get("payment_id"):
        try:
            import xero_mt
            await xero_mt.queue_payment(dn["node_id"], coll["payment_id"])
        except Exception:  # noqa: BLE001
            pass
    await commerce.refresh_order_status(dn["order_id"])
    fresh = await db.delivery_notes().find_one({"_id": dn["_id"]}, {"content_b64": 0})
    return await view(fresh, node)


@router.post("/{token}/delivered")
async def delivered(token: str, receiver_name: str = Form(""), lat: Optional[float] = Form(None),
                    lng: Optional[float] = Form(None), file: UploadFile | None = File(None)):
    dn, node = await resolve(token)
    if dn.get("delivery_status") not in ("scheduled", "out_for_delivery"):
        raise HTTPException(400, "This delivery is already closed")
    photo = await file.read() if file else None
    if photo and len(photo) > 12 * 1024 * 1024:
        raise HTTPException(400, "Photo too large (max 12 MB)")
    out = await commerce.capture_pod(dn, {"receiver_name": receiver_name, "lat": lat, "lng": lng}, actor(dn),
                                     photo, (file.content_type if file else None) or "image/jpeg")
    return await view(out, node)
