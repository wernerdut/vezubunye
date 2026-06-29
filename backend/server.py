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
import reports
from models import (
    CaptureEntriesIn, DeliveryNoteIn, FlagResolveIn, LedgerAdjustIn,
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
    # Self-heal the live config on every boot: idempotently adds any missing
    # tank types / products (e.g. the 1000L horizontal transport tank) without
    # overwriting admin edits. A bad migration must never block startup.
    try:
        import seed
        changed = await seed.migrate_config()
        if changed:
            print(f"config migration applied: {', '.join(changed)}")
    except Exception as exc:  # noqa: BLE001
        print(f"config migration skipped: {exc}")


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
        "Content-Disposition": 'inline; filename="Vezubunye Daily Capture Sheet.pdf"'})


@app.post("/api/nodes/{node_id}/captures")
async def create_capture(node_id: str, date: str,
                         user: dict = Depends(auth.require_role("operations", "admin"))):
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
                               user: dict = Depends(auth.require_role("operations", "admin"))):
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
                          user: dict = Depends(auth.require_role("operations", "admin"))):
    """Key the day's sheet: powder received/issued, fittings received/issued, paraffin
    received, tanks moulded (straight to stock). Tank dispatch is no longer captured here —
    it happens on the Deliveries tab. The app derives every balance from these movements."""
    cap = await db.daily_captures().find_one({"_id": capture_id})
    if not cap:
        raise HTTPException(404, "Capture not found")
    node_id, date = cap["node_id"], cap["date"]
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    tanks = {t["code"]: t for t in cfg["tank_types"]}
    for line in payload.production:
        if line.tank_type not in tanks:
            raise HTTPException(400, f"Unknown tank type {line.tank_type}")

    # re-capture: remove prior derived entries for this capture (audit-logged)
    prior = await _all(db.powder_ledger(), {"source_capture_id": capture_id})
    if prior:
        await audit.log(user, node_id, "update", "daily_captures", capture_id,
                        before={"note": "re-capture, prior entries replaced"})
    for coll in (db.powder_ledger(), db.fittings_ledger(), db.paraffin_ledger(),
                 db.production_runs(), db.scrap_log()):
        await coll.delete_many({"source_capture_id": capture_id})
    await db.finished_goods().delete_many({"reference": capture_id})

    # powder warehouse in/out, per type
    for line in payload.powder:
        for mtype, kg in (("received", line.received_kg), ("issued", line.issued_kg)):
            if kg:
                await db.powder_ledger().insert_one({
                    "_id": uuid4().hex, "node_id": node_id, "date": date,
                    "powder_type": line.powder_type, "type": mtype, "kg": kg,
                    "source_capture_id": capture_id, "notes": payload.notes or "",
                    "created_at": _now()})

    # paraffin received into stock (consumption is derived from tanks moulded)
    if payload.paraffin_received:
        await db.paraffin_ledger().insert_one({
            "_id": uuid4().hex, "node_id": node_id, "date": date,
            "type": "received", "litres": payload.paraffin_received,
            "source_capture_id": capture_id, "notes": payload.notes or "",
            "created_at": _now()})

    # fittings warehouse in/out, per type
    for line in payload.fittings:
        for mtype, qty in (("received", line.received_qty), ("issued", line.issued_qty)):
            if qty:
                await db.fittings_ledger().insert_one({
                    "_id": uuid4().hex, "node_id": node_id, "date": date,
                    "fitting_type": line.fitting_type, "type": mtype, "quantity": qty,
                    "source_capture_id": capture_id, "created_at": _now()})

    # tanks moulded -> production_runs (records production + consumes powder).
    # A/B-grade tanks go straight into finished-goods stock on capture (no separate
    # 'book to store' step): produced and captured == in stock. Rejects -> scrap_log.
    for line in payload.production:
        if line.quantity_a == line.quantity_b == line.quantity_reject == 0:
            continue
        await db.production_runs().insert_one({
            "_id": uuid4().hex, "node_id": node_id, "date": date,
            "tank_type": line.tank_type, "colour": line.colour,
            "quantity_a": line.quantity_a, "quantity_b": line.quantity_b,
            "quantity_reject": line.quantity_reject,
            "source_capture_id": capture_id, "created_at": _now()})
        for grade, qty in (("A", line.quantity_a), ("B", line.quantity_b)):
            if qty > 0:
                await db.finished_goods().insert_one({
                    "_id": uuid4().hex, "node_id": node_id, "date": date,
                    "tank_type": line.tank_type, "grade": grade, "type": "booked",
                    "quantity": qty, "reference": capture_id, "created_at": _now()})
        if line.quantity_reject > 0:
            t = tanks[line.tank_type]
            kg_lost = line.quantity_reject * (t["weight_kg"] + t.get("lid_weight_kg", 0.0))
            await db.scrap_log().insert_one({
                "_id": uuid4().hex, "node_id": node_id, "date": date,
                "tank_type": line.tank_type, "quantity": line.quantity_reject,
                "kg_lost": kg_lost,
                "material_cost_lost": round(kg_lost * cfg["material_cost_per_kg"], 2),
                "source_capture_id": capture_id, "notes": payload.notes or "",
                "created_at": _now()})

    # (Tank dispatch / stock-out now happens when a delivery is created, not here.)

    # No reconciliation at capture: production is just recorded and the tanks are in stock.
    # Powder/fittings/finished-goods reconciliation happens afterwards, at stocktake (Counts)
    # and on the Reconciliation dashboard — it must not hold up capturing production.
    await db.daily_captures().update_one(
        {"_id": capture_id},
        {"$set": {"status": "captured", "entries": payload.model_dump(),
                  "captured_by": user["email"], "captured_at": _now()}})
    await audit.log(user, node_id, "update", "daily_captures", capture_id,
                    after={"entries": payload.model_dump(), "status": "captured"})
    return {"capture_id": capture_id, "status": "captured", "flags_raised": []}


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
    """Per-grade warehouse + production-floor balances. Each colour is a distinct material."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    entries = await _all(db.powder_ledger(), {"node_id": node_id}, sort=[("date", 1), ("created_at", 1)])
    warehouse = await recon.powder_warehouse(node_id)
    floor = await recon.powder_floor(node_id, cfg)
    products = {p["code"]: p for p in cfg.get("powder_products", [])}
    codes = set(list(warehouse.keys()) + list(floor.keys()) + list(products.keys()))
    return {
        "entries": entries,
        "stock": [{"powder_type": k,
                   "colour": products.get(k, {}).get("colour", k),
                   "is_black": products.get(k, {}).get("is_black", False),
                   "warehouse": warehouse.get(k, 0.0), "floor": floor.get(k, 0.0)}
                  for k in sorted(codes)],
    }


@app.get("/api/nodes/{node_id}/paraffin")
async def paraffin(node_id: str, user: dict = Depends(auth.current_user)):
    """Paraffin (release agent) stock: received less consumed by moulding, plus movements."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    bal = await recon.paraffin_balance(node_id, cfg)
    entries = await _all(db.paraffin_ledger(), {"node_id": node_id},
                         sort=[("date", 1), ("created_at", 1)])
    return {**bal, "entries": entries}


