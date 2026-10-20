"""Xero, one organisation per node. Ported from RIPS xero_api (auth, refresh lock, keep-alive,
request wrapper with 429 handling, call log and daily cap) and made multi-tenant: every call
takes the node's connection from `xero_connections`, keyed by node_id.

Posts: sales invoices (ACCREC, our number, order number in the reference, PDF attached),
Yoco and cash payments against them (one Xero payment per allocation), bills (ACCPAY,
documents attached). Pulls: reference data, invoice and bill statuses, and payments on our
posted invoices: EFT the partner reconciled in Xero becomes a Vezubunye payment; payments
Vezubunye posted are skipped; a payment removed in Xero voids ours.

A broken connection never loses work: invoices, payments and bills wait as "waiting for Xero"
and post after reconnect.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException

import audit
import corrections
import crypto
import db
from corrections import ACTIVE

CLIENT_ID = os.getenv("XERO_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("XERO_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("XERO_REDIRECT_URI", "https://api.vezubunye.com/api/p/xero/callback")
# Granular scopes (apps created after 2 March 2026 cannot ask for accounting.transactions).
# Overridable so a scope Xero rejects at connect time is an env change, not a deploy.
DEFAULT_SCOPES = ("openid profile email offline_access accounting.settings.read accounting.contacts "
                  "accounting.invoices accounting.payments accounting.attachments")
SCOPES = os.getenv("XERO_SCOPES", DEFAULT_SCOPES)
AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
TOKEN_URL = "https://identity.xero.com/connect/token"
REVOKE_URL = "https://identity.xero.com/connect/revocation"
CONNECTIONS_URL = "https://api.xero.com/connections"
API_BASE = "https://api.xero.com/api.xro/2.0"
DAILY_CALL_CAP = int(os.getenv("XERO_DAILY_CALL_CAP", "1000"))
REFRESH_SKEW_SECONDS = 120
REFRESH_LOCK_SECONDS = 30
MAX_429_RETRIES = 3
SYSTEM = {"email": "system:xero", "role": "system", "employer": None}

transport: httpx.AsyncBaseTransport | None = None   # tests


class XeroAuthError(RuntimeError):
    pass


class XeroApiError(RuntimeError):
    def __init__(self, status: int, message: str, body: Any = None):
        super().__init__(f"Xero API {status}: {message}")
        self.status, self.message, self.body = status, message, body


def configured() -> bool:
    return bool(CLIENT_ID and CLIENT_SECRET and REDIRECT_URI)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(d):
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=60, transport=transport)


def conns():
    return db.xero_connections()


def contacts_coll():
    return db.db()["xero_contacts"]


# ============================== OAuth ============================== #

def _state_secret() -> str:
    import partner_auth
    return partner_auth.PARTNER_JWT_SECRET


def make_state(node_id: str, email: str) -> str:
    """Signed OAuth state carrying the node and the user who started the connect."""
    return jwt.encode({"node_id": node_id, "sub": email, "nonce": secrets.token_urlsafe(8),
                       "purpose": "xero_connect", "exp": _now() + timedelta(minutes=10)},
                      _state_secret(), algorithm="HS256")


def verify_state(state: str) -> dict:
    try:
        p = jwt.decode(state, _state_secret(), algorithms=["HS256"])
    except jwt.PyJWTError as e:
        raise XeroAuthError(f"Invalid state: {e}") from e
    if p.get("purpose") != "xero_connect":
        raise XeroAuthError("Invalid state purpose")
    return p


def authorize_url(node_id: str, email: str) -> str:
    if not configured():
        raise HTTPException(400, "Xero is not configured on the server (XERO_CLIENT_ID / XERO_CLIENT_SECRET)")
    return f"{AUTHORIZE_URL}?{urlencode({'response_type': 'code', 'client_id': CLIENT_ID, 'redirect_uri': REDIRECT_URI, 'scope': SCOPES, 'state': make_state(node_id, email)})}"


def _basic() -> str:
    return "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()


async def _token_request(form: dict) -> dict:
    async with _client() as c:
        r = await c.post(TOKEN_URL, data=form, headers={"Authorization": _basic(),
                                                       "Content-Type": "application/x-www-form-urlencoded"})
    if r.status_code >= 400:
        raise XeroAuthError(f"Token endpoint {r.status_code}: {r.text[:300]}")
    return r.json()


def _token_fields(tokens: dict) -> dict:
    return {"access_token": crypto.encrypt(tokens["access_token"]),
            "refresh_token": crypto.encrypt(tokens["refresh_token"]),
            "expires_at": _now() + timedelta(seconds=int(tokens.get("expires_in") or 1800)),
            "scopes": (tokens.get("scope") or SCOPES).split()}


async def callback(code: str, state: str) -> dict:
    """Exchange the code, list the organisations, connect the node to one. A node is one
    business with its own org; if the user authorised several, the first is used and the
    others are listed so a partner admin can switch."""
    st = verify_state(state)
    node_id = st["node_id"]
    tokens = await _token_request({"grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI})
    async with _client() as c:
        r = await c.get(CONNECTIONS_URL, headers={"Authorization": f"Bearer {tokens['access_token']}"})
    if r.status_code >= 400:
        raise XeroAuthError(f"Connections endpoint {r.status_code}")
    orgs = [o for o in r.json() if o.get("tenantType", "ORGANISATION") == "ORGANISATION"]
    if not orgs:
        raise XeroAuthError("Xero returned no organisation")
    org = orgs[0]
    prior = await conns().find_one({"node_id": node_id}) or {}
    doc = {**_token_fields(tokens), "node_id": node_id, "tenant_id": org["tenantId"],
           "org_name": org.get("tenantName", ""), "other_orgs": [{"tenant_id": o["tenantId"], "name": o.get("tenantName")} for o in orgs[1:]],
           "status": "connected", "connected_by": st["sub"], "connected_at": _now(),
           "last_refreshed_at": _now(), "refresh_lock_until": None, "last_error": None,
           "settings": prior.get("settings") or default_settings()}
    await conns().update_one({"node_id": node_id}, {"$set": doc}, upsert=True)
    await audit.log({"email": st["sub"], "role": "partner_admin", "employer": None}, node_id, "connect", "xero",
                    None, after={"org_name": doc["org_name"], "tenant_id": doc["tenant_id"]})
    return {"node_id": node_id, "org_name": doc["org_name"]}


def default_settings() -> dict:
    return {"post_status": "DRAFT", "sales_accounts": {"water": "", "septic": ""}, "charge_accounts": {},
            "tax_type": "OUTPUT", "bill_tax_type": "INPUT", "yoco_clearing_account": "", "cash_account": "",
            "tracking_category_id": None, "tracking_option_id": None}


async def connection(node_id: str) -> dict | None:
    return await conns().find_one({"node_id": node_id})


def public_view(c: dict | None) -> dict:
    if not c:
        return {"connected": False, "configured": configured()}
    return {"connected": c.get("status") == "connected", "status": c.get("status"), "org_name": c.get("org_name"),
            "tenant_id": c.get("tenant_id"), "connected_by": c.get("connected_by"), "connected_at": c.get("connected_at"),
            "last_sync": c.get("last_sync"), "last_error": c.get("last_error"), "calls_today": c.get("calls_today", 0),
            "settings": c.get("settings") or default_settings(), "other_orgs": c.get("other_orgs", []),
            "configured": configured()}


async def disconnect(node_id: str, user: dict):
    c = await connection(node_id)
    if c:
        try:
            async with _client() as cl:
                await cl.post(REVOKE_URL, data={"token": crypto.decrypt(c["refresh_token"])},
                              headers={"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"})
        except Exception:  # noqa: BLE001
            pass
        await conns().delete_one({"node_id": node_id})
        await audit.log(user, node_id, "disconnect", "xero", None)


async def mark_broken(node_id: str, reason: str):
    await conns().update_one({"node_id": node_id}, {"$set": {"status": "broken", "last_error": reason[:500],
                                                             "refresh_lock_until": None}})


async def _refresh_locked(c: dict) -> dict:
    try:
        tokens = await _token_request({"grant_type": "refresh_token", "refresh_token": crypto.decrypt(c["refresh_token"])})
    except XeroAuthError as e:
        await mark_broken(c["node_id"], str(e))
        raise
    f = {**_token_fields(tokens), "last_refreshed_at": _now(), "status": "connected", "last_error": None,
         "refresh_lock_until": None}
    await conns().update_one({"node_id": c["node_id"]}, {"$set": f})
    return {**c, **f}


async def ensure_access_token(node_id: str, force: bool = False) -> tuple[str, dict]:
    """Xero rotates refresh tokens, so two concurrent refreshes would strand the connection.
    A findOneAndUpdate lock serialises them; the loser waits for the winner's fresh token."""
    c = await connection(node_id)
    if not c:
        raise XeroAuthError("Xero is not connected")
    if c.get("status") == "broken":
        raise XeroAuthError(f"Xero connection is broken: {c.get('last_error') or 'reconnect required'}")
    exp = _aware(c.get("expires_at")) or _now()
    if (exp - _now()).total_seconds() > REFRESH_SKEW_SECONDS and not force:
        return crypto.decrypt(c["access_token"]), c
    deadline = time.monotonic() + REFRESH_LOCK_SECONDS + 5
    stale = c.get("last_refreshed_at")
    while True:
        now = _now()
        won = await conns().find_one_and_update(
            {"node_id": node_id, "status": "connected",
             "$or": [{"refresh_lock_until": None}, {"refresh_lock_until": {"$lt": now}}]},
            {"$set": {"refresh_lock_until": now + timedelta(seconds=REFRESH_LOCK_SECONDS)}})
        if won is not None:
            latest = await connection(node_id)
            lexp = _aware(latest.get("expires_at")) or now
            if (lexp - now).total_seconds() > REFRESH_SKEW_SECONDS and (not force or latest.get("last_refreshed_at") != stale):
                await conns().update_one({"node_id": node_id}, {"$set": {"refresh_lock_until": None}})
                return crypto.decrypt(latest["access_token"]), latest
            fresh = await _refresh_locked(latest)
            return crypto.decrypt(fresh["access_token"]), fresh
        await asyncio.sleep(0.2)
        latest = await connection(node_id)
        if not latest or latest.get("status") == "broken":
            raise XeroAuthError("Xero connection is broken: reconnect required")
        if latest.get("last_refreshed_at") != stale:
            return crypto.decrypt(latest["access_token"]), latest
        if time.monotonic() > deadline:
            raise XeroAuthError("Timed out waiting for token refresh")


