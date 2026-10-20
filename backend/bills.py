"""Bills, per node. Ported from RIPS Brief 15 (bills/, xero_api/bills.py, SOP_bills.md) with no
runtime dependency on RIPS.

Kept: photo or PDF capture, Claude extraction with confidence, supplier matching and defaults,
flags (new supplier, possible duplicate, same file, terms deviation, late capture, future date),
the query loop, approve is the post, bulk approve, Draft or Authorised posting, and the
duplicate guard (xero_invoice_id, then supplier + number in Xero).

Changed: every bill, supplier and document carries node_id. Capturers are the node's partner
staff and admins; the approver is a partner admin. No business-unit picker: the node-level
tracking option is applied when set. No GRN link. Documents live in GridFS in the Vezubunye
database (a base64 collection where GridFS is unavailable, e.g. the test mock). Fenix sees
every node's bills read-only.
"""
from __future__ import annotations

import base64
import hashlib
import os
from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import HTTPException

import audit
import bill_logic as logic
import db
import xero_mt
from xero_mt import XeroApiError, XeroAuthError

EXTRACT_MODEL = os.getenv("BILLS_EXTRACT_MODEL", "claude-opus-5-5")
EXTRACT_EFFORT = os.getenv("BILLS_EXTRACT_EFFORT", "medium")
EXTRACT_TIMEOUT_S = float(os.getenv("BILLS_EXTRACT_TIMEOUT_S", "90"))
MAX_DOC_BYTES = 25 * 1024 * 1024


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _history(user: dict, action: str, note: str | None = None) -> dict:
    return {"at": _now(), "who": user["email"], "employer": user.get("employer"), "action": action, "note": note}


# ============================== documents ============================== #