@app.post("/api/nodes/{node_id}/powder/adjustment")
async def powder_adjustment(node_id: str, payload: LedgerAdjustIn,
                            user: dict = Depends(auth.require_role("audit", "admin"))):
    auth.check_node_access(user, node_id)
    if payload.kg is None or not payload.powder_type:
        raise HTTPException(400, "kg and powder_type required")
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "powder_type": payload.powder_type, "type": "count_adjustment",
         "scope": payload.scope, "kg": payload.kg, "source_capture_id": None,
         "notes": payload.notes, "created_at": _now()}
    await db.powder_ledger().insert_one(d)
    await audit.log(user, node_id, "create", "powder_ledger", d["_id"], after=d)
    return d


@app.get("/api/nodes/{node_id}/fittings")
async def fittings(node_id: str, user: dict = Depends(auth.current_user)):
    """Per-type warehouse balance + issued-vs-expected (tanks produced x fittings-per-tank)."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    entries = await _all(db.fittings_ledger(), {"node_id": node_id}, sort=[("date", 1), ("created_at", 1)])
    warehouse = await recon.fittings_warehouse(node_id)
    issued = await recon.fittings_issued(node_id)
    expected = await recon.fittings_expected(node_id, cfg)
    names = {f["code"]: f.get("name", f["code"]) for f in cfg.get("fitting_types", [])}
    codes = set(list(warehouse.keys()) + list(issued.keys()) + list(expected.keys()))
    return {
        "entries": entries,
        "warehouse": [{"fitting_type": c, "name": names.get(c, c),
                       "balance": warehouse.get(c, 0),
                       "issued": issued.get(c, 0), "expected": expected.get(c, 0),
                       "variance": issued.get(c, 0) - expected.get(c, 0)}
                      for c in sorted(codes)],
    }


@app.post("/api/nodes/{node_id}/fittings/adjustment")
async def fittings_adjustment(node_id: str, payload: LedgerAdjustIn,
                              user: dict = Depends(auth.require_role("audit", "admin"))):
    auth.check_node_access(user, node_id)
    if payload.quantity is None or not payload.fitting_type:
        raise HTTPException(400, "quantity and fitting_type required")
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "fitting_type": payload.fitting_type, "type": "count_adjustment",
         "quantity": payload.quantity, "source_capture_id": None,
         "notes": payload.notes, "created_at": _now()}
    await db.fittings_ledger().insert_one(d)
    await audit.log(user, node_id, "create", "fittings_ledger", d["_id"], after=d)
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
    consume = {t["code"]: t["weight_kg"] + t.get("lid_weight_kg", 0.0) for t in cfg["tank_types"]}
    for r in runs:
        r["implied_powder_kg"] = round(
            (r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]) * consume.get(r["tank_type"], 0), 1)
    return runs


@app.get("/api/nodes/{node_id}/finished-goods")
async def finished_goods(node_id: str, user: dict = Depends(auth.current_user)):
    """Tank floor (moulded, not yet booked) + finished-goods warehouse (booked, not dispatched)."""
    auth.check_node_access(user, node_id)
    entries = await _all(db.finished_goods(), {"node_id": node_id},
                         sort=[("date", 1), ("created_at", 1)])
    floor = await recon.tank_floor(node_id)
    store = await recon.fg_warehouse(node_id)
    keys = sorted(set(list(floor.keys()) + list(store.keys())))
    return {
        "entries": entries,
        "positions": [{"tank_type": k[0], "grade": k[1],
                       "floor": floor.get(k, 0), "store": store.get(k, 0),
                       "total": floor.get(k, 0) + store.get(k, 0)}
                      for k in keys],
    }


@app.post("/api/nodes/{node_id}/finished-goods/adjustment")
async def fg_adjustment(node_id: str, payload: LedgerAdjustIn,
                        user: dict = Depends(auth.require_role("audit", "admin"))):
    auth.check_node_access(user, node_id)
    if not payload.tank_type or payload.quantity is None or not payload.grade:
        raise HTTPException(400, "tank_type, grade, quantity required")
    scope = "tank_floor" if payload.scope == "floor" else "fg_warehouse"
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "tank_type": payload.tank_type, "grade": payload.grade,
         "type": "count_adjustment", "scope": scope, "quantity": payload.quantity,
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
                               user: dict = Depends(auth.require_role("operations", "admin"))):
    """A delivery is the stock-out movement AND the priced record we reconcile against.
    It deducts the tanks from finished-goods stock and carries its value in-app; the PDF
    is a plain delivery note with no prices."""
    auth.check_node_access(user, node_id)
    node = await _get_node(node_id)
    cfg = await _get_cfg(node_id)
    if not payload.lines:
        raise HTTPException(400, "A delivery needs at least one line")
    names = {t["code"]: t["name"] for t in cfg["tank_types"]}
    store = await recon.fg_warehouse(node_id)          # tanks in stock, per (tank_type, grade)
    want: dict = {}
    for l in payload.lines:
        if l.tank_type not in names:
            raise HTTPException(400, f"Unknown tank type {l.tank_type}")
        if l.quantity <= 0:
            raise HTTPException(400, "Each line needs a quantity above zero")
        want[(l.tank_type, l.grade)] = want.get((l.tank_type, l.grade), 0) + l.quantity
    for (tt, gr), qty in want.items():
        have = store.get((tt, gr), 0)
        if qty > have:
            raise HTTPException(400, f"Only {have} × {names.get(tt, tt)} grade {gr} in stock — cannot deliver {qty}")

    seq = await db.next_number(node_id, "dn")
    dn_number = f"{node.get('prefix', node_id.upper())}-DN-{seq:04d}"
    dn_id = uuid4().hex
    vat_rate = cfg.get("vat_rate", 15.0)
    subtotal = round(sum(l.quantity * l.unit_price for l in payload.lines), 2)
    vat = round(subtotal * vat_rate / 100.0, 2)
    total = round(subtotal + vat, 2)
    dn = {
        "_id": dn_id, "node_id": node_id, "dn_number": dn_number,
        "date": payload.date, "client_name": payload.client_name,
        "client_details": payload.client_details,
        "lines": [l.model_dump() for l in payload.lines],
        "subtotal": subtotal, "vat_rate": vat_rate, "vat": vat, "total": total,
        "amount_paid": 0.0, "status": "unpaid", "created_at": _now(),
    }
    dn.update(recon.compute_split(dn, cfg, total))     # fenix_exworks_value + partner_balance at full pay
    # deduct the tanks from finished-goods stock — this delivery IS the stock-out movement
    for l in payload.lines:
        await db.finished_goods().insert_one({
            "_id": uuid4().hex, "node_id": node_id, "date": payload.date,
            "tank_type": l.tank_type, "grade": l.grade, "type": "dispatched",
            "quantity": l.quantity, "dn_number": dn_number, "reference": dn_id,
            "created_at": _now()})
    pdf = pdf_gen.delivery_note_pdf(node, cfg, dn)
    dn["content_b64"] = base64.b64encode(pdf).decode()
    dn["pdf_url"] = f"/api/delivery-notes/{dn_id}/pdf"
    await db.delivery_notes().insert_one(dn)
    await audit.log(user, node_id, "create", "delivery_notes", dn_id,
                    after={k: v for k, v in dn.items() if k != "content_b64"})
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


# ============================== payments ============================== #

@app.post("/api/nodes/{node_id}/payments")
async def create_payment(node_id: str, payload: PaymentIn,
                         user: dict = Depends(auth.require_role("audit", "admin"))):
    auth.check_node_access(user, node_id)
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "amount": payload.amount, "bank_reference": payload.bank_reference,
         "matched_delivery_id": None, "split": None, "status": "unmatched",
         "created_at": _now()}
    await db.payments().insert_one(d)
    await audit.log(user, node_id, "create", "payments", d["_id"], after=d)
    return d


@app.post("/api/payments/{payment_id}/match")
async def match_payment(payment_id: str, payload: PaymentMatchIn,
                        user: dict = Depends(auth.require_role("audit", "admin"))):
    """Match a bank receipt to a delivery and compute the Fenix/partner split.
    Fenix draws ex-works value: full for A-grade lines, b_grade_exworks_pct for B."""
    p = await db.payments().find_one({"_id": payment_id})
    if not p:
        raise HTTPException(404, "Payment not found")
    auth.check_node_access(user, p["node_id"])
    dn = await db.delivery_notes().find_one({"_id": payload.delivery_id})
    if not dn or dn["node_id"] != p["node_id"]:
        raise HTTPException(400, "Delivery not found on this node")
    cfg = await _get_cfg(p["node_id"])

    split = recon.compute_split(dn, cfg, p["amount"])
    paid_so_far = 0.0
    async for other in db.payments().find({"matched_delivery_id": dn["_id"]}):
        paid_so_far += other["amount"]
    total_paid = round(paid_so_far + p["amount"], 2)

    status = "matched"
    flags_raised = []
    if total_paid + 0.005 < dn["total"]:
        dn_status = "part_paid"
        status = "flagged"
        flags_raised.append(await recon.raise_flag(
            p["node_id"], "short_paid",
            f"Delivery {dn['dn_number']}: paid R{total_paid:.2f} of R{dn['total']:.2f} "
            f"(short R{dn['total'] - total_paid:.2f}).",
            {"delivery_id": dn["_id"], "payment_id": payment_id}, p["date"]))
    elif total_paid > dn["total"] + 0.005:
        dn_status = "flagged"
        status = "flagged"
        flags_raised.append(await recon.raise_flag(
            p["node_id"], "over_paid",
            f"Delivery {dn['dn_number']}: paid R{total_paid:.2f} against R{dn['total']:.2f} "
            f"(over by R{total_paid - dn['total']:.2f}).",
            {"delivery_id": dn["_id"], "payment_id": payment_id}, p["date"]))
    else:
        dn_status = "paid"

    before = dict(p)
    update = {"matched_delivery_id": dn["_id"], "split": split, "status": status}
    await db.payments().update_one({"_id": payment_id}, {"$set": update})
    await db.delivery_notes().update_one({"_id": dn["_id"]},
                                         {"$set": {"status": dn_status, "amount_paid": total_paid}})
    # matching is the fix for an unmatched-payment flag
    await db.flags().update_many(
        {"node_id": p["node_id"], "type": "payment_unmatched",
         "references.payment_id": payment_id, "status": "open"},
        {"$set": {"status": "resolved", "resolved_by": user["email"],
                  "resolution_note": f"Matched to {dn['dn_number']}."}})
    await audit.log(user, p["node_id"], "match", "payments", payment_id,
                    before=before, after=update)
    return {**p, **update, "delivery_status": dn_status, "flags_raised": flags_raised}


@app.get("/api/nodes/{node_id}/payments")
async def list_payments(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.payments(), {"node_id": node_id}, sort=[("date", -1)])
    if user["role"] == "operations":
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
                       user: dict = Depends(auth.require_role("audit", "admin"))):
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
                       user: dict = Depends(auth.require_role("audit", "admin"))):
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    tol = cfg.get("tolerances") or {}
    tol_kg = tol.get("powder_kg", 0.0)
    tol_tank = tol.get("tank_qty", 0)
    tol_fit = tol.get("fittings_qty", 0)
    date = payload.date
    count_id = uuid4().hex

    sys_wh = await recon.powder_warehouse(node_id, date)
    sys_floor = await recon.powder_floor(node_id, cfg, date)
    sys_tank_floor = await recon.tank_floor(node_id, date)
    sys_store = await recon.fg_warehouse(node_id, date)
    sys_fittings = await recon.fittings_warehouse(node_id, date)

    flags_raised = []
    variances: dict = {"powder_warehouse": [], "powder_floor": [], "tanks": [], "fittings": []}

    async def flag(desc, ref):
        flags_raised.append(await recon.raise_flag(node_id, "count_mismatch", desc, ref, date))

    # powder per grade: warehouse and floor each counted and reconciled separately
    counted_wh = {l.powder_type: l.warehouse_kg for l in payload.powder_counted}
    counted_floor = {l.powder_type: l.floor_kg for l in payload.powder_counted}
    names = {p["code"]: (p.get("colour") or p["code"]) for p in cfg.get("powder_products", [])}
    for pt in set(list(sys_wh.keys()) + list(counted_wh.keys())):
        var = round(counted_wh.get(pt, 0.0) - sys_wh.get(pt, 0.0), 2)
        variances["powder_warehouse"].append({"powder_type": pt, "system": sys_wh.get(pt, 0.0),
                                               "counted": counted_wh.get(pt, 0.0), "variance": var})
        if abs(var) > tol_kg + recon.EPS:
            await flag(f"{date}: {names.get(pt, pt)} warehouse counted {counted_wh.get(pt,0.0):.1f} kg vs "
                       f"system {sys_wh.get(pt,0.0):.1f} kg (variance {var:+.1f} kg).",
                       {"count_id": count_id, "what": "powder_warehouse", "powder_type": pt})
    for pt in set(list(sys_floor.keys()) + list(counted_floor.keys())):
        var = round(counted_floor.get(pt, 0.0) - sys_floor.get(pt, 0.0), 2)
        variances["powder_floor"].append({"powder_type": pt, "system": sys_floor.get(pt, 0.0),
                                          "counted": counted_floor.get(pt, 0.0), "variance": var})
        if abs(var) > tol_kg + recon.EPS:
            await flag(f"{date}: {names.get(pt, pt)} on floor counted {counted_floor.get(pt,0.0):.1f} kg vs "
                       f"system {sys_floor.get(pt,0.0):.1f} kg (variance {var:+.1f} kg).",
                       {"count_id": count_id, "what": "powder_floor", "powder_type": pt})

    # tanks: store + floor counts summed vs system total (moulded - dispatched)
    counted_store = {(l.tank_type, l.grade): l.quantity for l in payload.fg_warehouse_counted}
    counted_tfloor = {(l.tank_type, l.grade): l.quantity for l in payload.tank_floor_counted}
    keys = set(list(sys_tank_floor.keys()) + list(sys_store.keys()) +
               list(counted_store.keys()) + list(counted_tfloor.keys()))
    for k in keys:
        sys_total = sys_tank_floor.get(k, 0) + sys_store.get(k, 0)
        cnt_total = counted_store.get(k, 0) + counted_tfloor.get(k, 0)
        var = cnt_total - sys_total
        variances["tanks"].append({"tank_type": k[0], "grade": k[1], "system": sys_total,
                                   "counted": cnt_total, "variance": var,
                                   "store_counted": counted_store.get(k, 0),
                                   "floor_counted": counted_tfloor.get(k, 0)})
        if abs(var) > tol_tank:
            await flag(f"{date}: {k[0]} grade {k[1]} counted {cnt_total} (store + floor) vs system "
                       f"{sys_total} (variance {var:+d}). Counting only the store throws a false variance.",
                       {"count_id": count_id, "what": "tanks", "tank_type": k[0], "grade": k[1]})

    # fittings warehouse per type
    counted_fit = {l.fitting_type: l.warehouse_qty for l in payload.fittings_counted}
    for ft in set(list(sys_fittings.keys()) + list(counted_fit.keys())):
        var = counted_fit.get(ft, 0) - sys_fittings.get(ft, 0)
        variances["fittings"].append({"fitting_type": ft, "system": sys_fittings.get(ft, 0),
                                      "counted": counted_fit.get(ft, 0), "variance": var})
        if abs(var) > tol_fit:
            await flag(f"{date}: fitting {ft} warehouse counted {counted_fit.get(ft,0)} vs system "
                       f"{sys_fittings.get(ft,0)} (variance {var:+d}).",
                       {"count_id": count_id, "what": "fittings", "fitting_type": ft})

    doc = {
        "_id": uuid4().hex, "node_id": node_id, "date": date,
        "powder_counted": [l.model_dump() for l in payload.powder_counted],
        "fg_warehouse_counted": [l.model_dump() for l in payload.fg_warehouse_counted],
        "tank_floor_counted": [l.model_dump() for l in payload.tank_floor_counted],
        "fittings_counted": [l.model_dump() for l in payload.fittings_counted],
        "system_values_at_count": {
            "powder_warehouse": sys_wh, "powder_floor": sys_floor,
            "tank_floor": [{"tank_type": k[0], "grade": k[1], "quantity": v} for k, v in sorted(sys_tank_floor.items())],
            "fg_warehouse": [{"tank_type": k[0], "grade": k[1], "quantity": v} for k, v in sorted(sys_store.items())],
            "fittings": sys_fittings,
        },
        "variances": variances,
        "counted_by": user["email"], "created_at": _now(),
    }
    await db.physical_counts().insert_one(doc)
    # backfill the real count id into the flags we raised
    await db.flags().update_many(
        {"node_id": node_id, "status": "open", "references.count_id": "pending"},
        {"$set": {"references.count_id": doc["_id"]}})
    await audit.log(user, node_id, "create", "physical_counts", doc["_id"], after=doc)
    return {**doc, "flags_raised": flags_raised}


@app.get("/api/nodes/{node_id}/counts")
async def list_counts(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    return await _all(db.physical_counts(), {"node_id": node_id}, sort=[("date", -1)])


# ============================== reconciliation dashboard ============================== #

@app.post("/api/nodes/{node_id}/recon/sweep")
async def recon_sweep(node_id: str, user: dict = Depends(auth.require_role("audit", "admin"))):
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
        # capture no longer reconciles; a captured day is clear unless a flag (from a
        # stocktake or a sweep) was raised on it. Reconciliation is an afterwards activity.
        if d in flag_dates:
            day_status = "flagged"
        elif cap:
            day_status = "clear"
        else:
            day_status = "no_capture"
        days.append({"date": d, "status": day_status,
                     "capture_id": cap["_id"] if cap else None})

    unmatched = await _all(db.payments(), {"node_id": node_id, "status": "unmatched"})
    unpaid = await _all(db.delivery_notes(), {"node_id": node_id,
                                              "status": {"$in": ["unpaid", "part_paid", "flagged"]}})
    return {
        "month": month,
        "days": days,
        "open_flags": open_flags,
        "unmatched_payments": unmatched,
        "unpaid_deliveries": [{k: v for k, v in d.items() if k != "content_b64"} for d in unpaid],
    }


# ============================== reporting ============================== #

@app.get("/api/nodes/{node_id}/reports/daily")
async def daily_report(node_id: str, date: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    consume = {t["code"]: t["weight_kg"] + t.get("lid_weight_kg", 0.0) for t in cfg["tank_types"]}
    runs = await _all(db.production_runs(), {"node_id": node_id, "date": date})
    powder = await _all(db.powder_ledger(), {"node_id": node_id, "date": date})
    dns = await _all(db.delivery_notes(), {"node_id": node_id, "date": date})
    pays = await _all(db.payments(), {"node_id": node_id, "date": date})
    cap = await db.daily_captures().find_one({"node_id": node_id, "date": date})
    flags_open = await _all(db.flags(), {"node_id": node_id, "date_raised": date, "status": "open"})
    fg_store = await recon.fg_warehouse(node_id, date)
    return {
        "date": date,
        "tanks_produced": [{"tank_type": r["tank_type"], "a": r["quantity_a"],
                            "b": r["quantity_b"], "reject": r["quantity_reject"]} for r in runs],
        "powder_received_kg": sum(e["kg"] for e in powder if e["type"] == "received"),
        "powder_issued_kg": sum(e["kg"] for e in powder if e["type"] == "issued"),
        "implied_kg": sum((r["quantity_a"] + r["quantity_b"] + r["quantity_reject"])
                          * consume.get(r["tank_type"], 0) for r in runs),
        "finished_goods_on_hand": [{"tank_type": k[0], "grade": k[1], "quantity": v}
                                   for k, v in sorted(fg_store.items())],
        "deliveries": [{"dn_number": d["dn_number"], "client_name": d["client_name"],
                        "lines": d["lines"], "total": d.get("total", 0),
                        "status": d.get("status", "unpaid")} for d in dns],
        "payments_received": [{"amount": p["amount"], "status": p["status"]} for p in pays],
        "reconciliation_status": (cap or {}).get("status", "no_capture"),
        "open_flags": len(flags_open),
    }


@app.get("/api/nodes/{node_id}/reports/monthly")
async def monthly_report(node_id: str, month: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    consume = {t["code"]: t["weight_kg"] + t.get("lid_weight_kg", 0.0) for t in cfg["tank_types"]}
    prices = {t["code"]: t["ex_works_price"] for t in cfg["tank_types"]}
    b_pct = cfg.get("b_grade_exworks_pct", 100.0) / 100.0
    filt = {"node_id": node_id, "date": {"$regex": f"^{month}"}}

    runs = await _all(db.production_runs(), filt)
    kg = 0.0
    by_type: dict = {}
    for r in runs:
        total_q = r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]
        kg += total_q * consume.get(r["tank_type"], 0)
        t = by_type.setdefault(r["tank_type"], {"a": 0, "b": 0, "reject": 0})
        t["a"] += r["quantity_a"]; t["b"] += r["quantity_b"]; t["reject"] += r["quantity_reject"]

    dels = await _all(db.delivery_notes(), filt)
    invoiced = sum(d.get("total", 0) for d in dels)
    exworks = 0.0
    for d in dels:
        for l in d["lines"]:
            factor = 1.0 if l["grade"] == "A" else b_pct
            exworks += l["quantity"] * prices.get(l["tank_type"], 0) * factor

    pays = await _all(db.payments(), filt)
    cash = sum(p["amount"] for p in pays)
    outstanding = sum(d.get("total", 0) for d in await _all(
        db.delivery_notes(), {"node_id": node_id, "status": {"$in": ["unpaid", "part_paid"]}}))

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


@app.get("/api/dashboard/network")
async def dashboard_network(user: dict = Depends(auth.current_user)):
    """Per-node headline totals (tanks + material) and a grand total. Material cost admin-only."""
    return await reports.network_dashboard(user.get("node_access", "all"), user["role"] == "admin")


@app.get("/api/nodes/{node_id}/dashboard")
async def node_dashboard(node_id: str, year: Optional[str] = None,
                         user: dict = Depends(auth.current_user)):
    """Month-by-month tanks/material/sold for a node, with year + all-time totals."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    return await reports.node_dashboard(node_id, cfg, year or _today()[:4], user["role"] == "admin")