async def keep_alive() -> int:
    n = 0
    async for c in conns().find({"status": "connected"}):
        try:
            await ensure_access_token(c["node_id"], force=True)
            n += 1
        except XeroAuthError as e:
            print(f"[xero] keep-alive failed for {c.get('org_name')}: {e}")
    return n


# ============================== requests ============================== #

async def _count(node_id: str) -> int:
    key = f"{node_id}:{_now().strftime('%Y-%m-%d')}"
    doc = await db.counters().find_one_and_update({"_id": f"xero:{key}"}, {"$inc": {"seq": 1}},
                                                  upsert=True, return_document=True)
    n = (doc or {}).get("seq", 1)
    await conns().update_one({"node_id": node_id}, {"$set": {"calls_today": n}})
    return n


def _err(resp: httpx.Response) -> tuple[str, Any]:
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        return resp.text[:500], None
    msgs = []
    if isinstance(body, dict):
        for k in ("Message", "Detail"):
            if body.get(k):
                msgs.append(str(body[k]))
        for el in body.get("Elements") or []:
            for ve in el.get("ValidationErrors") or []:
                if ve.get("Message"):
                    msgs.append(ve["Message"])
    return ("; ".join(msgs) or resp.text[:500]), body


async def request(node_id: str, method: str, path: str, *, params=None, json_body=None, content=None,
                  headers=None, accept_json: bool = True) -> Any:
    url = path if path.startswith("http") else f"{API_BASE}/{path.lstrip('/')}"
    token, c = await ensure_access_token(node_id)
    refreshed = False
    retries = 0
    async with _client() as cl:
        while True:
            if await _count(node_id) > DAILY_CALL_CAP:
                raise XeroApiError(429, "Daily Xero call cap reached for this organisation")
            h = {"Authorization": f"Bearer {token}", "Xero-Tenant-Id": c["tenant_id"],
                 **({"Accept": "application/json"} if accept_json else {}), **(headers or {})}
            started = time.monotonic()
            try:
                resp = await cl.request(method, url, params=params, json=json_body, content=content, headers=h)
            except httpx.HTTPError as e:
                await db.xero_calls().insert_one({"node_id": node_id, "at": _now(), "method": method, "endpoint": path,
                                                  "error": str(e)[:300]})
                raise XeroApiError(0, f"{type(e).__name__}: {e}")
            await db.xero_calls().insert_one({"node_id": node_id, "at": _now(), "method": method,
                                              "endpoint": path.split("?")[0], "status": resp.status_code,
                                              "duration_ms": int((time.monotonic() - started) * 1000)})
            if resp.status_code == 401 and not refreshed:
                refreshed = True
                token, c = await ensure_access_token(node_id, force=True)
                continue
            if resp.status_code == 429 and retries < MAX_429_RETRIES:
                retries += 1
                try:
                    delay = max(1.0, float(resp.headers.get("Retry-After") or 5))
                except ValueError:
                    delay = 5.0
                await asyncio.sleep(delay)
                continue
            if resp.status_code >= 400:
                msg, body = _err(resp)
                raise XeroApiError(resp.status_code, msg, body)
            if not accept_json:
                return resp.content
            return resp.json() if resp.content else None