def _sniff(content: bytes, declared: str | None, filename: str) -> str:
    if content[:4] == b"%PDF":
        return "application/pdf"
    if content[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if content[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return declared or "application/octet-stream"


def _gridfs():
    try:
        from motor.motor_asyncio import AsyncIOMotorGridFSBucket
        if type(db.client()).__module__.startswith("mongomock"):
            return None
        return AsyncIOMotorGridFSBucket(db.db(), bucket_name="bill_files")
    except Exception:  # noqa: BLE001
        return None


async def store_document(node_id: str, content: bytes, filename: str, mime: str | None, user: dict) -> dict:
    if len(content) > MAX_DOC_BYTES:
        raise HTTPException(400, "File too large (max 25 MB)")
    mime = _sniff(content, mime, filename)
    if mime not in ("application/pdf", "image/jpeg", "image/png", "image/webp"):
        raise HTTPException(400, "Upload a PDF or a photo (JPEG, PNG)")
    sha = hashlib.sha256(content).hexdigest()
    doc = {"_id": uuid4().hex, "node_id": node_id, "filename": filename or "document", "mime": mime,
           "size": len(content), "sha256": sha, "uploaded_by": user["email"], "uploaded_at": _now()}
    bucket = _gridfs()
    if bucket is not None:
        doc["gridfs_id"] = await bucket.upload_from_stream(doc["filename"], content,
                                                           metadata={"node_id": node_id, "mime": mime})
    else:
        doc["content_b64"] = base64.b64encode(content).decode()
    await db.bill_documents().insert_one(doc)
    return {k: v for k, v in doc.items() if k not in ("content_b64", "gridfs_id")}


async def load_document(doc_id: str, node_id: str | None = None) -> tuple[bytes, str, str]:
    filt = {"_id": doc_id, **({"node_id": node_id} if node_id else {})}
    d = await db.bill_documents().find_one(filt)
    if not d:
        raise HTTPException(404, "Document not found")
    if d.get("gridfs_id") is not None:
        stream = await _gridfs().open_download_stream(d["gridfs_id"])
        return await stream.read(), d["filename"], d["mime"]
    return base64.b64decode(d["content_b64"]), d["filename"], d["mime"]


# ============================== extraction ============================== #

def _prompt(business: str) -> str:
    return (
        f"These are the document(s) for one supplier purchase captured by {business}, a South African "
        "water-tank manufacturer. Read them and fill the fields. Rules: dates as YYYY-MM-DD; amounts as "
        "plain numbers; if the document shows a VAT inclusive total only, put it in total_incl and leave "
        "total_excl null; if it shows no VAT at all, set shows_vat false and leave vat_amount null. "
        f"supplier_name is the supplier, never the customer (the customer is {business}). invoice_number "
        "is whatever number identifies this document. description is one short line in plain words. "
        "Confidence is 0 to 1 for each field, 0 when the field is null. Do not guess values that are not "
        "on the document. Answer with exactly one JSON object and nothing else, no code fence, in this shape:\n"
        + SCHEMA_TEXT)


SCHEMA_TEXT = """{
  "supplier_name": string|null, "supplier_vat_number": string|null, "invoice_number": string|null,
  "invoice_date": "YYYY-MM-DD"|null, "due_date": "YYYY-MM-DD"|null, "currency": string|null,
  "total_excl": number|null, "vat_amount": number|null, "total_incl": number|null, "shows_vat": boolean,
  "description": string|null, "is_paid_indicator": "paid"|"unpaid"|null,
  "document_type": "tax_invoice"|"till_slip"|"quote"|"statement"|"proof_of_payment"|"other"|null,
  "confidence": { field name: number 0 to 1 for each field above except shows_vat, 0 when null }
}"""


def parse_extraction(text: str) -> dict:
    import json
    import re
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise RuntimeError("The model returned no JSON")
    raw = json.loads(m.group(0))
    for k in ("total_excl", "vat_amount", "total_incl"):
        v = raw.get(k)
        if isinstance(v, str):
            try:
                raw[k] = float(re.sub(r"[^0-9.\-]", "", v.replace(",", ".")))
            except ValueError:
                raw[k] = None
    if raw.get("is_paid_indicator") not in ("paid", "unpaid"):
        raw["is_paid_indicator"] = None
    conf = raw.get("confidence") if isinstance(raw.get("confidence"), dict) else {}
    raw["confidence"] = {k: float(v) for k, v in conf.items() if isinstance(v, (int, float))}
    return raw


async def extract(node: dict, docs: list[tuple[bytes, str]]) -> dict:
    import anthropic
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        raise HTTPException(400, "Reading documents is not configured (ANTHROPIC_API_KEY)")
    blocks = []
    for content, mime in docs:
        data = base64.standard_b64encode(content).decode()
        kind = "document" if mime == "application/pdf" else "image"
        blocks.append({"type": kind, "source": {"type": "base64", "media_type": mime, "data": data}})
    business = (node.get("company") or {}).get("legal_name") or node["name"]
    blocks.append({"type": "text", "text": _prompt(business)})
    client = anthropic.AsyncAnthropic(api_key=key, timeout=EXTRACT_TIMEOUT_S, max_retries=1)
    resp = await client.messages.create(model=EXTRACT_MODEL, max_tokens=8000,
                                        output_config={"effort": EXTRACT_EFFORT},
                                        messages=[{"role": "user", "content": blocks}])
    if resp.stop_reason == "refusal":
        raise HTTPException(400, "The document could not be read")
    text = "".join(getattr(b, "text", "") for b in resp.content if getattr(b, "type", None) == "text")
    return {"fields": parse_extraction(text), "model": EXTRACT_MODEL, "extracted_at": _now()}


# ============================== bills ============================== #

def public(b: dict) -> dict:
    return {k: v for k, v in b.items() if k != "content_b64"}


async def get_bill(node_id: str, bill_id: str) -> dict:
    b = await db.bills().find_one({"_id": bill_id, "node_id": node_id})
    if not b:
        raise HTTPException(404, "Bill not found")
    return b


async def _bill_number(node_id: str) -> str:
    seq = await db.next_number(node_id, "bill")
    node = await db.nodes().find_one({"node_id": node_id}) or {}
    return f"{node.get('prefix', node_id.upper())}-BILL-{seq:04d}"


async def create(node_id: str, user: dict) -> dict:
    b = {"_id": uuid4().hex, "node_id": node_id, "bill_number": await _bill_number(node_id), "status": "draft",
         "documents": [], "lines": [], "flags": [], "history": [_history(user, "create")],
         "captured_by": user["email"], "captured_by_employer": user.get("employer"),
         "amounts_are": "inclusive", "payment_state": None, "currency": "ZAR",
         "date": date.today().isoformat(), "created_at": _now()}
    await db.bills().insert_one(b)
    await audit.log(user, node_id, "create", "bills", b["_id"], after=b)
    return b


async def attach(node_id: str, bill: dict, content: bytes, filename: str, mime: str | None, user: dict) -> dict:
    if bill["status"] not in ("draft", "queried"):
        raise HTTPException(400, "Documents are added while the bill is a draft or queried")
    d = await store_document(node_id, content, filename, mime, user)
    await db.bills().update_one({"_id": bill["_id"]}, {"$push": {"documents": d["_id"], "history": _history(user, "upload", filename)}})
    return d


async def run_extraction(node: dict, bill: dict, user: dict) -> dict:
    docs = []
    for did in bill.get("documents") or []:
        content, _, mime = await load_document(did, node["node_id"])
        docs.append((content, mime))
    if not docs:
        raise HTTPException(400, "Upload the invoice or slip first")
    ext = await extract(node, docs)
    f = ext["fields"]
    sup = await match_supplier(node["node_id"], f.get("supplier_name"), f.get("supplier_vat_number"))
    upd = {"extraction": ext, "invoice_number": bill.get("invoice_number") or f.get("invoice_number"),
           "invoice_date": bill.get("invoice_date") or f.get("invoice_date"),
           "description": bill.get("description") or f.get("description"),
           "stated_total": f.get("total_incl"), "stated_vat": f.get("vat_amount"),
           "supplier_name": bill.get("supplier_name") or f.get("supplier_name"),
           "supplier_vat_number": f.get("supplier_vat_number")}
    if sup and not bill.get("supplier_id"):
        upd.update(supplier_id=sup["contact_id"], supplier_match=sup)
    if not bill.get("lines") and f.get("total_incl") is not None:
        upd["lines"] = [{"description": f.get("description") or "", "quantity": 1, "unit_amount": f["total_incl"],
                         "account_code": None, "tax_type": None}]
    if f.get("is_paid_indicator") == "paid" and not bill.get("payment_state"):
        upd["payment_state"] = "paid_card"
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": upd, "$push": {"history": _history(user, "extract")}})
    return await apply_defaults(node["node_id"], await get_bill(node["node_id"], bill["_id"]))


# ---------- suppliers ---------- #

async def suppliers(node_id: str) -> list[dict]:
    return [s async for s in db.suppliers().find({"node_id": node_id}).sort("name", 1)]


async def match_supplier(node_id: str, name: str | None, vat: str | None) -> dict | None:
    cands = [{"_id": s["_id"], "name": s["name"], "vat_number": s.get("vat_number")} for s in await suppliers(node_id)]
    return logic.match_supplier(name, vat, cands)


async def upsert_supplier(node_id: str, raw: dict, user: dict) -> dict:
    sid = raw.get("_id") or uuid4().hex
    doc = {"node_id": node_id, "name": raw["name"].strip(), "vat_number": raw.get("vat_number", ""),
           "email": raw.get("email", ""), "phone": raw.get("phone", ""),
           "xero_contact_id": raw.get("xero_contact_id"),
           "default_account_code": raw.get("default_account_code"), "default_tax_type": raw.get("default_tax_type"),
           "payment_terms_type": raw.get("payment_terms_type"), "payment_terms_days": raw.get("payment_terms_days"),
           "updated_at": _now(), "updated_by": user["email"]}
    await db.suppliers().update_one({"_id": sid, "node_id": node_id}, {"$set": doc}, upsert=True)
    await audit.log(user, node_id, "update", "suppliers", sid, after=doc)
    return {"_id": sid, **doc}


async def sync_suppliers_from_xero(node_id: str) -> int:
    """Pull Xero supplier contacts into the node's supplier list (matched by contact id)."""
    n = 0
    page = 1
    while True:
        body = await xero_mt.request(node_id, "GET", "Contacts", params={"where": "IsSupplier==true", "page": page})
        cs = (body or {}).get("Contacts") or []
        for c in cs:
            terms = (c.get("PaymentTerms") or {}).get("Bills") or {}
            existing = await db.suppliers().find_one({"node_id": node_id, "xero_contact_id": c["ContactID"]})
            fields = {"node_id": node_id, "name": c.get("Name", ""), "vat_number": c.get("TaxNumber") or "",
                      "xero_contact_id": c["ContactID"], "bill_terms_day": terms.get("Day"),
                      "bill_terms_type": terms.get("Type"), "updated_at": _now()}
            await db.suppliers().update_one({"_id": existing["_id"] if existing else uuid4().hex},
                                            {"$set": fields}, upsert=True)
            n += 1
        if len(cs) < 100:
            return n
        page += 1


# ---------- edit, totals, flags ---------- #

EDITABLE = ("supplier_id", "supplier_name", "invoice_number", "invoice_date", "due_date", "description",
            "payment_state", "paid_date", "amounts_are", "lines", "stated_total", "stated_vat", "currency", "notes")


async def _tax_rates(node_id: str) -> dict:
    c = await xero_mt.connection(node_id) or {}
    rates = {t["tax_type"]: t["rate"] for t in (c.get("reference") or {}).get("tax_rates", [])}
    return rates or {"INPUT": 15.0, "NONE": 0.0, "EXEMPTINPUT": 0.0, "ZERORATEDINPUT": 0.0}


async def apply_defaults(node_id: str, bill: dict) -> dict:
    sup = await db.suppliers().find_one({"_id": bill.get("supplier_id"), "node_id": node_id}) if bill.get("supplier_id") else None
    c = await xero_mt.connection(node_id) or {}
    settings = c.get("settings") or xero_mt.default_settings()
    lines = []
    for ln in bill.get("lines") or []:
        ln = dict(ln)
        if not ln.get("account_code") and sup:
            ln["account_code"] = sup.get("default_account_code")
        if not ln.get("tax_type"):
            ln["tax_type"] = (sup or {}).get("default_tax_type") or settings.get("bill_tax_type") or "INPUT"
        lines.append(ln)
    tot = logic.compute_totals(lines, bill.get("amounts_are") or "inclusive", await _tax_rates(node_id))
    profile = {"payment_terms_type": (sup or {}).get("payment_terms_type"),
               "payment_terms_days": (sup or {}).get("payment_terms_days")} if sup else None
    contact = {"bill_terms_day": (sup or {}).get("bill_terms_day"), "bill_terms_type": (sup or {}).get("bill_terms_type")} if sup else None
    due, source = logic.compute_due_date(bill.get("invoice_date"), bill.get("payment_state") or "unpaid",
                                         bill.get("paid_date"), profile, contact)
    upd = {"lines": tot["lines"], "total_excl": tot["total_excl"], "total_tax": tot["total_tax"],
           "total_incl": tot["total_incl"], "due_date_computed": due, "due_source": source,
           "xero_contact_id": (sup or {}).get("xero_contact_id")}
    if not bill.get("due_date_locked"):
        upd["due_date"] = due
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": upd})
    return {**bill, **upd}


async def edit(node_id: str, bill: dict, raw: dict, user: dict) -> dict:
    if bill["status"] not in ("draft", "queried", "submitted") or (bill["status"] == "submitted" and user["role"] != "partner_admin"):
        raise HTTPException(400, f"A {bill['status']} bill cannot be edited")
    upd = {k: raw[k] for k in EDITABLE if k in raw}
    if "due_date" in raw:
        upd["due_date_locked"] = bool(raw["due_date"])
    if raw.get("new_supplier"):
        sup = await upsert_supplier(node_id, {"name": raw["new_supplier"]["name"],
                                              "vat_number": raw["new_supplier"].get("vat_number", "")}, user)
        upd.update(supplier_id=sup["_id"], supplier_name=sup["name"], new_supplier=True)
    if upd.get("supplier_id"):
        s = await db.suppliers().find_one({"_id": upd["supplier_id"], "node_id": node_id})
        if not s:
            raise HTTPException(400, "Supplier not found")
        upd["supplier_name"] = s["name"]
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": upd, "$push": {"history": _history(user, "edit")}})
    return await apply_defaults(node_id, {**bill, **upd})


async def _flags(node_id: str, bill: dict) -> list[dict]:
    blocks = logic.submit_blocks({**bill, "xero_contact_id": bill.get("supplier_id")}, False)
    same_sup = [b async for b in db.bills().find({"node_id": node_id, "supplier_id": bill.get("supplier_id"),
                                                  "_id": {"$ne": bill["_id"]}})] if bill.get("supplier_id") else []
    shas = set()
    for did in bill.get("documents") or []:
        d = await db.bill_documents().find_one({"_id": did})
        if d:
            shas.add(d["sha256"])
    other_docs = [d["_id"] async for d in db.bill_documents().find({"node_id": node_id, "sha256": {"$in": list(shas)},
                                                                    "_id": {"$nin": bill.get("documents") or []}})]
    same_file = [b async for b in db.bills().find({"node_id": node_id, "documents": {"$in": other_docs}})] if other_docs else []
    warns = logic.warn_flags(bill, date.today(), same_sup, same_file)
    if bill.get("new_supplier"):
        warns.append({"code": "new_supplier", "level": logic.WARN, "message": f"New supplier: {bill.get('supplier_name')}."})
    # duplicate guard: same supplier and number already captured
    num = logic.normalise_invoice_number(bill.get("invoice_number"))
    for o in same_sup:
        if num and logic.normalise_invoice_number(o.get("invoice_number")) == num and o.get("status") != "void":
            blocks.append({"code": "duplicate_number", "level": logic.BLOCK,
                           "message": f"{o['bill_number']} already has invoice {o.get('invoice_number')} from this supplier."})
    cleared = {f["code"]: f for f in bill.get("flags") or [] if f.get("cleared_at")}
    return [{**f, **({k: cleared[f["code"]][k] for k in ("cleared_at", "cleared_by")} if f["code"] in cleared else {})}
            for f in blocks + warns]


async def submit(node_id: str, bill: dict, user: dict) -> dict:
    if bill["status"] not in ("draft", "queried"):
        raise HTTPException(400, f"A {bill['status']} bill cannot be submitted")
    bill = await apply_defaults(node_id, bill)
    flags = await _flags(node_id, bill)
    if any(f["level"] == logic.BLOCK for f in flags):
        await db.bills().update_one({"_id": bill["_id"]}, {"$set": {"flags": flags}})
        raise HTTPException(400, "; ".join(f["message"] for f in flags if f["level"] == logic.BLOCK))
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": {"status": "submitted", "flags": flags, "submitted_at": _now()},
                                                       "$push": {"history": _history(user, "submit")}})
    await audit.log(user, node_id, "submit", "bills", bill["_id"], after={"flags": flags})
    return await get_bill(node_id, bill["_id"])