@app.get("/api/nodes/{node_id}/dashboard/daily")
async def node_dashboard_daily(node_id: str, month: Optional[str] = None,
                               user: dict = Depends(auth.current_user)):
    """Day-by-day tanks produced/sold for a node within `month` (YYYY-MM), grouped by ISO week."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    return await reports.node_daily(node_id, cfg, month or _today()[:7], user["role"] == "admin")


@app.get("/api/network/kg")
async def network_kg(user: dict = Depends(auth.current_user)):
    """The one network number: total kilograms through all nodes."""
    total = 0.0
    consume_by_node: dict = {}
    async for cfg in db.node_config().find({}):
        consume_by_node[cfg["node_id"]] = {
            t["code"]: t["weight_kg"] + t.get("lid_weight_kg", 0.0) for t in cfg["tank_types"]}
    async for r in db.production_runs().find({}):
        c = consume_by_node.get(r["node_id"], {}).get(r["tank_type"], 0)
        total += (r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]) * c
    return {"total_kg": round(total, 1)}


# ============================== audit ============================== #

@app.get("/api/audit")
async def audit_trail(node_id: Optional[str] = None, limit: int = 200,
                      user: dict = Depends(auth.require_role("admin"))):
    filt = {"node_id": node_id} if node_id else {}
    cursor = db.audit_log().find(filt).sort("at", -1).limit(min(limit, 1000))
    return [d async for d in cursor]