def _idem(kind: str, rid: str, payload: Any) -> str:
    d = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:24]
    return f"vz-{kind}-{rid}-{d}"[:128]


# ============================== reference data ============================== #

async def sync_reference(node_id: str) -> dict:
    accounts = (await request(node_id, "GET", "Accounts") or {}).get("Accounts") or []
    taxes = (await request(node_id, "GET", "TaxRates") or {}).get("TaxRates") or []
    tracking = (await request(node_id, "GET", "TrackingCategories") or {}).get("TrackingCategories") or []
    ref = {"accounts": [{"code": a.get("Code", ""), "name": a.get("Name", ""), "type": a.get("Type", ""),
                         "tax_type": a.get("TaxType", ""), "enable_payments": bool(a.get("EnablePaymentsToAccount")),
                         "status": a.get("Status")} for a in accounts if a.get("Status") == "ACTIVE"],
           "tax_rates": [{"tax_type": t["TaxType"], "name": t.get("Name", ""), "rate": float(t.get("EffectiveRate") or 0)}
                         for t in taxes if t.get("Status") == "ACTIVE"],
           "tracking": [{"tracking_category_id": t["TrackingCategoryID"], "name": t.get("Name", ""),
                         "options": [{"tracking_option_id": o["TrackingOptionID"], "name": o.get("Name", "")}
                                     for o in t.get("Options") or []]} for t in tracking]}
    await conns().update_one({"node_id": node_id}, {"$set": {"reference": ref, "last_sync": _now()}})
    return {k: len(v) for k, v in ref.items()}


