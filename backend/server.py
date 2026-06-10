"""Vezubunye platform API.

One live node (GoGreen Roto Moulding, Queenstown) running the full
powder-to-cash chain. node_id on every record; node two is a config entry.

Route handlers are thin: validate, write, hand off to recon for rule checks.
Every write is audit-logged. No flag auto-clears.
"""
from __future__ import annotations

import base64
import hashlib
import os
import time
from datetime import datetime, date as date_cls
from typing import Optional
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv()

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

import audit
import auth
import db
import pdf_gen
import recon
from models import (
    CaptureEntriesIn, DeliveryNoteIn, FlagResolveIn, InvoiceIn, LedgerAdjustIn,
    LoginIn, NodeConfigIn, NodeIn, PaymentIn, PaymentMatchIn, PhysicalCountIn,
    UserIn,
)

app = FastAPI(title="Vezubunye API", version="0.1.0")

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:5174",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await db.ensure_indexes()


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def _now():
    return datetime.utcnow()


def _today() -> str:
    return date_cls.today().isoformat()


async def _all(coll, filt=None, sort=None):
    cursor = coll.find(filt or {})
    if sort:
        cursor = cursor.sort(sort)
    return [d async for d in cursor]


async def _get_node(node_id: str) -> dict:
    node = await db.nodes().find_one({"node_id": node_id})
    if not node:
        raise HTTPException(404, f"Node {node_id} not found")
    return node


async def _get_cfg(node_id: str) -> dict:
    cfg = await db.node_config().find_one({"node_id": node_id})
    if not cfg:
        raise HTTPException(404, f"No config for node {node_id}")
    return cfg


def _scrub_cfg(cfg: dict, user: dict) -> dict:
    """Fenix cost and margin never render for any role except admin."""
    out = dict(cfg)
    if user["role"] != "admin":
        out.pop("material_cost_per_kg", None)
    return out


# ============================== auth ============================== #

@app.post("/api/auth/login")
async def login(payload: LoginIn):
    user = await db.users().find_one({"email": payload.email.lower().strip()})
    if not user or not auth.verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid email or password")
    token = auth.make_token(user)
    return {
        "token": token,
        "user": {"email": user["email"], "name": user.get("name", ""),
                 "role": user["role"], "node_access": user.get("node_access", "all")},
    }


@app.get("/api/auth/me")
async def me(user: dict = Depends(auth.current_user)):
    return user


# ============================== nodes & config ============================== #

@app.get("/api/nodes")
async def list_nodes(user: dict = Depends(auth.current_user)):
    nodes = await _all(db.nodes())
    if user["node_access"] != "all" and user["role"] != "admin":
        nodes = [n for n in nodes if n["node_id"] in user["node_access"]]
    return nodes


@app.post("/api/nodes")
async def create_node(payload: NodeIn, user: dict = Depends(auth.require_role("admin"))):
    if await db.nodes().find_one({"node_id": payload.node_id}):
        raise HTTPException(400, "node_id already exists")
    doc = {"_id": uuid4().hex, **payload.model_dump(), "created_at": _now()}
    await db.nodes().insert_one(doc)
    await audit.log(user, payload.node_id, "create", "nodes", doc["_id"], after=doc)
    return doc


@app.put("/api/nodes/{node_id}")
async def update_node(node_id: str, payload: NodeIn,
                      user: dict = Depends(auth.require_role("admin"))):
    before = await _get_node(node_id)
    update = payload.model_dump()
    await db.nodes().update_one({"node_id": node_id}, {"$set": update})
    await audit.log(user, node_id, "update", "nodes", before["_id"], before=before, after=update)
    return {**before, **update}


