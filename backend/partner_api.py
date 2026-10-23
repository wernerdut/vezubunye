"""Partner API: /api/p/*. Partner users only, one node each.

Every authed route takes node_id from the token, never from the path or body. The only
routes that name a node otherwise are not user routes: the Yoco webhook (verified by the
node's signing secret) and the public branding/pay/share endpoints keyed by slug or a
signed token.

Visibility wall: responses pass through `wall()`, which strips Fenix cost and margin, the
platform's internal fields and secrets at any depth. Partners never see another node,
network figures, flags, the audit log, internal users or recon dashboards.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote as urlquote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse, Response

import audit
import bills
import board
import capture
import commerce
import corrections
import customer_search
import db
import files
import maps
import partner_auth
import partner_pdf
import pricing
import recon
import xero_mt
import yoco
from corrections import ACTIVE
from models import CaptureEntriesIn, PartnerUserIn
from partner_auth import current_partner, partner_admin

router = APIRouter(prefix="/api/p")

# ---------- the visibility wall ---------- #

FORBIDDEN = {"material_cost_per_kg", "material_cost_lost", "material_cost", "margin",
             "password_hash", "content_b64", "photo_b64", "secret_key_enc", "webhook_secret_enc",
             "access_token", "refresh_token", "driver_token", "share_token", "fenix_exworks_value",
             "partner_balance", "_methods", "network_kg", "total_kg"}


def wall(doc, cfg: dict | None = None, role: str = "partner_staff"):
    """Strip what a partner never sees, at any depth. On fenix_owns_tanks nodes the invoice's
    amount due to Fenix (fenix_value) and, for partner admins, the payment split stay; on
    powder_sale nodes neither exists."""
    keep_split = role == "partner_admin" and cfg is not None and commerce.fenix_model(cfg) == "fenix_owns_tanks"
    def scrub(x):
        if isinstance(x, dict):
            out = {}
            for k, v in x.items():
                if k in FORBIDDEN:
                    continue
                if k == "split" and not keep_split:
                    continue
                if k == "fenix_value" and (cfg is None or commerce.fenix_model(cfg) != "fenix_owns_tanks"):
                    continue
                out[k] = scrub(v)
            return out
        if isinstance(x, list):
            return [scrub(v) for v in x]
        return x
    return scrub(doc)


async def ctx(user: dict) -> tuple[dict, dict]:
    node = await commerce.get_node(user["node_id"])
    cfg = await commerce.get_cfg(user["node_id"])
    return node, cfg


async def node_for_pdf(node: dict) -> dict:
    return {**node, "_logo_png": await xero_mt.logo_png(node["node_id"])}


def pdf_response(pdf: bytes, name: str) -> Response:
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{name}.pdf"'})


def require_reason(raw: dict) -> str:
    return corrections.require_reason((raw or {}).get("reason"))


# ============================== public: branding, login ============================== #

async def node_by_slug(slug: str) -> dict:
    node = await db.nodes().find_one({"slug": (slug or "").lower().strip(), "status": "active"})
    if not node:
        raise HTTPException(404, "Unknown business")
    return node


@router.get("/branding")
async def branding(slug: str):
    """Public, before login: everything the shell needs to look like the partner's business."""
    node = await node_by_slug(slug)
    b = node.get("branding") or {}
    kinds = [a["kind"] async for a in db.brand_assets().find({"node_id": node["node_id"]}, {"kind": 1})]
    co = node.get("company") or {}
    return {"slug": node["slug"], "display_name": b.get("display_name") or node["name"],
            "colors": b.get("colors") or {}, "font": b.get("font") or {},
            "assets": {k: f"/api/p/branding/{node['slug']}/asset/{k}" for k in kinds},
            "company": {k: co.get(k) for k in ("legal_name", "phone", "whatsapp", "email", "website", "hours", "address")}}


@router.get("/branding/{slug}/asset/{kind}")
async def branding_asset(slug: str, kind: str):
    node = await node_by_slug(slug)
    a = await db.brand_assets().find_one({"node_id": node["node_id"], "kind": kind})
    if not a:
        raise HTTPException(404, "No such asset")
    return Response(base64.b64decode(a["content_b64"]), media_type=a["content_type"],
                    headers={"Cache-Control": "public, max-age=3600"})