# ============================== contacts ============================== #

async def ensure_contact(node_id: str, record: dict, coll) -> str:
    """Link a customer or supplier to a Xero contact: an existing contact matched by name and
    VAT number first, else created. Stores xero_contact_id on the record."""
    if record.get("xero_contact_id"):
        return record["xero_contact_id"]
    name = record["name"].replace('"', '\\"')
    found = (await request(node_id, "GET", "Contacts", params={"where": f'Name=="{name}"'}) or {}).get("Contacts") or []
    vat = (record.get("vat_number") or "").strip()
    match = next((c for c in found if not vat or (c.get("TaxNumber") or "").strip() in ("", vat)), None)
    if match:
        cid = match["ContactID"]
    else:
        body = {"Contacts": [{"Name": record["name"], "EmailAddress": record.get("email") or None,
                              "TaxNumber": vat or None,
                              "Phones": [{"PhoneType": "MOBILE", "PhoneNumber": record.get("phone")}] if record.get("phone") else []}]}
        created = await request(node_id, "PUT", "Contacts", json_body=body,
                                headers={"Idempotency-Key": _idem("contact", record["_id"], body)})
        cid = created["Contacts"][0]["ContactID"]
    await coll.update_one({"_id": record["_id"]}, {"$set": {"xero_contact_id": cid}})
    return cid