async def query(node_id: str, bill: dict, note: str, user: dict) -> dict:
    if not note.strip():
        raise HTTPException(400, "Say what needs fixing")
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": {"status": "queried", "query_note": note},
                                                       "$push": {"history": _history(user, "query", note)}})
    return await get_bill(node_id, bill["_id"])


async def clear_flag(node_id: str, bill: dict, code: str, user: dict) -> dict:
    flags = [{**f, "cleared_at": _now(), "cleared_by": user["email"]} if f["code"] == code else f for f in bill.get("flags") or []]
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": {"flags": flags}, "$push": {"history": _history(user, "clear_flag", code)}})
    return await get_bill(node_id, bill["_id"])


async def void(node_id: str, bill: dict, note: str, user: dict) -> dict:
    if not note.strip():
        raise HTTPException(400, "Reason required")
    upd = {"status": "void", "void": {"by": user["email"], "at": _now(), "reason": note}}
    if bill.get("xero_invoice_id"):
        upd["xero_void_pending"] = True
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": upd, "$push": {"history": _history(user, "void", note)}})
    await audit.log(user, node_id, "void", "bills", bill["_id"], before=public(bill), after=upd)
    if bill.get("xero_invoice_id"):
        try:
            await xero_mt.request(node_id, "POST", f"Invoices/{bill['xero_invoice_id']}",
                                  json_body={"Invoices": [{"InvoiceID": bill["xero_invoice_id"], "Status": "VOIDED"}]})
            await db.bills().update_one({"_id": bill["_id"]}, {"$set": {"xero_void_pending": False}})
        except (XeroAuthError, XeroApiError):
            pass   # retried nightly; the bill shows voided after the refresh
    return await get_bill(node_id, bill["_id"])