@app.get("/api/nodes/{node_id}/config")
async def get_config(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    return _scrub_cfg(cfg, user)


@app.put("/api/nodes/{node_id}/config")
async def put_config(node_id: str, payload: NodeConfigIn,
                     user: dict = Depends(auth.require_role("admin"))):
    await _get_node(node_id)
    before = await db.node_config().find_one({"node_id": node_id})
    doc = {"node_id": node_id, **payload.model_dump(), "updated_at": _now()}
    await db.node_config().update_one({"node_id": node_id}, {"$set": doc}, upsert=True)
    await audit.log(user, node_id, "update", "node_config",
                    before["_id"] if before else None, before=before, after=doc)
    return doc


# ============================== users (admin) ============================== #

@app.get("/api/users")
async def list_users(user: dict = Depends(auth.require_role("admin"))):
    users = await _all(db.users())
    return [{k: v for k, v in u.items() if k != "password_hash"} for u in users]


@app.post("/api/users")
async def create_user(payload: UserIn, user: dict = Depends(auth.require_role("admin"))):
    email = payload.email.lower().strip()
    if await db.users().find_one({"email": email}):
        raise HTTPException(400, "Email already exists")
    if not payload.password:
        raise HTTPException(400, "Password required")
    doc = {
        "_id": uuid4().hex, "email": email, "name": payload.name,
        "password_hash": auth.hash_password(payload.password),
        "role": payload.role, "node_access": payload.node_access,
        "created_at": _now(),
    }
    await db.users().insert_one(doc)
    await audit.log(user, "-", "create", "users", doc["_id"], after=doc)
    return {k: v for k, v in doc.items() if k != "password_hash"}


@app.put("/api/users/{email}")
async def update_user(email: str, payload: UserIn,
                      user: dict = Depends(auth.require_role("admin"))):
    before = await db.users().find_one({"email": email.lower()})
    if not before:
        raise HTTPException(404, "User not found")
    update = {"name": payload.name, "role": payload.role, "node_access": payload.node_access}
    if payload.password:
        update["password_hash"] = auth.hash_password(payload.password)
    await db.users().update_one({"email": email.lower()}, {"$set": update})
    await audit.log(user, "-", "update", "users", before["_id"], before=before, after=update)
    return {"ok": True}


@app.delete("/api/users/{email}")
async def delete_user(email: str, user: dict = Depends(auth.require_role("admin"))):
    if email.lower() == user["email"]:
        raise HTTPException(400, "Cannot delete yourself")
    before = await db.users().find_one({"email": email.lower()})
    res = await db.users().delete_one({"email": email.lower()})
    if before:
        await audit.log(user, "-", "delete", "users", before["_id"], before=before)
    return {"deleted": res.deleted_count}


# ============================== daily capture ============================== #

@app.get("/api/nodes/{node_id}/capture-sheet.pdf")
async def capture_sheet(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    node = await _get_node(node_id)
    cfg = await _get_cfg(node_id)
    pdf = pdf_gen.daily_capture_sheet(node, cfg)
    return Response(pdf, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="{node_id}_daily_capture_sheet.pdf"'})


@app.post("/api/nodes/{node_id}/captures")
async def create_capture(node_id: str, date: str,
                         user: dict = Depends(auth.require_role("capturer", "admin"))):
    auth.check_node_access(user, node_id)
    await _get_node(node_id)
    existing = await db.daily_captures().find_one({"node_id": node_id, "date": date})
    if existing:
        return existing
    doc = {
        "_id": uuid4().hex, "node_id": node_id, "date": date,
        "photo_url": None, "captured_by": user["email"],
        "status": "pending", "created_at": _now(),
    }
    await db.daily_captures().insert_one(doc)
    await audit.log(user, node_id, "create", "daily_captures", doc["_id"], after=doc)
    return doc


async def _upload_cloudinary(content: bytes, public_id: str) -> Optional[str]:
    """Signed upload via REST if CLOUDINARY_URL is set; returns secure_url or None."""
    url = os.getenv("CLOUDINARY_URL", "")
    if not url.startswith("cloudinary://"):
        return None
    creds, cloud = url[len("cloudinary://"):].split("@")
    api_key, api_secret = creds.split(":")
    ts = str(int(time.time()))
    to_sign = f"public_id={public_id}&timestamp={ts}{api_secret}"
    signature = hashlib.sha1(to_sign.encode()).hexdigest()
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"https://api.cloudinary.com/v1_1/{cloud}/image/upload",
            data={"api_key": api_key, "timestamp": ts,
                  "public_id": public_id, "signature": signature},
            files={"file": ("photo.jpg", content)},
            timeout=60,
        )
    if r.status_code == 200:
        return r.json().get("secure_url")
    return None


@app.post("/api/captures/{capture_id}/photo")
async def upload_capture_photo(capture_id: str, file: UploadFile = File(...),
                               user: dict = Depends(auth.require_role("capturer", "admin"))):
    cap = await db.daily_captures().find_one({"_id": capture_id})
    if not cap:
        raise HTTPException(404, "Capture not found")
    auth.check_node_access(user, cap["node_id"])
    content = await file.read()
    if len(content) > 12 * 1024 * 1024:
        raise HTTPException(400, "Photo too large (max 12 MB)")
    photo_url = await _upload_cloudinary(content, f"vezubunye/{cap['node_id']}/{cap['date']}")
    update = {"photo_url": photo_url or f"/api/captures/{capture_id}/photo",
              "photo_content_type": file.content_type or "image/jpeg"}
    if not photo_url:
        update["photo_b64"] = base64.b64encode(content).decode()
    await db.daily_captures().update_one({"_id": capture_id}, {"$set": update})
    await audit.log(user, cap["node_id"], "update", "daily_captures", capture_id,
                    after={"photo_uploaded": True})
    return {"photo_url": update["photo_url"]}


@app.get("/api/captures/{capture_id}/photo")
async def get_capture_photo(capture_id: str, user: dict = Depends(auth.current_user)):
    cap = await db.daily_captures().find_one({"_id": capture_id})
    if not cap or not cap.get("photo_b64"):
        raise HTTPException(404, "Photo not found")
    auth.check_node_access(user, cap["node_id"])
    return Response(base64.b64decode(cap["photo_b64"]),
                    media_type=cap.get("photo_content_type", "image/jpeg"))


@app.post("/api/captures/{capture_id}/entries")
async def capture_entries(capture_id: str, payload: CaptureEntriesIn,
                          user: dict = Depends(auth.require_role("capturer", "admin"))):
    """Key the day's sheet in one transaction: powder ledger, production runs,
    finished goods, scrap log — all referencing the capture. Recon rules 1 and 2
    evaluate immediately on save."""
    cap = await db.daily_captures().find_one({"_id": capture_id})
    if not cap:
        raise HTTPException(404, "Capture not found")
    node_id, date = cap["node_id"], cap["date"]
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    weights = {t["code"]: t["weight_kg"] for t in cfg["tank_types"]}
    for line in payload.production:
        if line.tank_type not in weights:
            raise HTTPException(400, f"Unknown tank type {line.tank_type}")

    # re-capture: remove prior derived entries for this capture (audit-logged)
    prior = await _all(db.powder_ledger(), {"source_capture_id": capture_id})
    if prior:
        await audit.log(user, node_id, "update", "daily_captures", capture_id,
                        before={"note": "re-capture, prior entries replaced"})
    for coll in (db.powder_ledger(), db.production_runs(), db.scrap_log()):
        await coll.delete_many({"source_capture_id": capture_id})
    await db.finished_goods().delete_many({"reference": capture_id, "type": "produced"})

    docs_written = []
    if payload.powder_in_kg:
        d = {"_id": uuid4().hex, "node_id": node_id, "date": date, "type": "in",
             "kg": payload.powder_in_kg, "source_capture_id": capture_id,
             "notes": payload.notes or "", "created_at": _now()}
        await db.powder_ledger().insert_one(d)
        docs_written.append(("powder_ledger", d))
    if payload.powder_drawn_kg:
        d = {"_id": uuid4().hex, "node_id": node_id, "date": date, "type": "drawn",
             "kg": payload.powder_drawn_kg, "source_capture_id": capture_id,
             "notes": payload.notes or "", "created_at": _now()}
        await db.powder_ledger().insert_one(d)
        docs_written.append(("powder_ledger", d))

    for line in payload.production:
        if line.quantity_a == line.quantity_b == line.quantity_reject == 0:
            continue
        run = {"_id": uuid4().hex, "node_id": node_id, "date": date,
               "tank_type": line.tank_type, "quantity_a": line.quantity_a,
               "quantity_b": line.quantity_b, "quantity_reject": line.quantity_reject,
               "source_capture_id": capture_id, "created_at": _now()}
        await db.production_runs().insert_one(run)
        docs_written.append(("production_runs", run))
        # A and B enter finished goods; rejects never do — they exit via scrap_log
        for grade, qty in (("A", line.quantity_a), ("B", line.quantity_b)):
            if qty > 0:
                fg = {"_id": uuid4().hex, "node_id": node_id, "date": date,
                      "tank_type": line.tank_type, "grade": grade, "type": "produced",
                      "quantity": qty, "reference": capture_id, "created_at": _now()}
                await db.finished_goods().insert_one(fg)
        if line.quantity_reject > 0:
            kg_lost = line.quantity_reject * weights[line.tank_type]
            scrap = {"_id": uuid4().hex, "node_id": node_id, "date": date,
                     "tank_type": line.tank_type, "quantity": line.quantity_reject,
                     "kg_lost": kg_lost,
                     "material_cost_lost": round(kg_lost * cfg["material_cost_per_kg"], 2),
                     "source_capture_id": capture_id, "notes": payload.notes or "",
                     "created_at": _now()}
            await db.scrap_log().insert_one(scrap)

    # reconciliation rules 1 and 2 run on save
    flags_raised = []
    flags_raised += await recon.check_powder_vs_production(node_id, date, capture_id)
    flags_raised += await recon.check_finished_goods(node_id, date, {"capture_id": capture_id})

    status = "reconciled" if not flags_raised else "captured"
    await db.daily_captures().update_one(
        {"_id": capture_id},
        {"$set": {"status": status, "entries": payload.model_dump(),
                  "captured_by": user["email"], "captured_at": _now()}})
    await audit.log(user, node_id, "update", "daily_captures", capture_id,
                    after={"entries": payload.model_dump(), "status": status,
                           "flags_raised": flags_raised})
    return {"capture_id": capture_id, "status": status, "flags_raised": flags_raised}


@app.get("/api/nodes/{node_id}/captures")
async def list_captures(node_id: str, month: Optional[str] = None,
                        user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    filt: dict = {"node_id": node_id}
    if month:
        filt["date"] = {"$regex": f"^{month}"}
    docs = await _all(db.daily_captures(), filt, sort=[("date", -1)])
    return [{k: v for k, v in d.items() if k != "photo_b64"} for d in docs]


# ============================== ledgers ============================== #

@app.get("/api/nodes/{node_id}/powder")
async def powder_ledger(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    entries = await _all(db.powder_ledger(), {"node_id": node_id}, sort=[("date", 1), ("created_at", 1)])
    bal = 0.0
    for e in entries:
        bal += e["kg"] if e["type"] in ("in", "count_adjustment") else -e["kg"]
        e["running_balance"] = round(bal, 2)
    return {"entries": entries, "balance": round(bal, 2)}


@app.post("/api/nodes/{node_id}/powder/adjustment")
async def powder_adjustment(node_id: str, payload: LedgerAdjustIn,
                            user: dict = Depends(auth.require_role("controller", "admin"))):
    auth.check_node_access(user, node_id)
    if payload.kg is None:
        raise HTTPException(400, "kg required")
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "type": "count_adjustment", "kg": payload.kg, "source_capture_id": None,
         "notes": payload.notes, "created_at": _now()}
    await db.powder_ledger().insert_one(d)
    await audit.log(user, node_id, "create", "powder_ledger", d["_id"], after=d)
    return d


@app.get("/api/nodes/{node_id}/production")
async def production(node_id: str, month: Optional[str] = None,
                     user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    filt: dict = {"node_id": node_id}
    if month:
        filt["date"] = {"$regex": f"^{month}"}
    runs = await _all(db.production_runs(), filt, sort=[("date", -1)])
    cfg = await _get_cfg(node_id)
    weights = {t["code"]: t["weight_kg"] for t in cfg["tank_types"]}
    for r in runs:
        r["implied_powder_kg"] = round(
            (r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]) * weights.get(r["tank_type"], 0), 1)
    return runs


@app.get("/api/nodes/{node_id}/finished-goods")
async def finished_goods(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    entries = await _all(db.finished_goods(), {"node_id": node_id},
                         sort=[("date", 1), ("created_at", 1)])
    on_hand = await recon.fg_on_hand(node_id)
    return {
        "entries": entries,
        "on_hand": [{"tank_type": k[0], "grade": k[1], "quantity": v}
                    for k, v in sorted(on_hand.items())],
    }


@app.post("/api/nodes/{node_id}/finished-goods/adjustment")
async def fg_adjustment(node_id: str, payload: LedgerAdjustIn,
                        user: dict = Depends(auth.require_role("controller", "admin"))):
    auth.check_node_access(user, node_id)
    if not payload.tank_type or payload.quantity is None or not payload.grade:
        raise HTTPException(400, "tank_type, grade, quantity required")
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "tank_type": payload.tank_type, "grade": payload.grade,
         "type": "count_adjustment", "quantity": payload.quantity,
         "reference": None, "notes": payload.notes, "created_at": _now()}
    await db.finished_goods().insert_one(d)
    await audit.log(user, node_id, "create", "finished_goods_ledger", d["_id"], after=d)
    return d


@app.get("/api/nodes/{node_id}/scrap")
async def scrap(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.scrap_log(), {"node_id": node_id}, sort=[("date", -1)])
    if user["role"] != "admin":
        for d in docs:
            d.pop("material_cost_lost", None)
    return docs


# ============================== delivery notes ============================== #

@app.post("/api/nodes/{node_id}/delivery-notes")
async def create_delivery_note(node_id: str, payload: DeliveryNoteIn,
                               user: dict = Depends(auth.require_role("capturer", "admin"))):
    auth.check_node_access(user, node_id)
    node = await _get_node(node_id)
    cfg = await _get_cfg(node_id)
    seq = await db.next_number(node_id, "dn")
    dn_number = f"{node.get('prefix', node_id.upper())}-DN-{seq:04d}"
    dn_id = uuid4().hex
    dn = {
        "_id": dn_id, "node_id": node_id, "dn_number": dn_number,
        "date": payload.date, "client_name": payload.client_name,
        "client_details": payload.client_details,
        "lines": [l.model_dump() for l in payload.lines],
        "linked_invoice_id": None, "created_at": _now(),
    }
    # delivered entries hit the finished goods ledger, per type per grade
    for l in payload.lines:
        await db.finished_goods().insert_one({
            "_id": uuid4().hex, "node_id": node_id, "date": payload.date,
            "tank_type": l.tank_type, "grade": l.grade, "type": "delivered",
            "quantity": l.quantity, "reference": dn_id, "created_at": _now()})
    pdf = pdf_gen.delivery_note_pdf(node, cfg, dn)
    dn["content_b64"] = base64.b64encode(pdf).decode()
    dn["pdf_url"] = f"/api/delivery-notes/{dn_id}/pdf"
    await db.delivery_notes().insert_one(dn)
    await audit.log(user, node_id, "create", "delivery_notes", dn_id,
                    after={k: v for k, v in dn.items() if k != "content_b64"})
    await recon.check_finished_goods(node_id, payload.date, {"delivery_note_id": dn_id})
    return {k: v for k, v in dn.items() if k != "content_b64"}


@app.get("/api/nodes/{node_id}/delivery-notes")
async def list_delivery_notes(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.delivery_notes(), {"node_id": node_id}, sort=[("dn_number", -1)])
    return [{k: v for k, v in d.items() if k != "content_b64"} for d in docs]


@app.get("/api/delivery-notes/{dn_id}/pdf")
async def delivery_note_pdf_endpoint(dn_id: str, user: dict = Depends(auth.current_user)):
    dn = await db.delivery_notes().find_one({"_id": dn_id})
    if not dn:
        raise HTTPException(404, "Delivery note not found")
    auth.check_node_access(user, dn["node_id"])
    return Response(base64.b64decode(dn["content_b64"]), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{dn["dn_number"]}.pdf"'})


# ============================== invoices ============================== #

@app.post("/api/nodes/{node_id}/invoices")
async def create_invoice(node_id: str, payload: InvoiceIn,
                         user: dict = Depends(auth.require_role("capturer", "admin"))):
    auth.check_node_access(user, node_id)
    node = await _get_node(node_id)
    cfg = await _get_cfg(node_id)
    if not payload.lines:
        raise HTTPException(400, "Invoice needs at least one line")
    dns = []
    for dn_id in payload.delivery_note_ids:
        dn = await db.delivery_notes().find_one({"_id": dn_id})
        if not dn or dn["node_id"] != node_id:
            raise HTTPException(400, f"Delivery note {dn_id} not found on this node")
        if dn.get("linked_invoice_id"):
            raise HTTPException(400, f"{dn['dn_number']} is already invoiced")
        dns.append(dn)

    seq = await db.next_number(node_id, "inv")
    invoice_number = f"{node.get('prefix', node_id.upper())}-INV-{seq:04d}"
    subtotal = round(sum(l.quantity * l.unit_price for l in payload.lines), 2)
    vat_rate = cfg.get("vat_rate", 15.0)
    vat = round(subtotal * vat_rate / 100.0, 2)
    inv_id = uuid4().hex
    inv = {
        "_id": inv_id, "node_id": node_id, "invoice_number": invoice_number,
        "date": payload.date, "client_name": payload.client_name,
        "client_details": payload.client_details,
        "lines": [l.model_dump() for l in payload.lines],
        "subtotal": subtotal, "vat_rate": vat_rate, "vat": vat,
        "total": round(subtotal + vat, 2),
        "linked_delivery_note_ids": payload.delivery_note_ids,
        "linked_delivery_note_numbers": [d["dn_number"] for d in dns],
        "status": "unpaid", "created_at": _now(),
    }
    pdf = pdf_gen.invoice_pdf(node, cfg, inv)
    inv["content_b64"] = base64.b64encode(pdf).decode()
    inv["pdf_url"] = f"/api/invoices/{inv_id}/pdf"
    await db.invoices().insert_one(inv)
    for dn in dns:
        await db.delivery_notes().update_one({"_id": dn["_id"]},
                                             {"$set": {"linked_invoice_id": inv_id}})
        # resolve is manual everywhere else; linking the invoice is the fix itself,
        # so close any open delivery_without_invoice flag with a system note
        await db.flags().update_many(
            {"node_id": node_id, "type": "delivery_without_invoice",
             "references.delivery_note_id": dn["_id"], "status": "open"},
            {"$set": {"status": "resolved", "resolved_by": user["email"],
                      "resolution_note": f"Invoice {invoice_number} raised and linked."}})
    await audit.log(user, node_id, "create", "invoices", inv_id,
                    after={k: v for k, v in inv.items() if k != "content_b64"})
    return {k: v for k, v in inv.items() if k != "content_b64"}


@app.get("/api/nodes/{node_id}/invoices")
async def list_invoices(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.invoices(), {"node_id": node_id}, sort=[("invoice_number", -1)])
    return [{k: v for k, v in d.items() if k != "content_b64"} for d in docs]


@app.get("/api/invoices/{inv_id}/pdf")
async def invoice_pdf_endpoint(inv_id: str, user: dict = Depends(auth.current_user)):
    inv = await db.invoices().find_one({"_id": inv_id})
    if not inv:
        raise HTTPException(404, "Invoice not found")
    auth.check_node_access(user, inv["node_id"])
    return Response(base64.b64decode(inv["content_b64"]), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{inv["invoice_number"]}.pdf"'})


# ============================== payments ============================== #

@app.post("/api/nodes/{node_id}/payments")
async def create_payment(node_id: str, payload: PaymentIn,
                         user: dict = Depends(auth.require_role("controller", "admin"))):
    auth.check_node_access(user, node_id)
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "amount": payload.amount, "bank_reference": payload.bank_reference,
         "matched_invoice_id": None, "split": None, "status": "unmatched",
         "created_at": _now()}
    await db.payments().insert_one(d)
    await audit.log(user, node_id, "create", "payments", d["_id"], after=d)
    return d


@app.post("/api/payments/{payment_id}/match")
async def match_payment(payment_id: str, payload: PaymentMatchIn,
                        user: dict = Depends(auth.require_role("controller", "admin"))):
    """Match a payment to an invoice and compute the Fenix/partner split.
    Fenix draws ex-works value: full for A-grade lines, b_grade_exworks_pct for B."""
    p = await db.payments().find_one({"_id": payment_id})
    if not p:
        raise HTTPException(404, "Payment not found")
    auth.check_node_access(user, p["node_id"])
    inv = await db.invoices().find_one({"_id": payload.invoice_id})
    if not inv or inv["node_id"] != p["node_id"]:
        raise HTTPException(400, "Invoice not found on this node")
    cfg = await _get_cfg(p["node_id"])

    split = recon.compute_split(inv, cfg, p["amount"])
    paid_so_far = 0.0
    async for other in db.payments().find({"matched_invoice_id": inv["_id"]}):
        paid_so_far += other["amount"]
    total_paid = paid_so_far + p["amount"]

    status = "matched"
    flags_raised = []
    if total_paid + 0.005 < inv["total"]:
        inv_status = "part_paid"
        status = "flagged"
        flags_raised.append(await recon.raise_flag(
            p["node_id"], "short_paid",
            f"Invoice {inv['invoice_number']}: paid R{total_paid:.2f} of R{inv['total']:.2f} "
            f"(short R{inv['total'] - total_paid:.2f}).",
            {"invoice_id": inv["_id"], "payment_id": payment_id}, p["date"]))
    elif total_paid > inv["total"] + 0.005:
        inv_status = "flagged"
        status = "flagged"
        flags_raised.append(await recon.raise_flag(
            p["node_id"], "over_paid",
            f"Invoice {inv['invoice_number']}: paid R{total_paid:.2f} against R{inv['total']:.2f} "
            f"(over by R{total_paid - inv['total']:.2f}).",
            {"invoice_id": inv["_id"], "payment_id": payment_id}, p["date"]))
    else:
        inv_status = "paid"

    before = dict(p)
    update = {"matched_invoice_id": inv["_id"], "split": split, "status": status}
    await db.payments().update_one({"_id": payment_id}, {"$set": update})
    await db.invoices().update_one({"_id": inv["_id"]}, {"$set": {"status": inv_status}})
    # matching is the fix for an unmatched-payment flag
    await db.flags().update_many(
        {"node_id": p["node_id"], "type": "payment_unmatched",
         "references.payment_id": payment_id, "status": "open"},
        {"$set": {"status": "resolved", "resolved_by": user["email"],
                  "resolution_note": f"Matched to {inv['invoice_number']}."}})
    await audit.log(user, p["node_id"], "match", "payments", payment_id,
                    before=before, after=update)
    return {**p, **update, "invoice_status": inv_status, "flags_raised": flags_raised}


@app.get("/api/nodes/{node_id}/payments")
async def list_payments(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.payments(), {"node_id": node_id}, sort=[("date", -1)])
    if user["role"] == "capturer":
        for d in docs:
            d.pop("split", None)
    return docs


# ============================== flags ============================== #

@app.get("/api/nodes/{node_id}/flags")
async def list_flags(node_id: str, status: Optional[str] = None,
                     user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    filt: dict = {"node_id": node_id}
    if status:
        filt["status"] = status
    return await _all(db.flags(), filt, sort=[("date_raised", -1)])


@app.post("/api/flags/{flag_id}/resolve")
async def resolve_flag(flag_id: str, payload: FlagResolveIn,
                       user: dict = Depends(auth.require_role("controller", "admin"))):
    """No flag auto-clears. Resolution requires a note."""
    if not payload.resolution_note.strip():
        raise HTTPException(400, "Resolution note required")
    f = await db.flags().find_one({"_id": flag_id})
    if not f:
        raise HTTPException(404, "Flag not found")
    auth.check_node_access(user, f["node_id"])
    if f["status"] == "resolved":
        raise HTTPException(400, "Flag already resolved")
    update = {"status": "resolved", "resolved_by": user["email"],
              "resolution_note": payload.resolution_note,
              "resolved_at": _now()}
    await db.flags().update_one({"_id": flag_id}, {"$set": update})
    await audit.log(user, f["node_id"], "resolve", "flags", flag_id, before=f, after=update)
    return {**f, **update}


# ============================== physical counts ============================== #

@app.post("/api/nodes/{node_id}/counts")
async def create_count(node_id: str, payload: PhysicalCountIn,
                       user: dict = Depends(auth.require_role("controller", "admin"))):
    auth.check_node_access(user, node_id)
    system_powder = await recon.powder_balance(node_id, payload.date)
    system_fg = await recon.fg_on_hand(node_id, payload.date)
    powder_variance = round(payload.powder_kg_counted - system_powder, 2)

    fg_variances = []
    counted = {(l.tank_type, l.grade): l.quantity for l in payload.finished_goods_counted}
    for key in set(list(system_fg.keys()) + list(counted.keys())):
        sys_q = system_fg.get(key, 0)
        cnt_q = counted.get(key, 0)
        if sys_q != cnt_q:
            fg_variances.append({"tank_type": key[0], "grade": key[1],
                                 "system": sys_q, "counted": cnt_q,
                                 "variance": cnt_q - sys_q})

    doc = {
        "_id": uuid4().hex, "node_id": node_id, "date": payload.date,
        "powder_kg_counted": payload.powder_kg_counted,
        "finished_goods_counted": [l.model_dump() for l in payload.finished_goods_counted],
        "system_values_at_count": {
            "powder_kg": system_powder,
            "finished_goods": [{"tank_type": k[0], "grade": k[1], "quantity": v}
                               for k, v in sorted(system_fg.items())],
        },
        "variances": {"powder_kg": powder_variance, "finished_goods": fg_variances},
        "counted_by": user["email"], "created_at": _now(),
    }
    await db.physical_counts().insert_one(doc)
    await audit.log(user, node_id, "create", "physical_counts", doc["_id"], after=doc)

    flags_raised = []
    if abs(powder_variance) > recon.EPS:
        flags_raised.append(await recon.raise_flag(
            node_id, "count_mismatch",
            f"{payload.date}: physical powder count {payload.powder_kg_counted:.1f} kg vs "
            f"system {system_powder:.1f} kg (variance {powder_variance:+.1f} kg).",
            {"count_id": doc["_id"], "what": "powder"}, payload.date))
    for v in fg_variances:
        flags_raised.append(await recon.raise_flag(
            node_id, "count_mismatch",
            f"{payload.date}: physical count {v['tank_type']} grade {v['grade']} = {v['counted']} "
            f"vs system {v['system']} (variance {v['variance']:+d}).",
            {"count_id": doc["_id"], "tank_type": v["tank_type"], "grade": v["grade"]},
            payload.date))
    return {**doc, "flags_raised": flags_raised}


@app.get("/api/nodes/{node_id}/counts")
async def list_counts(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    return await _all(db.physical_counts(), {"node_id": node_id}, sort=[("date", -1)])


# ============================== reconciliation dashboard ============================== #

@app.post("/api/nodes/{node_id}/recon/sweep")
async def recon_sweep(node_id: str, user: dict = Depends(auth.require_role("controller", "admin"))):
    auth.check_node_access(user, node_id)
    return await recon.run_sweeps(node_id)


@app.get("/api/nodes/{node_id}/recon")
async def recon_dashboard(node_id: str, month: Optional[str] = None,
                          user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    month = month or _today()[:7]
    captures = await _all(db.daily_captures(),
                          {"node_id": node_id, "date": {"$regex": f"^{month}"}})
    cap_by_date = {c["date"]: c for c in captures}
    open_flags = await _all(db.flags(), {"node_id": node_id, "status": "open"},
                            sort=[("date_raised", -1)])
    flag_dates = {f["date_raised"] for f in open_flags}

    days = []
    year, mon = int(month[:4]), int(month[5:7])
    import calendar as _cal
    for day in range(1, _cal.monthrange(year, mon)[1] + 1):
        d = f"{month}-{day:02d}"
        if d > _today():
            break
        cap = cap_by_date.get(d)
        if d in flag_dates:
            day_status = "flagged"
        elif cap and cap["status"] == "reconciled":
            day_status = "clear"
        elif cap:
            day_status = "captured"
        else:
            day_status = "no_capture"
        days.append({"date": d, "status": day_status,
                     "capture_id": cap["_id"] if cap else None})

    unmatched = await _all(db.payments(), {"node_id": node_id, "status": "unmatched"})
    unpaid = await _all(db.invoices(), {"node_id": node_id,
                                        "status": {"$in": ["unpaid", "part_paid", "flagged"]}})
    return {
        "month": month,
        "days": days,
        "open_flags": open_flags,
        "unmatched_payments": unmatched,
        "unpaid_invoices": [{k: v for k, v in i.items() if k != "content_b64"} for i in unpaid],
    }


# ============================== reporting ============================== #

@app.get("/api/nodes/{node_id}/reports/daily")
async def daily_report(node_id: str, date: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    weights = {t["code"]: t["weight_kg"] for t in cfg["tank_types"]}
    runs = await _all(db.production_runs(), {"node_id": node_id, "date": date})
    powder = await _all(db.powder_ledger(), {"node_id": node_id, "date": date})
    dns = await _all(db.delivery_notes(), {"node_id": node_id, "date": date})
    invs = await _all(db.invoices(), {"node_id": node_id, "date": date})
    pays = await _all(db.payments(), {"node_id": node_id, "date": date})
    cap = await db.daily_captures().find_one({"node_id": node_id, "date": date})
    flags_open = await _all(db.flags(), {"node_id": node_id, "date_raised": date, "status": "open"})
    return {
        "date": date,
        "tanks_produced": [{"tank_type": r["tank_type"], "a": r["quantity_a"],
                            "b": r["quantity_b"], "reject": r["quantity_reject"]} for r in runs],
        "powder_in_kg": sum(e["kg"] for e in powder if e["type"] == "in"),
        "powder_drawn_kg": sum(e["kg"] for e in powder if e["type"] == "drawn"),
        "implied_kg": sum((r["quantity_a"] + r["quantity_b"] + r["quantity_reject"])
                          * weights.get(r["tank_type"], 0) for r in runs),
        "finished_goods_on_hand": [{"tank_type": k[0], "grade": k[1], "quantity": v}
                                   for k, v in sorted((await recon.fg_on_hand(node_id, date)).items())],
        "deliveries": [{"dn_number": d["dn_number"], "client_name": d["client_name"],
                        "lines": d["lines"]} for d in dns],
        "invoices_raised": [{"invoice_number": i["invoice_number"], "total": i["total"],
                             "status": i["status"]} for i in invs],
        "payments_received": [{"amount": p["amount"], "status": p["status"]} for p in pays],
        "reconciliation_status": (cap or {}).get("status", "no_capture"),
        "open_flags": len(flags_open),
    }


@app.get("/api/nodes/{node_id}/reports/monthly")
async def monthly_report(node_id: str, month: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    weights = {t["code"]: t["weight_kg"] for t in cfg["tank_types"]}
    prices = {t["code"]: t["ex_works_price"] for t in cfg["tank_types"]}
    b_pct = cfg.get("b_grade_exworks_pct", 100.0) / 100.0
    filt = {"node_id": node_id, "date": {"$regex": f"^{month}"}}

    runs = await _all(db.production_runs(), filt)
    kg = 0.0
    by_type: dict = {}
    for r in runs:
        total_q = r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]
        kg += total_q * weights.get(r["tank_type"], 0)
        t = by_type.setdefault(r["tank_type"], {"a": 0, "b": 0, "reject": 0})
        t["a"] += r["quantity_a"]; t["b"] += r["quantity_b"]; t["reject"] += r["quantity_reject"]

    invs = await _all(db.invoices(), filt)
    invoiced = sum(i["total"] for i in invs)
    exworks = 0.0
    for i in invs:
        for l in i["lines"]:
            factor = 1.0 if l["grade"] == "A" else b_pct
            exworks += l["quantity"] * prices.get(l["tank_type"], 0) * factor

    pays = await _all(db.payments(), filt)
    cash = sum(p["amount"] for p in pays)
    outstanding = sum(i["total"] for i in await _all(
        db.invoices(), {"node_id": node_id, "status": {"$in": ["unpaid", "part_paid"]}}))

    scrap_docs = await _all(db.scrap_log(), filt)
    out = {
        "month": month,
        "kg_through_plant": round(kg, 1),
        "tanks_by_type": [{"tank_type": k, **v} for k, v in sorted(by_type.items())],
        "ex_works_value_invoiced": round(exworks, 2),
        "invoiced_value": round(invoiced, 2),
        "cash_received": round(cash, 2),
        "outstanding": round(outstanding, 2),
        "scrap_kg": round(sum(s["kg_lost"] for s in scrap_docs), 1),
    }
    if user["role"] == "admin":
        out["scrap_material_cost"] = round(sum(s.get("material_cost_lost", 0) for s in scrap_docs), 2)
    return out


@app.get("/api/network/kg")
async def network_kg(user: dict = Depends(auth.current_user)):
    """The one network number: total kilograms through all nodes."""
    total = 0.0
    weights_by_node: dict = {}
    async for cfg in db.node_config().find({}):
        weights_by_node[cfg["node_id"]] = {t["code"]: t["weight_kg"] for t in cfg["tank_types"]}
    async for r in db.production_runs().find({}):
        w = weights_by_node.get(r["node_id"], {}).get(r["tank_type"], 0)
        total += (r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]) * w
    return {"total_kg": round(total, 1)}


# ============================== audit ============================== #

@app.get("/api/audit")
async def audit_trail(node_id: Optional[str] = None, limit: int = 200,
                      user: dict = Depends(auth.require_role("admin"))):
    filt = {"node_id": node_id} if node_id else {}
    cursor = db.audit_log().find(filt).sort("at", -1).limit(min(limit, 1000))
    return [d async for d in cursor]