# ============================== sales invoices ============================== #

def invoice_payload(inv: dict, cfg: dict, settings: dict, contact_id: str, status: str) -> dict:
    tanks = {t["code"]: t for t in cfg["tank_types"]}
    charges = {c["code"]: c for c in cfg.get("charges", []) or []}
    incl = all(ln.get("price_includes_vat") for ln in inv["lines"])
    items = []
    import commerce
    for ln in inv["lines"]:
        if ln.get("kind") == "charge":
            acct = (settings.get("charge_accounts") or {}).get(ln["code"]) or charges.get(ln["code"], {}).get("xero_account") or ""
        else:
            acct = (settings.get("sales_accounts") or {}).get(tanks.get(ln["tank_type"], {}).get("kind", "water"), "")
        qty = ln.get("qty", ln.get("quantity", 1))
        unit = (ln["unit_incl"] if incl else ln["unit_ex"]) if "unit_ex" in ln else ln.get("unit_price", 0)
        item = {"Description": commerce.line_label(cfg, ln), "Quantity": qty,
                "UnitAmount": round(ln["line_total"] / qty, 4) if incl and qty else unit,
                "AccountCode": acct, "TaxType": settings.get("tax_type") or "OUTPUT"}
        if settings.get("tracking_category_id") and settings.get("tracking_option_id"):
            item["Tracking"] = [{"TrackingCategoryID": settings["tracking_category_id"],
                                 "TrackingOptionID": settings["tracking_option_id"]}]
        items.append(item)
    return {"Invoices": [{"Type": "ACCREC", "Contact": {"ContactID": contact_id},
                          "InvoiceNumber": inv["invoice_number"], "Reference": inv.get("order_number") or "",
                          "Date": inv["date"], "DueDate": inv["due_date"], "CurrencyCode": "ZAR",
                          "LineAmountTypes": "Inclusive" if incl else "Exclusive",
                          "Status": status, "LineItems": items}]}


async def post_invoice(invoice_id: str) -> dict:
    inv = await db.invoices().find_one({"_id": invoice_id})
    if not inv or inv.get("legacy"):
        return {"skipped": True}
    node_id = inv["node_id"]
    c = await connection(node_id)
    if not c:
        return {"skipped": "no_xero"}
    import commerce
    import partner_pdf
    cfg = await commerce.get_cfg(node_id)
    settings = c.get("settings") or default_settings()
    if inv.get("void") and not (inv.get("xero") or {}).get("invoice_id"):
        return {"skipped": "void, never posted"}
    try:
        if not (inv.get("xero") or {}).get("invoice_id"):
            cust = await db.customers().find_one({"_id": inv["customer_id"]})
            cid = await ensure_contact(node_id, cust, db.customers())
            payload = invoice_payload(inv, cfg, settings, cid, "AUTHORISED" if settings.get("post_status") == "AUTHORISED" else "DRAFT")
            body = await request(node_id, "PUT", "Invoices", json_body=payload,
                                 headers={"Idempotency-Key": _idem("inv", invoice_id, payload)})
            x = body["Invoices"][0]
            await db.invoices().update_one({"_id": invoice_id}, {"$set": {"xero": {
                "status": "posted", "invoice_id": x["InvoiceID"], "xero_status": x.get("Status"),
                "url": f"https://go.xero.com/AccountsReceivable/View.aspx?InvoiceID={x['InvoiceID']}",
                "posted_at": _now()}}})
            node = await commerce.get_node(node_id)
            node["_logo_png"] = await logo_png(node_id)
            pdf = partner_pdf.invoice(node, cfg, inv, cust)
            await request(node_id, "PUT", f"Invoices/{x['InvoiceID']}/Attachments/{inv['invoice_number']}.pdf",
                          content=pdf, headers={"Content-Type": "application/pdf"})
        if ((inv.get("xero") or {}).get("void_pending") or inv.get("void")) and not (inv.get("xero") or {}).get("void_sent"):
            xid = (await db.invoices().find_one({"_id": invoice_id}))["xero"]["invoice_id"]
            await request(node_id, "POST", f"Invoices/{xid}", json_body={"Invoices": [{"InvoiceID": xid, "Status": "VOIDED"}]})
            await db.invoices().update_one({"_id": invoice_id}, {"$set": {"xero.void_pending": False, "xero.void_sent": True}})
        return {"posted": True}
    except XeroAuthError as e:
        await db.invoices().update_one({"_id": invoice_id}, {"$set": {"xero.status": "waiting", "xero.error": str(e)}})
        return {"waiting": str(e)}
    except XeroApiError as e:
        await db.invoices().update_one({"_id": invoice_id}, {"$set": {"xero.status": "failed", "xero.error": e.message}})
        return {"failed": e.message}