async def approve(node_id: str, bill: dict, user: dict) -> dict:
    """Approve is the post."""
    if bill["status"] not in ("submitted", "post_failed"):
        raise HTTPException(400, f"A {bill['status']} bill cannot be approved")
    reasons = logic.approve_blocks({**bill, "xero_contact_id": bill.get("xero_contact_id") or bill.get("supplier_id")}, False)
    reasons = [r for r in reasons if "Xero contact" not in r]   # created on post when missing
    if reasons:
        raise HTTPException(400, " ".join(reasons))
    await db.bills().update_one({"_id": bill["_id"]}, {"$set": {"status": "approved", "approved_by": user["email"], "approved_at": _now()},
                                                       "$push": {"history": _history(user, "approve")}})
    await audit.log(user, node_id, "approve", "bills", bill["_id"])
    return await post_bill(bill["_id"])


def bill_payload(bill: dict, settings: dict, contact_id: str) -> dict:
    lines = []
    for ln in bill.get("lines") or []:
        item = {"Description": ln.get("description") or bill.get("description") or "", "Quantity": float(ln.get("quantity") or 1),
                "UnitAmount": float(ln.get("unit_amount") or 0), "AccountCode": ln.get("account_code") or ""}
        if ln.get("tax_type"):
            item["TaxType"] = ln["tax_type"]
        if settings.get("tracking_category_id") and settings.get("tracking_option_id"):
            item["Tracking"] = [{"TrackingCategoryID": settings["tracking_category_id"], "TrackingOptionID": settings["tracking_option_id"]}]
        lines.append(item)
    return {"Invoices": [{"Type": "ACCPAY", "Contact": {"ContactID": contact_id}, "InvoiceNumber": bill.get("invoice_number") or "",
                          "Date": str(bill.get("invoice_date"))[:10], "DueDate": str(bill.get("due_date") or bill.get("invoice_date"))[:10],
                          "CurrencyCode": bill.get("currency") or "ZAR",
                          "LineAmountTypes": "Inclusive" if (bill.get("amounts_are") or "inclusive") == "inclusive" else "Exclusive",
                          "Reference": bill.get("bill_number"), "Status": settings.get("post_status") or "DRAFT", "LineItems": lines}]}