@router.post("/auth/login")
async def login(payload: dict):
    """Only authenticates users of that slug's node."""
    node = await node_by_slug(payload.get("slug", ""))
    email = (payload.get("email") or "").lower().strip()
    u = await db.partner_users().find_one({"node_id": node["node_id"], "email": email})
    if not u or not u.get("active", True) or not partner_auth.verify_password(payload.get("password", ""), u["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    await db.partner_users().update_one({"_id": u["_id"]}, {"$set": {"last_login": datetime.utcnow()}})
    return {"token": partner_auth.make_token(u),
            "user": {"email": u["email"], "name": u.get("name", ""), "role": u["role"], "employer": u.get("employer")}}


@router.get("/auth/me")
async def me(user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    return {**user, "business": (node.get("branding") or {}).get("display_name") or node["name"],
            "features": {"yoco": await yoco.configured(node["node_id"]),
                         "xero": bool(await xero_mt.connection(node["node_id"])),
                         "colour_stock": recon.track_colour(cfg),
                         "fenix_split": commerce.fenix_model(cfg) == "fenix_owns_tanks"}}


# ============================== settings ============================== #

@router.get("/config")
async def get_config(user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    out = {k: v for k, v in cfg.items() if k not in ("material_cost_per_kg", "tolerances", "_id")}
    out["company"] = node.get("company") or {}
    out["branding"] = node.get("branding") or {}
    out["slug"] = node.get("slug")
    out["prefix"] = node.get("prefix")
    out["tiers_versions"] = {}
    for t in pricing.tiers(cfg):
        v = await pricing.current_version(node["node_id"], t["code"])
        out["tiers_versions"][t["code"]] = v
    return wall(out, cfg, user["role"])


SETTABLE = {"tank_types", "colours", "price_tiers", "charges", "quote_validity_days", "cash_banking_days",
            "drivers", "payment_terms_days", "proforma"}


@router.put("/settings/config")
async def put_config(payload: dict, user: dict = Depends(partner_admin)):
    """Products, colours, tiers, charges, quote validity, cash banking days, drivers. Recipe
    weights and powder products stay with Fenix: a partner cannot change what Fenix audits."""
    node, cfg = await ctx(user)
    upd = {k: v for k, v in payload.items() if k in SETTABLE}
    if "tank_types" in upd:
        fenix_fields = {t["code"]: t for t in cfg["tank_types"]}
        merged = []
        for t in upd["tank_types"]:
            base = fenix_fields.get(t["code"])
            if not base:
                raise HTTPException(400, f"New tank types are added by Fenix ({t['code']})")
            merged.append({**base, **{k: t[k] for k in ("name", "capacity_l", "diameter_mm", "height_mm",
                                                         "allowed_colours", "active", "kind") if k in t}})
        upd["tank_types"] = merged
    if "colours" in upd:
        products = {p["code"] for p in cfg.get("powder_products", [])}
        for c in upd["colours"]:
            if c.get("powder_product") and c["powder_product"] not in products:
                raise HTTPException(400, f"{c.get('name')}: unknown powder product")
    if not upd:
        raise HTTPException(400, "Nothing to change")
    await db.node_config().update_one({"node_id": node["node_id"]}, {"$set": {**upd, "updated_at": datetime.utcnow()}})
    await audit.log(user, node["node_id"], "update", "node_config", cfg.get("_id"),
                    before={k: cfg.get(k) for k in upd}, after=upd)
    return await get_config(user)


@router.put("/settings/company")
async def put_company(payload: dict, user: dict = Depends(partner_admin)):
    node, _ = await ctx(user)
    co = {**(node.get("company") or {}), **{k: v for k, v in payload.items() if k != "registered"}}
    await db.nodes().update_one({"node_id": node["node_id"]}, {"$set": {"company": co}})
    await audit.log(user, node["node_id"], "update", "nodes", node["_id"], before=node.get("company"), after=co)
    return co


@router.put("/settings/branding")
async def put_branding(payload: dict, user: dict = Depends(partner_admin)):
    node, _ = await ctx(user)
    b = {**(node.get("branding") or {}), **{k: payload[k] for k in ("display_name", "colors", "font") if k in payload}}
    await db.nodes().update_one({"node_id": node["node_id"]}, {"$set": {"branding": b}})
    await audit.log(user, node["node_id"], "update", "nodes", node["_id"], before=node.get("branding"), after=b)
    return b


@router.post("/settings/branding/asset/{kind}")
async def upload_brand_asset(kind: str, file: UploadFile = File(...), user: dict = Depends(partner_admin)):
    if kind not in ("logo_light", "logo_dark", "favicon", "icon", "app_icon", "logo_light_png", "logo_dark_png", "icon_png"):
        raise HTTPException(400, "Unknown asset")
    content = await file.read()
    if len(content) > 3 * 1024 * 1024:
        raise HTTPException(400, "Max 3 MB")
    ctype = file.content_type or "image/png"
    if ctype not in ("image/png", "image/svg+xml", "image/jpeg", "image/x-icon"):
        raise HTTPException(400, "PNG, SVG, JPEG or ICO")
    await db.brand_assets().replace_one({"node_id": user["node_id"], "kind": kind},
                                        {"_id": uuid4().hex, "node_id": user["node_id"], "kind": kind,
                                         "content_type": ctype, "filename": file.filename,
                                         "content_b64": base64.b64encode(content).decode(),
                                         "created_at": datetime.utcnow()}, upsert=True)
    await audit.log(user, user["node_id"], "update", "brand_assets", kind)
    return {"ok": True}


@router.post("/settings/prices/{tier}")
async def new_price_version(tier: str, payload: dict, user: dict = Depends(partner_admin)):
    """Editing prices creates a new version effective from a date; open quotes and orders keep
    the version they locked."""
    node, cfg = await ctx(user)
    pricing.tier(cfg, tier)
    tanks = {t["code"] for t in cfg["tank_types"]}
    prices = payload.get("prices") or {}
    for code, p in prices.items():
        if code not in tanks:
            raise HTTPException(400, f"Unknown tank type {code}")
        if p.get("A") in (None, ""):
            raise HTTPException(400, f"{code}: an A price is required")
    v = await pricing.new_version(node["node_id"], tier, payload.get("effective_from") or commerce.today(), prices, user)
    await audit.log(user, node["node_id"], "create", "price_lists", v["_id"], after=v)
    return v


@router.get("/settings/prices/{tier}/versions")
async def price_versions(tier: str, user: dict = Depends(current_partner)):
    return [v async for v in db.price_lists().find({"node_id": user["node_id"], "tier": tier}).sort("version", -1)]


# ---------- users ---------- #

@router.get("/users")
async def list_users(user: dict = Depends(partner_admin)):
    return [partner_auth.public_user(u) async for u in db.partner_users().find({"node_id": user["node_id"]})]


@router.post("/users")
async def create_user(payload: PartnerUserIn, user: dict = Depends(partner_admin)):
    email = payload.email.lower().strip()
    if await db.partner_users().find_one({"node_id": user["node_id"], "email": email}):
        raise HTTPException(400, "That email already has a login")
    if not payload.password or len(payload.password) < 8:
        raise HTTPException(400, "Password of at least 8 characters required")
    doc = {"_id": uuid4().hex, "email": email, "name": payload.name, "node_id": user["node_id"],
           "password_hash": partner_auth.hash_password(payload.password), "role": payload.role,
           "employer": payload.employer, "active": True, "created_at": datetime.utcnow(), "created_by": user["email"]}
    await db.partner_users().insert_one(doc)
    await audit.log(user, user["node_id"], "create", "partner_users", doc["_id"], after=doc)
    return partner_auth.public_user(doc)


@router.put("/users/{user_id}")
async def update_user(user_id: str, payload: dict, user: dict = Depends(partner_admin)):
    u = await db.partner_users().find_one({"_id": user_id, "node_id": user["node_id"]})
    if not u:
        raise HTTPException(404, "User not found")
    upd = {k: payload[k] for k in ("name", "role", "active", "employer") if k in payload}
    if payload.get("password"):
        if len(payload["password"]) < 8:
            raise HTTPException(400, "Password of at least 8 characters required")
        upd["password_hash"] = partner_auth.hash_password(payload["password"])
    if u["email"] == user["email"] and (upd.get("active") is False or upd.get("role") == "partner_staff"):
        raise HTTPException(400, "You cannot remove your own admin access")
    await db.partner_users().update_one({"_id": user_id}, {"$set": upd})
    await audit.log(user, user["node_id"], "update", "partner_users", user_id, before=u, after=upd)
    return {"ok": True}


# ============================== customers ============================== #

@router.post("/maps/locate")
async def locate(payload: dict, user: dict = Depends(current_partner)):
    return await maps.locate(payload.get("url", ""))


@router.post("/customers/import/cin7")
async def import_cin7_customers(customers: UploadFile = File(...), addresses: UploadFile = File(...),
                                apply: bool = Form(False), user: dict = Depends(partner_admin)):
    """Cin7 customer export: dry run by default (counts only), then `apply`. Re-running updates."""
    import import_cin7
    try:
        return await import_cin7.run(await customers.read(), await addresses.read(), apply,
                                     node_id=user["node_id"], user=user)
    except (ValueError, KeyError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"Could not read the Cin7 files: {e}")


@router.get("/customers/search")
async def search_customers(q: str = "", inactive: bool = False, user: dict = Depends(current_partner)):
    """One box: name, contact, phone (partial), email, suburb, ref. Empty: recent customers."""
    return await customer_search.search(user["node_id"], q, include_inactive=inactive)


@router.get("/customers/duplicates")
async def customer_duplicates(phone: str = "", email: str = "", exclude: Optional[str] = None,
                              user: dict = Depends(current_partner)):
    return await customer_search.duplicates(user["node_id"], phone, email, exclude)


@router.get("/customers/by-phone")
async def customer_by_phone(phone: str, user: dict = Depends(current_partner)):
    return await customer_search.by_phone(user["node_id"], phone)


@router.get("/customers")
async def list_customers(q: Optional[str] = None, user: dict = Depends(current_partner)):
    filt: dict = {"node_id": user["node_id"]}
    out = [c async for c in db.customers().find(filt).sort("name", 1)]
    if q:
        out = [c for c in out if q.lower() in c["name"].lower() or q in (c.get("phone") or "")]
    deb = {d["customer_id"]: d for d in await board.debtors(user["node_id"])}
    return [{**c, "balance": deb.get(c["_id"], {}).get("balance", 0.0)} for c in out]


@router.post("/customers")
async def create_customer(payload: dict, user: dict = Depends(current_partner)):
    return await commerce.create_customer(user["node_id"], payload, user, allow_terms=user["role"] == "partner_admin")


@router.get("/customers/{customer_id}")
async def get_customer(customer_id: str, brief: bool = False, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    c = await commerce.get_customer(node["node_id"], customer_id)
    if brief:       # the customer alone, for pickers
        return wall(c, cfg, user["role"])
    nid = node["node_id"]
    quotes = [q async for q in db.quotes().find({"node_id": nid, "customer_id": customer_id}).sort("date", -1)]
    orders = [r for r in await board.order_rows(nid) if r["customer_id"] == customer_id]
    invoices = [r for r in await board.invoice_rows(nid) if r["customer_id"] == customer_id]
    dns = [r for r in await board.delivery_rows(nid) if r["customer_id"] == customer_id]
    pays = [p async for p in db.payments().find({"node_id": nid, "customer_id": customer_id}).sort("date", -1)]
    deb = next((d for d in await board.debtors(nid) if d["customer_id"] == customer_id), None)
    return wall({**c, "quotes": quotes, "orders": board.public(orders), "invoices": board.public(invoices),
                 "deliveries": board.public(dns), "payments": pays, "aging": deb}, cfg, user["role"])


@router.put("/customers/{customer_id}")
async def update_customer(customer_id: str, payload: dict, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    c = await commerce.get_customer(node["node_id"], customer_id)
    upd = {k: payload[k] for k in ("name", "contact_person", "email", "phone", "billing_address", "vat_number",
                                   "payment_terms_days", "active") if k in payload}
    if "phone" in upd:
        upd["phone"] = customer_search.phone_e164(upd["phone"])
    for k in ("payment_basis", "price_tier"):
        if k in payload and payload[k] != c.get(k):
            if user["role"] != "partner_admin":
                raise HTTPException(403, "Only a partner admin puts a customer on terms or a price tier")
            if k == "price_tier":
                pricing.tier(cfg, payload[k])
            if k == "payment_basis" and payload[k] not in ("upfront", "terms"):
                raise HTTPException(400, "Basis is upfront or terms")
            upd[k] = payload[k]
    if "payment_terms_days" in upd and user["role"] != "partner_admin":
        raise HTTPException(403, "Only a partner admin changes terms")
    await db.customers().update_one({"_id": customer_id}, {"$set": upd})
    customer_search.invalidate(node["node_id"])
    await audit.log(user, node["node_id"], "update", "customers", customer_id,
                    before={k: c.get(k) for k in upd}, after=upd)
    return {**c, **upd}


@router.post("/customers/{customer_id}/sites")
async def save_site(customer_id: str, payload: dict, user: dict = Depends(current_partner)):
    c = await commerce.get_customer(user["node_id"], customer_id)
    site = await commerce.make_site(payload)
    sites = [s for s in c.get("sites") or [] if s["site_id"] != site["site_id"]] + [site]
    await db.customers().update_one({"_id": customer_id}, {"$set": {"sites": sites}})
    customer_search.invalidate(user["node_id"])
    await audit.log(user, user["node_id"], "update", "customers", customer_id, after={"site": site})
    return site


@router.get("/customers/{customer_id}/statement.pdf")
async def statement_pdf(customer_id: str, start: Optional[str] = None, end: Optional[str] = None,
                        user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    c = await commerce.get_customer(node["node_id"], customer_id)
    end = end or commerce.today()
    start = start or commerce.add_days(end, -90)
    st = await board.statement(node["node_id"], customer_id, start, end)
    return pdf_response(partner_pdf.statement(await node_for_pdf(node), cfg, c, st), f"Statement {c['name']}")


@router.get("/debtors")
async def debtors(user: dict = Depends(current_partner)):
    return await board.debtors(user["node_id"])


# ============================== quotes ============================== #

@router.get("/quotes")
async def list_quotes(status: Optional[str] = None, user: dict = Depends(current_partner)):
    filt: dict = {"node_id": user["node_id"]}
    if status:
        filt["status"] = {"$in": status.split(",")}
    return wall([q async for q in db.quotes().find(filt).sort([("date", -1), ("quote_number", -1)])])


@router.post("/quotes")
async def create_quote(payload: dict, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    cust = await commerce.get_customer(node["node_id"], payload.get("customer_id", ""))
    return wall(await commerce.create_quote(node, cfg, cust, payload, user))


async def quote_or_404(node_id: str, quote_id: str) -> dict:
    q = await db.quotes().find_one({"_id": quote_id, "node_id": node_id})
    if not q:
        raise HTTPException(404, "Quote not found")
    return q


@router.get("/quotes/{quote_id}")
async def get_quote(quote_id: str, user: dict = Depends(current_partner)):
    return wall(await quote_or_404(user["node_id"], quote_id))


@router.put("/quotes/{quote_id}")
async def revise_quote(quote_id: str, payload: dict, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    return wall(await commerce.revise_quote(node["node_id"], await quote_or_404(node["node_id"], quote_id), cfg, payload, user))


@router.post("/quotes/{quote_id}/status")
async def quote_status(quote_id: str, payload: dict, user: dict = Depends(current_partner)):
    node, _ = await ctx(user)
    q = await quote_or_404(node["node_id"], quote_id)
    out = await commerce.set_quote_status(node, q, payload.get("status"), user, payload.get("reason", ""),
                                          payload.get("order"))
    return wall(out)


@router.post("/quotes/{quote_id}/invoice")
async def invoice_proforma(quote_id: str, payload: dict, user: dict = Depends(current_partner)):
    """Steven decides when the tax invoice is made: the proforma becomes the order and its tax
    invoice, with the payment received so far if there is one (cash, card-machine slip, EFT).
    The invoice is then shared like any other."""
    node, cfg = await ctx(user)
    q = await quote_or_404(node["node_id"], quote_id)
    pay = payload.get("payment") or None
    raw = None
    if pay:
        method = pay.get("method")
        if method not in ("cash", "yoco_machine", "eft"):
            raise HTTPException(400, "Method is cash, yoco_machine or eft")
        if method == "yoco_machine" and not pay.get("reference"):
            raise HTTPException(400, "Enter the card-machine slip reference")
        try:
            amount = float(pay.get("amount") or 0)
        except (TypeError, ValueError):
            raise HTTPException(400, "Amount must be a number")
        raw = {"amount": amount, "method": "yoco" if method == "yoco_machine" else method,
               "source": "machine" if method == "yoco_machine" else None,
               "reference": pay.get("reference") or q["quote_number"], "date": pay.get("date"),
               "holder": {"type": "staff", "name": user.get("name") or user["email"]} if method == "cash" else None}
    out = await commerce.invoice_proforma(node, q, user, raw, {"pay_on_delivery": bool(payload.get("pay_on_delivery"))})
    await _xero_invoice(out["invoice_id"])
    if out.get("payment"):
        await _xero_payment(node["node_id"], out["payment"]["_id"])
    return wall(out, cfg, user["role"])


@router.get("/quotes/{quote_id}/pdf")
async def quote_pdf(quote_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    q = await quote_or_404(node["node_id"], quote_id)
    cust = await db.customers().find_one({"_id": q["customer_id"]})
    return pdf_response(partner_pdf.quote(await node_for_pdf(node), cfg, q, cust), q["quote_number"])


# ---------- share links (signed, read-only, expiring) ---------- #

def _share_sig(kind: str, rid: str, exp: int, secret: str) -> str:
    return hmac.new(partner_auth.PARTNER_JWT_SECRET.encode(), f"{kind}:{rid}:{exp}:{secret}".encode(),
                    hashlib.sha256).hexdigest()[:24]


def share_token(kind: str, doc: dict, exp: datetime) -> str:
    e = int(exp.timestamp())
    return f"{doc['_id']}.{e}.{_share_sig(kind, doc['_id'], e, doc.get('share_token', ''))}"


def share_url(node: dict, kind: str, tok: str) -> str:
    host = f"https://{node['slug']}.vezubunye.com" if node.get("slug") else ""
    return f"{host}/api/p/share/{kind}/{tok}.pdf"


def _greeting(cust: dict | None) -> str:
    name = ((cust or {}).get("contact_person") or (cust or {}).get("name") or "").split(" ")[0]
    return f"Hi {name}," if name else "Hi,"


@router.post("/quotes/{quote_id}/share")
async def share_quote(quote_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    q = await quote_or_404(node["node_id"], quote_id)
    if not q.get("share_token"):
        q["share_token"] = commerce.driver_token()
        await db.quotes().update_one({"_id": q["_id"]}, {"$set": {"share_token": q["share_token"]}})
    proforma = commerce.is_proforma(cfg)
    exp = (datetime.utcnow() + timedelta(days=180)) if proforma else datetime.fromisoformat(q["valid_until"]) + timedelta(days=1)
    url = share_url(node, "q", share_token("q", q, exp))
    cust = await db.customers().find_one({"_id": q["customer_id"]})
    biz = (node.get("branding") or {}).get("display_name") or node["name"]
    if proforma:
        msg = (f"{_greeting(cust)} here is your proforma {q['quote_number']} from {biz} for "
               f"{partner_pdf.money(q['total'])}. Please use {q['quote_number']} as your payment reference.\n{url}")
    else:
        msg = (f"{_greeting(cust)} here is your quote {q['quote_number']} from {biz} for "
               f"{partner_pdf.money(q['total'])}, valid until {q['valid_until']}.\n{url}")
    if q["status"] == "draft":
        await commerce.set_quote_status(node, q, "sent", user)
    return {"url": url, "message": msg, "whatsapp": maps.wa_link((cust or {}).get("phone", ""), msg),
            **_email(cust, f"{'Proforma' if proforma else 'Quote'} {q['quote_number']} from {biz}", msg)}


def _email(cust: dict | None, subject: str, body: str) -> dict:
    """mailto: for the share bar (opens the phone's or computer's mail app, nothing is sent from here)."""
    to = ((cust or {}).get("email") or "").strip()
    return {"email": f"mailto:{urlquote(to)}?subject={urlquote(subject)}&body={urlquote(body)}", "email_to": to}


@router.get("/share/{kind}/{token}.pdf")
async def shared_pdf(kind: str, token: str):
    try:
        rid, exp, sig = token.split(".")
        exp_i = int(exp)
    except ValueError:
        raise HTTPException(404, "Link not valid")
    coll = {"q": db.quotes(), "i": db.invoices()}.get(kind)
    if coll is None:
        raise HTTPException(404, "Link not valid")
    doc = await coll.find_one({"_id": rid})
    if not doc or not hmac.compare_digest(sig, _share_sig(kind, rid, exp_i, doc.get("share_token", ""))):
        raise HTTPException(404, "Link not valid")
    if datetime.utcnow().timestamp() > exp_i:
        raise HTTPException(410, "This link has expired")
    node = await node_for_pdf(await commerce.get_node(doc["node_id"]))
    cfg = await commerce.get_cfg(doc["node_id"])
    cust = await db.customers().find_one({"_id": doc.get("customer_id")})
    if kind == "q":
        return pdf_response(partner_pdf.quote(node, cfg, doc, cust), doc["quote_number"])
    link = await _existing_pay_link(node, doc)
    return pdf_response(partner_pdf.invoice(node, cfg, doc, cust, link), doc["display_number"])


# ============================== orders ============================== #

@router.post("/orders")
async def create_order(payload: dict, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    cust = await commerce.get_customer(node["node_id"], payload.get("customer_id", ""))
    async with corrections.txn() as s:
        o = await commerce.create_order(node, cfg, cust, payload, user, session=s)
    await _after_new_invoices(node, o, user)
    return wall(await commerce.order_view(node["node_id"], o), cfg, user["role"])


async def _after_new_invoices(node: dict, order: dict, user: dict):
    """A pay-first order's invoice gets its pay link at once (sent later from the order), and
    every new invoice goes to Xero when the node is connected."""
    async for inv in db.invoices().find({"order_id": order["_id"], **ACTIVE, "xero.status": "not_posted"}):
        if order["payment_basis"] == "upfront" and not order.get("pay_on_delivery") and inv["dn_id"] is None:
            try:
                await yoco.pay_link(node, inv, user)
            except HTTPException:
                pass    # no Yoco on this node: cash, card machine and EFT still work
        await _xero_invoice(inv["_id"])


@router.get("/orders/{order_id}")
async def get_order(order_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    o = await commerce.order_or_404(node["node_id"], order_id)
    return wall(await commerce.order_view(node["node_id"], o), cfg, user["role"])


@router.put("/orders/{order_id}/lines")
async def edit_order(order_id: str, payload: dict, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    o = await commerce.order_or_404(node["node_id"], order_id)
    if o.get("status") != "open":
        raise HTTPException(400, "Only an open order is edited")
    await commerce.edit_order_lines(node, o, payload.get("lines") or [], user, require_reason(payload))
    async for inv in db.invoices().find({"order_id": order_id}):
        if inv.get("void") or (inv.get("xero") or {}).get("status") == "not_posted":
            await _xero_invoice(inv["_id"])
    return wall(await commerce.order_view(node["node_id"], await commerce.order_or_404(node["node_id"], order_id)), cfg, user["role"])


@router.put("/orders/{order_id}")
async def edit_order_details(order_id: str, payload: dict, user: dict = Depends(current_partner)):
    """Address, requested date, notes: until a delivery is scheduled."""
    node, cfg = await ctx(user)
    o = await commerce.order_or_404(node["node_id"], order_id)
    if o.get("status") != "open":
        raise HTTPException(400, "Only an open order is edited")
    await commerce.update_order_details(o, payload, user)
    return wall(await commerce.order_view(node["node_id"], await commerce.order_or_404(node["node_id"], order_id)), cfg, user["role"])


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, payload: dict, user: dict = Depends(current_partner)):
    """Undo an order nothing has been scheduled on. Any partner user, with a reason."""
    node, cfg = await ctx(user)
    o = await commerce.order_or_404(node["node_id"], order_id)
    out = await commerce.cancel_order(node, o, user, require_reason(payload))
    for inv_id in out["voided_invoices"]:
        await _xero_invoice(inv_id)
    return wall(await commerce.order_view(node["node_id"], await commerce.order_or_404(node["node_id"], order_id)), cfg, user["role"])


@router.post("/orders/{order_id}/cancel-remainder")
async def cancel_remainder(order_id: str, payload: dict, user: dict = Depends(partner_admin)):
    node, cfg = await ctx(user)
    o = await commerce.order_or_404(node["node_id"], order_id)
    await commerce.cancel_remainder(node, o, user, require_reason(payload))
    return wall(await commerce.order_view(node["node_id"], await commerce.order_or_404(node["node_id"], order_id)), cfg, user["role"])


@router.post("/orders/{order_id}/deliveries")
async def schedule_delivery(order_id: str, payload: dict, user: dict = Depends(current_partner)):
    """Schedule a delivery (full or part) from the order. Terms: invoice now is on by default."""
    node, cfg = await ctx(user)
    o = await commerce.order_or_404(node["node_id"], order_id)
    if o.get("status") != "open":
        raise HTTPException(400, "Only an open order is delivered")
    if payload.get("driver") and not isinstance(payload["driver"], dict):
        raise HTTPException(400, "Driver is {name, phone}")
    async with corrections.txn() as s:
        dn = await commerce.write_dn(node, cfg, o, payload, user, session=s)
        inv = None
        if o["payment_basis"] == "terms" and payload.get("invoice_now", True):
            inv = await commerce.invoice_dn(node, cfg, dn, user, session=s)
        await audit.log(user, node["node_id"], "create", "delivery_notes", dn["_id"], after=dn, session=s)
    await commerce.store_dn_pdf(await node_for_pdf(node), cfg, dn)
    await commerce.refresh_order_status(o["_id"])
    if inv:
        await _xero_invoice(inv["_id"])
    out = wall(await commerce.dn_or_404(node["node_id"], dn["_id"]), cfg, user["role"])
    return {**out, "invoice": wall(inv, cfg, user["role"]) if inv else None}


@router.post("/quick-sale")
async def quick_sale(payload: dict, user: dict = Depends(current_partner)):
    """Walk-in at the factory: order, invoice, payment (cash, Yoco machine, or a pay link on the
    spot) and delivery note on one screen."""
    node, cfg = await ctx(user)
    cust_id = payload.get("customer_id")
    if not cust_id:
        cust = await commerce.create_customer(node["node_id"], payload.get("customer") or {"name": "Walk-in customer"},
                                              user, allow_terms=False)
    else:
        cust = await commerce.get_customer(node["node_id"], cust_id)
    if cust.get("payment_basis") != "upfront":
        cust = {**cust, "payment_basis": "upfront"}
    pay = payload.get("payment") or {}
    async with corrections.txn() as s:
        o = await commerce.create_order(node, cfg, cust, {**payload, "pay_on_delivery": False}, user, session=s,
                                        extra={"quick_sale": True})
        inv = await db.invoices().find_one({"order_id": o["_id"]}, session=s)
        p = None
        if pay.get("method") in ("cash", "yoco_machine"):
            p = await commerce.record_payment(node["node_id"], {
                "amount": pay.get("amount") or inv["total"], "method": "cash" if pay["method"] == "cash" else "yoco",
                "source": "machine" if pay["method"] == "yoco_machine" else None,
                "reference": pay.get("reference", ""), "customer_id": cust["_id"], "invoice_id": inv["_id"],
                "holder": {"type": "staff", "name": user.get("name") or user["email"]}}, user, session=s)
    link = None
    if pay.get("method") == "yoco_link":
        link = await yoco.pay_link(node, await db.invoices().find_one({"_id": inv["_id"]}), user, mark_sent=True)
    dn = None
    if payload.get("deliver_now", True) and (p or payload.get("release_reason")):
        o = await commerce.order_or_404(node["node_id"], o["_id"])
        async with corrections.txn() as s:
            dn = await commerce.write_dn(node, cfg, o, {
                "lines": [{"order_line_id": ln["line_id"], "quantity": ln["qty"]} for ln in o["lines"] if commerce.is_tank(ln)],
                "release_reason": payload.get("release_reason")}, user, session=s,
                extra={"delivery_status": "delivered", "collected_at_factory": True})
        await commerce.store_dn_pdf(await node_for_pdf(node), cfg, dn)
    await _xero_invoice(inv["_id"])
    if p:
        await _xero_payment(node["node_id"], p["_id"])
    await commerce.refresh_order_status(o["_id"])
    return wall({"order": await commerce.order_view(node["node_id"], await commerce.order_or_404(node["node_id"], o["_id"])),
                 "pay_link": link["url"] if link else None, "dn_id": dn["_id"] if dn else None}, cfg, user["role"])


# ============================== deliveries ============================== #

@router.get("/deliveries/{dn_id}")
async def get_delivery(dn_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    dn = await db.delivery_notes().find_one({"_id": dn_id, "node_id": node["node_id"]}, {"content_b64": 0})
    if not dn:
        raise HTTPException(404, "Delivery note not found")
    return wall({**dn, "nav_link": maps.nav_link(dn.get("site")),
                 "driver_link": driver_url(node, dn) if not dn.get("void") else None}, cfg, user["role"])


@router.get("/deliveries/{dn_id}/pdf")
async def delivery_pdf(dn_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    dn = await db.delivery_notes().find_one({"_id": dn_id, "node_id": node["node_id"]})
    if not dn:
        raise HTTPException(404, "Delivery note not found")
    if not dn.get("content_b64") or dn.get("origin") == "internal" or dn.get("legacy"):
        # partner-branded on demand (legacy notes were printed in the internal style)
        ctx_ = await commerce._dn_context(dn)
        return pdf_response(partner_pdf.delivery_note(await node_for_pdf(node), cfg, dn, ctx_), dn["dn_number"])
    return pdf_response(base64.b64decode(dn["content_b64"]), dn["dn_number"])


def driver_token(dn: dict) -> str:
    sig = hmac.new(partner_auth.PARTNER_JWT_SECRET.encode(), f"driver:{dn['_id']}:{dn['driver_token']}".encode(),
                   hashlib.sha256).hexdigest()[:20]
    return f"{dn['_id']}.{sig}"


def driver_url(node: dict, dn: dict) -> str:
    host = f"https://{node['slug']}.vezubunye.com" if node.get("slug") else ""
    return f"{host}/d/{driver_token(dn)}"


@router.post("/deliveries/{dn_id}/send-to-driver")
async def send_to_driver(dn_id: str, payload: dict, user: dict = Depends(current_partner)):
    """The message Steven used to type: map link, contact, load, amount to collect, and the
    driver link. Opens WhatsApp to the driver."""
    node, cfg = await ctx(user)
    dn = await commerce.dn_or_404(node["node_id"], dn_id)
    full = await db.delivery_notes().find_one({"_id": dn_id})
    driver = payload.get("driver") or dn.get("driver") or {}
    if not driver.get("phone"):
        raise HTTPException(400, "Pick a driver with a phone number")
    upd = {"driver": {"name": driver.get("name", ""), "phone": driver["phone"]}}
    if payload.get("vehicle_reg") is not None:
        upd["vehicle_reg"] = payload["vehicle_reg"]
    await db.delivery_notes().update_one({"_id": dn_id}, {"$set": {**upd, "sent_to_driver_at": datetime.utcnow()}})
    site = dn.get("site") or {}
    load = ", ".join(f"{ln['quantity']} x {commerce.line_label(cfg, {**ln, 'kind': 'tank'})}" for ln in dn["lines"])
    lines = [f"Delivery {dn['dn_number']} for {dn['client_name']}",
             ", ".join(x for x in (site.get("label"), site.get("town")) if x),
             f"Navigate: {maps.nav_link(site)}" if maps.nav_link(site) else "",
             f"Site contact: {site.get('contact_name', '')} {site.get('contact_phone', '')}".strip()
             if site.get("contact_name") or site.get("contact_phone") else "",
             site.get("directions", ""), f"Load: {load}"]
    if dn.get("collect_amount"):
        o = await db.orders().find_one({"_id": dn["order_id"]}) or {}
        how = {"cash": "cash", "yoco": "card", "either": "cash or card"}.get(o.get("cod_method") or "either", "cash or card")
        lines.append(f"Collect: {partner_pdf.money(dn['collect_amount'])} ({how})")
    lines.append(f"Confirm delivery: {driver_url(node, full)}")
    msg = "\n".join(x for x in lines if x)
    await audit.log(user, node["node_id"], "send_to_driver", "delivery_notes", dn_id, after=upd)
    return {"message": msg, "whatsapp": maps.wa_link(driver["phone"], msg)}


@router.post("/deliveries/{dn_id}/status")
async def delivery_status(dn_id: str, payload: dict, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    dn = await commerce.dn_or_404(node["node_id"], dn_id)
    out = await commerce.set_delivery_status(dn, payload.get("status"), user, payload.get("reason", ""),
                                             payload.get("planned_date"))
    return wall(out, cfg, user["role"])


@router.post("/deliveries/{dn_id}/pod")
async def delivery_pod(dn_id: str, receiver_name: str = Form(""), lat: Optional[float] = Form(None),
                       lng: Optional[float] = Form(None), file: UploadFile | None = File(None),
                       user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    dn = await commerce.dn_or_404(node["node_id"], dn_id)
    photo = await file.read() if file else None
    if photo and len(photo) > 12 * 1024 * 1024:
        raise HTTPException(400, "Photo too large (max 12 MB)")
    out = await commerce.capture_pod(dn, {"receiver_name": receiver_name, "lat": lat, "lng": lng,
                                          "after_the_fact": True}, user, photo,
                                     (file.content_type if file else None) or "image/jpeg")
    return wall(out, cfg, user["role"])


@router.get("/deliveries/{dn_id}/pod/photo")
async def pod_photo(dn_id: str, user: dict = Depends(current_partner)):
    dn = await db.delivery_notes().find_one({"_id": dn_id, "node_id": user["node_id"]}, {"pod": 1})
    fid = ((dn or {}).get("pod") or {}).get("photo_file_id")
    f = await db.pod_files().find_one({"_id": fid}) if fid else None
    if not f:
        raise HTTPException(404, "No photo")
    return Response(await files.load(f), media_type=f["content_type"])


@router.post("/deliveries/{dn_id}/collection")
async def delivery_collection(dn_id: str, payload: dict, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    dn = await commerce.dn_or_404(node["node_id"], dn_id)
    coll = await commerce.record_collection(node, dn, payload, user)
    if coll.get("payment_id"):
        await _xero_payment(node["node_id"], coll["payment_id"])
    await commerce.refresh_order_status(dn["order_id"])
    return coll


@router.post("/deliveries/{dn_id}/invoice")
async def invoice_delivery(dn_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    dn = await commerce.dn_or_404(node["node_id"], dn_id)
    inv = await commerce.invoice_dn(node, cfg, dn, user)
    await commerce.refresh_order_status(dn["order_id"])
    await _xero_invoice(inv["_id"])
    return wall(inv, cfg, user["role"])


@router.post("/deliveries/{dn_id}/void")
async def void_delivery(dn_id: str, payload: dict, user: dict = Depends(current_partner)):
    """Tanks return to stock. Staff void a failed or not-yet-dispatched delivery; anything
    collected or invoiced-and-paid needs Fenix audit."""
    node, cfg = await ctx(user)
    reason = require_reason(payload)
    dn = await commerce.dn_or_404(node["node_id"], dn_id)
    if dn.get("legacy"):
        raise HTTPException(400, "Deliveries captured in the internal app are corrected there")
    if user["role"] != "partner_admin" and dn.get("delivery_status") not in ("scheduled", "failed"):
        raise HTTPException(403, "Only a partner admin voids a delivery that is out or delivered")
    if dn.get("collection") and dn["collection"].get("payment_id"):
        raise HTTPException(400, "A collection is recorded on this delivery: Fenix audit corrects it")
    async for inv in db.invoices().find({"dn_id": dn_id, **ACTIVE}):
        if inv.get("amount_paid", 0) > 0:
            raise HTTPException(400, f"Invoice {inv['display_number']} on this delivery has payments")
    async with corrections.txn() as s:
        async for inv in db.invoices().find({"dn_id": dn_id, **ACTIVE}, session=s):
            await corrections.mark_void("invoices", inv["_id"], user, reason, session=s, extra={"status": "void"})
    out = await corrections.void_record("delivery_notes", dn_id, user, reason, corrections.delivery_cascade(dn_id))
    await commerce.refresh_order_status(dn["order_id"])
    return wall(out, cfg, user["role"])


# ============================== invoices ============================== #

async def _existing_pay_link(node: dict, inv: dict) -> str | None:
    pl = await db.pay_links().find_one({"invoice_id": inv["_id"], "status": {"$in": ["open", "sent"]}})
    return yoco.public_url(node, pl["token"]) if pl else None


@router.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    inv = await db.invoices().find_one({"_id": invoice_id, "node_id": node["node_id"]})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    pays = [p async for p in db.payments().find({"allocations.invoice_id": invoice_id}).sort("date", 1)]
    links = [{**pl, "url": yoco.public_url(node, pl["token"])} async for pl in db.pay_links().find({"invoice_id": invoice_id})]
    return wall({**inv, "payments": pays, "pay_links": links}, cfg, user["role"])


@router.get("/invoices/{invoice_id}/pdf")
async def invoice_pdf(invoice_id: str, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    inv = await db.invoices().find_one({"_id": invoice_id, "node_id": node["node_id"]})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    if inv.get("legacy"):
        raise HTTPException(400, "Legacy invoices have no PDF: the delivery note carries the record")
    cust = await db.customers().find_one({"_id": inv["customer_id"]})
    return pdf_response(partner_pdf.invoice(await node_for_pdf(node), cfg, inv, cust, await _existing_pay_link(node, inv)),
                        inv["display_number"])


@router.post("/invoices/{invoice_id}/share")
async def share_invoice(invoice_id: str, user: dict = Depends(current_partner)):
    node, _ = await ctx(user)
    inv = await db.invoices().find_one({"_id": invoice_id, "node_id": node["node_id"]})
    if not inv or inv.get("legacy"):
        raise HTTPException(404, "Invoice not found")
    if not inv.get("share_token"):
        inv["share_token"] = commerce.driver_token()
        await db.invoices().update_one({"_id": invoice_id}, {"$set": {"share_token": inv["share_token"]}})
    url = share_url(node, "i", share_token("i", inv, datetime.utcnow() + timedelta(days=90)))
    cust = await db.customers().find_one({"_id": inv["customer_id"]})
    biz = (node.get("branding") or {}).get("display_name") or node["name"]
    link = await _existing_pay_link(node, inv)
    msg = (f"{_greeting(cust)} here is invoice {inv['display_number']} from {biz}. Amount due "
           f"{partner_pdf.money(inv.get('balance', inv['total']))}.\n{url}" + (f"\nPay by card: {link}" if link else ""))
    return {"url": url, "message": msg, "whatsapp": maps.wa_link((cust or {}).get("phone", ""), msg),
            **_email(cust, f"Tax invoice {inv['display_number']} from {biz}", msg)}


@router.post("/invoices/{invoice_id}/pay-link")
async def send_pay_link(invoice_id: str, user: dict = Depends(current_partner)):
    node, _ = await ctx(user)
    inv = await db.invoices().find_one({"_id": invoice_id, "node_id": node["node_id"], **ACTIVE})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    pl = await yoco.pay_link(node, inv, user, mark_sent=True)
    cust = await db.customers().find_one({"_id": inv["customer_id"]})
    biz = (node.get("branding") or {}).get("display_name") or node["name"]
    msg = (f"{_greeting(cust)} you can pay {partner_pdf.money(inv.get('balance', inv['total']))} for "
           f"{inv['display_number']} ({biz}) by card here:\n{pl['url']}")
    await commerce.refresh_order_status(inv["order_id"])
    return {"url": pl["url"], "message": msg, "whatsapp": maps.wa_link((cust or {}).get("phone", ""), msg)}


@router.post("/invoices/{invoice_id}/void")
async def void_invoice(invoice_id: str, payload: dict, user: dict = Depends(partner_admin)):
    inv = await db.invoices().find_one({"_id": invoice_id, "node_id": user["node_id"]})
    if not inv or inv.get("legacy"):
        raise HTTPException(404, "Invoice not found")
    out = await commerce.void_invoice(invoice_id, user, require_reason(payload), reissue=bool(payload.get("reissue")))
    await _xero_invoice(invoice_id)
    if out.get("new"):
        await _xero_invoice(out["new"]["_id"])
    return wall(out)


# ============================== payments and cash ============================== #

@router.get("/payments")
async def list_payments(method: Optional[str] = None, user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    filt: dict = {"node_id": node["node_id"]}
    if method:
        filt["method"] = method
    pays = [p async for p in db.payments().find(filt).sort([("date", -1), ("created_at", -1)])]
    inv_ids = {a["invoice_id"] for p in pays for a in p.get("allocations") or []}
    nums = {i["_id"]: i["display_number"] async for i in db.invoices().find({"_id": {"$in": list(inv_ids)}}, {"display_number": 1})}
    names = {c["_id"]: c["name"] async for c in db.customers().find({"node_id": node["node_id"]}, {"name": 1})}
    for p in pays:
        p["customer_name"] = names.get(p.get("customer_id"), "")
        for a in p.get("allocations") or []:
            a["invoice_number"] = nums.get(a["invoice_id"], "")
    return wall(pays, cfg, user["role"])


@router.post("/payments")
async def capture_payment(payload: dict, user: dict = Depends(current_partner)):
    """Cash received, a Yoco card-machine slip, or an EFT that shows in the bank. With Xero
    connected, the EFT is linked to the matching payment when it is reconciled there.
    Allocation defaults to oldest open invoice first and is editable before saving."""
    node, cfg = await ctx(user)
    method = payload.get("method")
    if method not in ("cash", "yoco_machine", "eft"):
        raise HTTPException(400, "Method is cash, yoco_machine or eft")
    if method == "yoco_machine" and not payload.get("reference"):
        raise HTTPException(400, "Enter the card-machine slip reference")
    raw = {**payload, "method": "yoco" if method == "yoco_machine" else method,
           "source": "machine" if method == "yoco_machine" else None,
           "holder": {"type": "staff", "name": user.get("name") or user["email"]} if method == "cash" else None}
    p = await commerce.record_payment(node["node_id"], raw, user, allocations=payload.get("allocations"))
    for a in p["allocations"]:
        inv = await db.invoices().find_one({"_id": a["invoice_id"]})
        await commerce.refresh_order_status(inv["order_id"])
    await _xero_payment(node["node_id"], p["_id"])
    return wall(p, cfg, user["role"])


@router.get("/payments/allocation-preview")
async def allocation_preview(customer_id: str, amount: float, user: dict = Depends(current_partner)):
    invs = await commerce.open_invoices(user["node_id"], customer_id)
    alloc = commerce.default_allocation(amount, invs)
    return {"open_invoices": [{"_id": i["_id"], "number": i["display_number"], "date": i["date"],
                               "due_date": i["due_date"], "total": i["total"],
                               "balance": round(i["total"] - i.get("amount_paid", 0), 2)} for i in invs],
            "allocations": alloc}


@router.get("/cash")
async def cash(user: dict = Depends(current_partner)):
    on_hand = await commerce.cash_on_hand(user["node_id"])
    deposits = [d async for d in db.cash_deposits().find({"node_id": user["node_id"]}).sort("date", -1)]
    receipts = [p async for p in db.payments().find({"node_id": user["node_id"], "method": "cash",
                                                     "deposit_id": None, **ACTIVE}).sort("date", 1)]
    if user["role"] != "partner_admin":
        # staff see receipts to bank, not the cash-on-hand totals
        on_hand = [{"holder": h["holder"], "receipts": h["receipts"], "oldest": h["oldest"]} for h in on_hand]
    return wall({"on_hand": on_hand, "receipts": receipts, "deposits": deposits})


@router.post("/cash/deposits")
async def bank_cash(payload: dict, user: dict = Depends(current_partner)):
    return wall(await commerce.bank_cash(user["node_id"], payload, user))


@router.post("/cash/deposits/slip")
async def deposit_slip(file: UploadFile = File(...), user: dict = Depends(current_partner)):
    content = await file.read()
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(400, "Photo too large (max 12 MB)")
    fid = uuid4().hex
    f = await files.store(f"deposits/{user['node_id']}", content, file.filename or "slip.jpg",
                          file.content_type or "image/jpeg")
    await db.pod_files().insert_one({"_id": fid, "node_id": user["node_id"], "kind": "deposit_slip",
                                     **f, "created_at": datetime.utcnow()})
    return {"slip_file_id": fid}


# ============================== dashboard ============================== #

@router.get("/dashboard/tiles")
async def tiles(period: str = "month", start: Optional[str] = None, end: Optional[str] = None,
                user: dict = Depends(current_partner)):
    admin = user["role"] == "partner_admin"
    return await board.tiles(user["node_id"], board.period_range(period, start, end), include_cash=admin, include_fenix=admin)


@router.get("/dashboard/board/{kind}.csv")
async def board_csv(kind: str, filters: str = "{}", sort: str = "{}", user: dict = Depends(current_partner)):
    if kind not in board.ROWS:
        raise HTTPException(404, "Unknown board")
    rows = board.apply(kind, await board.ROWS[kind](user["node_id"]), json.loads(filters or "{}"), json.loads(sort or "{}"))
    return Response(board.to_csv(kind, rows), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{kind}-{commerce.today()}.csv"'})


@router.get("/dashboard/board/{kind}")
async def board_rows(kind: str, filters: str = "{}", sort: str = "{}", user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    if kind not in board.ROWS:
        raise HTTPException(404, "Unknown board")
    rows = board.apply(kind, await board.ROWS[kind](node["node_id"]), json.loads(filters or "{}"), json.loads(sort or "{}"))
    return wall({"rows": board.public(rows), "totals": board.totals(kind, rows)}, cfg, user["role"])


@router.get("/dashboard/views")
async def views(user: dict = Depends(current_partner)):
    return await board.views(user["node_id"], user["email"])


@router.post("/dashboard/views")
async def save_view(payload: dict, user: dict = Depends(current_partner)):
    if not (payload.get("name") or "").strip():
        raise HTTPException(400, "Name the view")
    return await board.save_view(user["node_id"], user["email"], payload)


@router.delete("/dashboard/views/{view_id}")
async def delete_view(view_id: str, user: dict = Depends(current_partner)):
    await db.dashboard_views().delete_one({"_id": view_id, "node_id": user["node_id"], "user": user["email"]})
    return {"deleted": True}


# ============================== stock and production ============================== #

@router.get("/stock")
async def stock(user: dict = Depends(current_partner)):
    """Finished goods per type, colour and grade against remaining-to-deliver; powder and
    fittings positions, read-only."""
    node, cfg = await ctx(user)
    nid = node["node_id"]
    store = await recon.fg_warehouse(nid, cfg=cfg)
    remaining: dict = {}
    for r in await board.order_rows(nid):
        if r["order_status"] != "open":
            continue
        for l in r["lines"]:
            if commerce.is_tank(l) and l.get("qty_remaining", 0) > 0:
                k = recon.fg_key(cfg, l["tank_type"], l.get("grade", "A"), l.get("colour"))
                remaining[k] = remaining.get(k, 0) + l["qty_remaining"]
    keys = sorted(set(store) | set(remaining))
    fg = [{"tank_type": k[0], "colour": k[1] if len(k) == 3 else None, "grade": k[-1],
           "on_hand": store.get(k, 0), "to_deliver": remaining.get(k, 0),
           "free": store.get(k, 0) - remaining.get(k, 0)} for k in keys]
    wh = await recon.powder_warehouse(nid)
    products = {p["code"]: p for p in cfg.get("powder_products", [])}
    powder = [{"powder_type": k, "colour": products.get(k, {}).get("colour", k),
               "is_black": products.get(k, {}).get("is_black", False), "warehouse_kg": v}
              for k, v in sorted(wh.items())]
    fittings = [{"fitting_type": k, "qty": v} for k, v in sorted((await recon.fittings_warehouse(nid)).items())]
    return {"finished_goods": fg, "powder": powder, "fittings": fittings}


@router.get("/production/captures")
async def captures(month: Optional[str] = None, user: dict = Depends(current_partner)):
    filt: dict = {"node_id": user["node_id"], **ACTIVE}
    if month:
        filt["date"] = {"$regex": f"^{month}"}
    return [{k: v for k, v in c.items() if k not in ("photo_b64", "photo")}
            async for c in db.daily_captures().find(filt).sort("date", -1)]


@router.post("/production/captures")
async def open_capture(date: str, user: dict = Depends(current_partner)):
    return await capture.open_capture(user["node_id"], date, user)


@router.post("/production/captures/{capture_id}/entries")
async def capture_entries(capture_id: str, payload: CaptureEntriesIn, user: dict = Depends(current_partner)):
    cap = await db.daily_captures().find_one({"_id": capture_id, "node_id": user["node_id"]})
    if not cap:
        raise HTTPException(404, "Capture not found")
    _, cfg = await ctx(user)
    # same-day fixes only; an earlier day is a Fenix admin correction
    return await capture.key_entries(cap, payload, user, cfg, can_correct_any_day=False)


@router.post("/production/captures/{capture_id}/photo")
async def capture_photo(capture_id: str, file: UploadFile = File(...), user: dict = Depends(current_partner)):
    cap = await db.daily_captures().find_one({"_id": capture_id, "node_id": user["node_id"]})
    if not cap:
        raise HTTPException(404, "Capture not found")
    content = await file.read()
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(400, "Photo too large (max 12 MB)")
    f = await files.store(f"captures/{user['node_id']}", content, f"{cap['date']}.jpg",
                          file.content_type or "image/jpeg")
    await db.daily_captures().update_one({"_id": capture_id}, {"$set": {
        "photo": f, "photo_content_type": f["content_type"], "photo_url": f"/api/captures/{capture_id}/photo"}})
    await audit.log(user, user["node_id"], "update", "daily_captures", capture_id, after={"photo_uploaded": True})
    return {"ok": True}


@router.get("/production/capture-sheet.pdf")
async def capture_sheet(user: dict = Depends(current_partner)):
    node, cfg = await ctx(user)
    return pdf_response(partner_pdf.capture_sheet(await node_for_pdf(node), cfg), "Daily Capture Sheet")


# ============================== bills ============================== #

@router.post("/bills")
async def new_bill(user: dict = Depends(current_partner)):
    return bills.public(await bills.create(user["node_id"], user))


@router.get("/bills")
async def list_bills(scope: str = "mine", status: Optional[str] = None, user: dict = Depends(current_partner)):
    filt: dict = {"node_id": user["node_id"]}
    if scope == "mine":
        filt["captured_by"] = user["email"]
    elif scope == "to_approve":
        if user["role"] != "partner_admin":
            raise HTTPException(403, "Requires partner admin")
        filt["status"] = {"$in": ["submitted", "post_failed"]}
    if status:
        filt["status"] = status
    return [bills.public(b) async for b in db.bills().find(filt).sort("created_at", -1)]


@router.get("/bills/suppliers")
async def bill_suppliers(user: dict = Depends(current_partner)):
    return await bills.suppliers(user["node_id"])


@router.put("/bills/suppliers")
async def save_supplier(payload: dict, user: dict = Depends(partner_admin)):
    return await bills.upsert_supplier(user["node_id"], payload, user)


@router.get("/bills/files/{doc_id}")
async def bill_file(doc_id: str, user: dict = Depends(current_partner)):
    content, name, mime = await bills.load_document(doc_id, user["node_id"])
    return Response(content, media_type=mime, headers={"Content-Disposition": f'inline; filename="{name}"'})


@router.get("/bills/{bill_id}")
async def get_bill(bill_id: str, user: dict = Depends(current_partner)):
    return bills.public(await bills.get_bill(user["node_id"], bill_id))


@router.post("/bills/{bill_id}/upload")
async def bill_upload(bill_id: str, file: UploadFile = File(...), user: dict = Depends(current_partner)):
    b = await bills.get_bill(user["node_id"], bill_id)
    return await bills.attach(user["node_id"], b, await file.read(), file.filename or "document", file.content_type, user)


@router.post("/bills/{bill_id}/extract")
async def bill_extract(bill_id: str, user: dict = Depends(current_partner)):
    node, _ = await ctx(user)
    return bills.public(await bills.run_extraction(node, await bills.get_bill(node["node_id"], bill_id), user))


@router.patch("/bills/{bill_id}")
async def bill_edit(bill_id: str, payload: dict, user: dict = Depends(current_partner)):
    return bills.public(await bills.edit(user["node_id"], await bills.get_bill(user["node_id"], bill_id), payload, user))


@router.post("/bills/{bill_id}/submit")
async def bill_submit(bill_id: str, user: dict = Depends(current_partner)):
    return bills.public(await bills.submit(user["node_id"], await bills.get_bill(user["node_id"], bill_id), user))


@router.post("/bills/{bill_id}/approve")
async def bill_approve(bill_id: str, user: dict = Depends(partner_admin)):
    return bills.public(await bills.approve(user["node_id"], await bills.get_bill(user["node_id"], bill_id), user))


@router.post("/bills/approve-bulk")
async def bill_approve_bulk(payload: dict, user: dict = Depends(partner_admin)):
    out = []
    for bid in payload.get("bill_ids") or []:
        try:
            b = await bills.approve(user["node_id"], await bills.get_bill(user["node_id"], bid), user)
            out.append({"_id": bid, "status": b["status"]})
        except HTTPException as e:
            out.append({"_id": bid, "error": e.detail})
    return out


@router.post("/bills/{bill_id}/query")
async def bill_query(bill_id: str, payload: dict, user: dict = Depends(partner_admin)):
    return bills.public(await bills.query(user["node_id"], await bills.get_bill(user["node_id"], bill_id), payload.get("note", ""), user))


@router.post("/bills/{bill_id}/flags/{code}/clear")
async def bill_clear_flag(bill_id: str, code: str, user: dict = Depends(partner_admin)):
    return bills.public(await bills.clear_flag(user["node_id"], await bills.get_bill(user["node_id"], bill_id), code, user))


@router.post("/bills/{bill_id}/void")
async def bill_void(bill_id: str, payload: dict, user: dict = Depends(partner_admin)):
    return bills.public(await bills.void(user["node_id"], await bills.get_bill(user["node_id"], bill_id), payload.get("note", ""), user))


# ============================== Xero ============================== #

async def _xero_invoice(invoice_id: str):
    try:
        await xero_mt.post_invoice(invoice_id)
    except Exception as e:  # noqa: BLE001  (stays queued; nothing is lost)
        print(f"[xero] invoice post deferred: {e}")


async def _xero_payment(node_id: str, payment_id: str):
    try:
        await xero_mt.queue_payment(node_id, payment_id)
    except Exception as e:  # noqa: BLE001
        print(f"[xero] payment post deferred: {e}")


@router.get("/xero")
async def xero_status(user: dict = Depends(current_partner)):
    c = await xero_mt.connection(user["node_id"])
    waiting = await db.invoices().count_documents({"node_id": user["node_id"], "xero.status": {"$in": ["waiting", "failed"]}})
    out = xero_mt.public_view(c)
    out["waiting"] = waiting
    if c and user["role"] == "partner_admin":
        out["reference"] = c.get("reference")
    return out


@router.get("/xero/connect")
async def xero_connect(user: dict = Depends(partner_admin)):
    return {"url": xero_mt.authorize_url(user["node_id"], user["email"])}


@router.get("/xero/callback")
async def xero_callback(code: str = "", state: str = "", error: str = ""):
    """Xero redirects here. The signed state names the node; the browser goes back to the
    node's own Settings > Xero."""
    try:
        st = xero_mt.verify_state(state)
        node = await commerce.get_node(st["node_id"])
        target = f"https://{node['slug']}.vezubunye.com/settings/xero" if node.get("slug") else "/"
        if error or not code:
            return RedirectResponse(f"{target}?error={error or 'cancelled'}")
        await xero_mt.callback(code, state)
        try:
            await xero_mt.sync_reference(st["node_id"])
            await xero_mt.process_queue(st["node_id"])
        except Exception:  # noqa: BLE001
            pass
        return RedirectResponse(f"{target}?connected=1")
    except xero_mt.XeroAuthError as e:
        raise HTTPException(400, str(e))


@router.put("/xero/settings")
async def xero_settings(payload: dict, user: dict = Depends(partner_admin)):
    c = await xero_mt.connection(user["node_id"])
    if not c:
        raise HTTPException(400, "Connect Xero first")
    s = {**(c.get("settings") or xero_mt.default_settings()), **{k: v for k, v in payload.items()
                                                                 if k in xero_mt.default_settings()}}
    if s["post_status"] not in ("DRAFT", "AUTHORISED"):
        raise HTTPException(400, "Post status is DRAFT or AUTHORISED")
    if s.get("yoco_in_xero", "match") not in ("match", "clearing"):
        raise HTTPException(400, "Yoco in Xero is match or clearing")
    await xero_mt.conns().update_one({"node_id": user["node_id"]}, {"$set": {"settings": s}})
    await audit.log(user, user["node_id"], "update", "xero_settings", None, before=c.get("settings"), after=s)
    return s


@router.post("/xero/sync")
async def xero_sync(user: dict = Depends(partner_admin)):
    return await xero_mt.nightly(user["node_id"])


@router.delete("/xero")
async def xero_disconnect(user: dict = Depends(partner_admin)):
    await xero_mt.disconnect(user["node_id"], user)
    return {"disconnected": True}


# ============================== Yoco ============================== #

@router.get("/yoco")
async def yoco_status(user: dict = Depends(current_partner)):
    return await yoco.status(user["node_id"])


@router.put("/yoco")
async def yoco_key(payload: dict, user: dict = Depends(partner_admin)):
    return await yoco.save_key(user["node_id"], payload.get("secret_key", ""), user)


@router.delete("/yoco")
async def yoco_disconnect(user: dict = Depends(partner_admin)):
    await yoco.disconnect(user["node_id"], user)
    return {"disconnected": True}


@router.post("/yoco/webhook/{node_id}")
async def yoco_webhook(node_id: str, request: Request):
    """Not a user route: the node comes from the path and is proven by its signing secret."""
    body = await request.body()
    return await yoco.handle_webhook(node_id, {k.lower(): v for k, v in request.headers.items()}, body)


@router.get("/pay/{token}")
async def pay(token: str):
    """Public: the customer opened the business's pay link."""
    return await yoco.open_link(token)