async def logo_png(node_id: str) -> bytes | None:
    a = await db.brand_assets().find_one({"node_id": node_id, "kind": "logo_light_png"})
    return base64.b64decode(a["content_b64"]) if a else None


async def queue_payment(node_id: str, payment_id: str):
    """Mark a Yoco or cash payment for posting; post now when connected."""
    p = await db.payments().find_one({"_id": payment_id})
    if not p or p.get("method") not in ("yoco", "cash") or not await connection(node_id):
        return
    await db.payments().update_one({"_id": payment_id}, {"$set": {"xero_status": "queued"}})
    await post_payment(payment_id)


async def post_payment(payment_id: str) -> dict:
    p = await db.payments().find_one({"_id": payment_id, **ACTIVE})
    if not p:
        return {"skipped": True}
    node_id = p["node_id"]
    c = await connection(node_id)
    if not c:
        return {"skipped": "no_xero"}
    s = c.get("settings") or default_settings()
    acct = s.get("cash_account") if p["method"] == "cash" else s.get("yoco_clearing_account")
    posted = dict(p.get("xero_payment_ids") or {})
    try:
        for a in p.get("allocations") or []:
            if a["invoice_id"] in posted:
                continue
            inv = await db.invoices().find_one({"_id": a["invoice_id"]})
            xid = ((inv or {}).get("xero") or {}).get("invoice_id")
            if not xid:
                r = await post_invoice(a["invoice_id"])
                inv = await db.invoices().find_one({"_id": a["invoice_id"]})
                xid = ((inv or {}).get("xero") or {}).get("invoice_id")
                if not xid:
                    raise XeroAuthError(f"Invoice not in Xero yet: {r}")
            body = {"Payments": [{"Invoice": {"InvoiceID": xid}, "Account": {"Code": acct}, "Date": p["date"],
                                  "Amount": a["amount"], "Reference": p.get("reference") or p["method"]}]}
            res = await request(node_id, "PUT", "Payments", json_body=body,
                                headers={"Idempotency-Key": _idem("pay", f"{payment_id}-{a['invoice_id']}", body)})
            posted[a["invoice_id"]] = res["Payments"][0]["PaymentID"]
            await db.payments().update_one({"_id": payment_id}, {"$set": {"xero_payment_ids": posted}})
        await db.payments().update_one({"_id": payment_id}, {"$set": {"xero_status": "posted", "posted_to_xero": True}})
        return {"posted": len(posted)}
    except XeroAuthError as e:
        await db.payments().update_one({"_id": payment_id}, {"$set": {"xero_status": "waiting", "xero_error": str(e)}})
        return {"waiting": str(e)}
    except XeroApiError as e:
        await db.payments().update_one({"_id": payment_id}, {"$set": {"xero_status": "failed", "xero_error": e.message}})
        return {"failed": e.message}


# ============================== pulls ============================== #