async def post_bill(bill_id: str) -> dict:
    """Never raises on a Xero validation error or a broken connection: those become bill state."""
    bill = await db.bills().find_one({"_id": bill_id})
    node_id = bill["node_id"]
    c = await xero_mt.connection(node_id)
    if not c:
        # Nodes without Xero run fully in-app: an approved bill is final.
        await db.bills().update_one({"_id": bill_id}, {"$set": {"status": "approved", "post_error": None}})
        return await db.bills().find_one({"_id": bill_id})
    settings = c.get("settings") or xero_mt.default_settings()
    try:
        if not bill.get("xero_invoice_id"):
            sup = await db.suppliers().find_one({"_id": bill.get("supplier_id")})
            cid = await xero_mt.ensure_contact(node_id, sup, db.suppliers())
            num = (bill.get("invoice_number") or "").replace('"', '\\"')
            found = (await xero_mt.request(node_id, "GET", "Invoices", params={
                "where": f'Type=="ACCPAY" AND Contact.ContactID==Guid("{cid}") AND InvoiceNumber=="{num}"'}) or {}).get("Invoices") or []
            live = [i for i in found if i.get("Status") != "DELETED"]
            if live:
                await db.bills().update_one({"_id": bill_id}, {"$set": {
                    "status": "post_failed", "post_blocked": "duplicate_in_xero",
                    "post_error": f"Xero already has bill {live[0].get('InvoiceNumber')} for this supplier."}})
                return await db.bills().find_one({"_id": bill_id})
            payload = bill_payload(bill, settings, cid)
            body = await xero_mt.request(node_id, "PUT", "Invoices", json_body=payload,
                                         headers={"Idempotency-Key": xero_mt._idem("bill", bill_id, payload)})
            x = body["Invoices"][0]
            await db.bills().update_one({"_id": bill_id}, {"$set": {
                "xero_invoice_id": x["InvoiceID"], "xero_status": x.get("Status"),
                "xero_url": f"https://go.xero.com/AccountsPayable/Edit.aspx?InvoiceID={x['InvoiceID']}"}})
            bill = await db.bills().find_one({"_id": bill_id})
        attached = set(bill.get("xero_attached_documents") or [])
        for did in bill.get("documents") or []:
            if did in attached:
                continue
            content, fname, mime = await load_document(did)
            try:
                await xero_mt.request(node_id, "PUT", f"Invoices/{bill['xero_invoice_id']}/Attachments/{fname.replace('/', '_')}",
                                      content=content, headers={"Content-Type": mime})
            except (XeroApiError, XeroAuthError) as e:
                await db.bills().update_one({"_id": bill_id}, {"$set": {"status": "posted_attachment_pending", "post_error": str(e)}})
                return await db.bills().find_one({"_id": bill_id})
            await db.bills().update_one({"_id": bill_id}, {"$push": {"xero_attached_documents": did}})
        await db.bills().update_one({"_id": bill_id}, {"$set": {"status": "posted", "posted_at": _now(), "post_error": None}})
    except XeroAuthError as e:
        await db.bills().update_one({"_id": bill_id}, {"$set": {"status": "approved", "post_error": f"Waiting for Xero: {e}"}})
    except XeroApiError as e:
        await db.bills().update_one({"_id": bill_id}, {"$set": {"status": "post_failed", "post_error": e.message}})
    return await db.bills().find_one({"_id": bill_id})


async def refresh_statuses(node_id: str) -> dict:
    n = 0
    async for b in db.bills().find({"node_id": node_id, "xero_invoice_id": {"$ne": None},
                                    "xero_status": {"$nin": ["PAID", "VOIDED", "DELETED"]}}):
        body = await xero_mt.request(node_id, "GET", f"Invoices/{b['xero_invoice_id']}")
        x = ((body or {}).get("Invoices") or [{}])[0]
        upd = {"xero_status": x.get("Status"), "amount_due": x.get("AmountDue")}
        if x.get("Status") == "VOIDED":
            upd["status"] = "void"
        await db.bills().update_one({"_id": b["_id"]}, {"$set": upd})
        n += 1
    return {"refreshed": n}