async def pull_invoice_payments(node_id: str) -> dict:
    """EFT reconciled in Xero against our invoices becomes a Vezubunye payment (one Xero payment
    per invoice; grouped by bank transaction for display via `xero_batch`). Payments Vezubunye
    posted itself are skipped. A Xero payment that disappears voids ours."""
    import commerce
    ids = {}
    async for inv in db.invoices().find({"node_id": node_id, "xero.invoice_id": {"$exists": True}}):
        ids[inv["xero"]["invoice_id"]] = inv
    if not ids:
        return {"new": 0, "removed": 0}
    ours = set()
    async for p in db.payments().find({"node_id": node_id, **ACTIVE}):
        for v in (p.get("xero_payment_ids") or {}).values():
            ours.add(v)
        if p.get("xero_payment_id"):
            ours.add(p["xero_payment_id"])
    new = removed = 0
    seen = set()
    keys = list(ids)
    for i in range(0, len(keys), 40):
        body = await request(node_id, "GET", "Invoices", params={"IDs": ",".join(keys[i:i + 40])})
        for x in (body or {}).get("Invoices") or []:
            inv = ids[x["InvoiceID"]]
            await db.invoices().update_one({"_id": inv["_id"]}, {"$set": {"xero.xero_status": x.get("Status")}})
            for xp in x.get("Payments") or []:
                pid = xp["PaymentID"]
                seen.add(pid)
                if pid in ours:
                    continue
                await commerce.record_payment(node_id, {
                    "amount": float(xp.get("Amount") or 0), "method": "eft", "reference": xp.get("Reference") or "",
                    "xero_payment_id": pid, "customer_id": inv.get("customer_id"),
                    "date": _xero_date(xp.get("Date")) or commerce.today()}, SYSTEM,
                    allocations=[{"invoice_id": inv["_id"], "amount": float(xp.get("Amount") or 0)}])
                await db.payments().update_one({"xero_payment_id": pid}, {"$set": {"xero_batch": xp.get("BatchPaymentID") or pid}})
                new += 1
    async for p in db.payments().find({"node_id": node_id, "method": "eft", "xero_payment_id": {"$ne": None}, **ACTIVE}):
        if p["xero_payment_id"] not in seen:
            await corrections.void_record("payments", p["_id"], SYSTEM, "Removed in Xero", moves_stock=False)
            for a in p.get("allocations") or []:
                await commerce.refresh_invoice(a["invoice_id"])
            removed += 1
    return {"new": new, "removed": removed}


def _xero_date(v) -> str | None:
    if not v:
        return None
    s = str(v)
    if s.startswith("/Date("):
        ms = int(s[6:].split("+")[0].split(")")[0])
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
    return s[:10]


async def process_queue(node_id: str) -> dict:
    """Post everything waiting (after reconnect and nightly)."""
    out = {"invoices": 0, "payments": 0, "bills": 0}
    if not await connection(node_id):
        return out
    async for inv in db.invoices().find({"node_id": node_id, "legacy": {"$ne": True},
                                         "$or": [{"xero.status": {"$in": ["not_posted", "waiting"]}, **ACTIVE},
                                                 {"xero.void_pending": True},
                                                 {"void": {"$exists": True}, "xero.invoice_id": {"$exists": True},
                                                  "xero.void_sent": {"$ne": True}}]}):
        if (await post_invoice(inv["_id"])).get("posted"):
            out["invoices"] += 1
    async for p in db.payments().find({"node_id": node_id, "xero_status": {"$in": ["queued", "waiting"]}, **ACTIVE}):
        if (await post_payment(p["_id"])).get("posted") is not None:
            out["payments"] += 1
    import bills
    async for b in db.bills().find({"node_id": node_id, "status": {"$in": ["approved", "posted_attachment_pending"]}}):
        if (await bills.post_bill(b["_id"])).get("status") == "posted":
            out["bills"] += 1
    return out


async def nightly(node_id: str) -> dict:
    out: dict = {}
    for name, fn in (("reference", sync_reference), ("payments", pull_invoice_payments), ("queue", process_queue)):
        try:
            out[name] = await fn(node_id)
        except (XeroAuthError, XeroApiError) as e:
            out[name] = {"error": str(e)}
    try:
        import bills
        out["bills"] = await bills.refresh_statuses(node_id)
    except (XeroAuthError, XeroApiError) as e:
        out["bills"] = {"error": str(e)}
    return out
