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
from datetime import datetime, date as date_cls, timedelta, timezone
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
import board
import capture
import commerce
import corrections
import db
import partner_auth
import pdf_gen
import pricing
import recon
import reports
from corrections import ACTIVE
from models import (
    CaptureEntriesIn, CorrectionIn, DeliveryNoteEditIn, DeliveryNoteIn,
    DeliveryNoteReissueIn, FlagResolveIn, LedgerAdjustIn, LoginIn, NodeConfigIn, NodeIn,
    PartnerUserIn, PaymentEditIn, PaymentIn, PaymentMatchIn, PhysicalCountIn, UserIn,
)

app = FastAPI(title="Vezubunye API", version="0.1.0")

cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://localhost:5174",
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    # every partner app lives on its own <slug>.vezubunye.com
    allow_origin_regex=os.getenv("PARTNER_ORIGIN_REGEX", r"https://[a-z0-9-]+\.vezubunye\.com"),
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
    try:
        import migrate_brief2
        out = await migrate_brief2.run()
        print(f"brief 2 migration: {out}")
    except Exception as exc:  # noqa: BLE001
        print(f"brief 2 migration skipped: {exc}")
    import scheduler
    scheduler.start()


import driver_api  # noqa: E402
import partner_api  # noqa: E402

app.include_router(partner_api.router)
app.include_router(driver_api.router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


def _now():
    return datetime.utcnow()


SAST = timezone(timedelta(hours=2))  # South Africa has no daylight saving


def _today() -> str:
    return date_cls.today().isoformat()


async def _all(coll, filt=None, sort=None):
    cursor = coll.find(filt or {})
    if sort:
        cursor = cursor.sort(sort)
    return [d async for d in cursor]


def _shown(filt: dict, include_voided: bool = False) -> dict:
    """List filter: voided rows are hidden unless the caller asks to see them."""
    return filt if include_voided else {**filt, **ACTIVE}


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


def _check_slug(slug: str | None):
    if slug is None:
        return
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,30}", slug) or slug in partner_auth.RESERVED_SLUGS:
        raise HTTPException(400, "Slug must be lowercase letters, digits or dashes, and not www, api, app or admin")


@app.post("/api/nodes")
async def create_node(payload: NodeIn, user: dict = Depends(auth.require_role("admin"))):
    if await db.nodes().find_one({"node_id": payload.node_id}):
        raise HTTPException(400, "node_id already exists")
    _check_slug(payload.slug)
    if payload.slug and await db.nodes().find_one({"slug": payload.slug}):
        raise HTTPException(400, "Slug already in use")
    doc = {"_id": uuid4().hex, **payload.model_dump(exclude_none=True), "created_at": _now()}
    await db.nodes().insert_one(doc)
    await audit.log(user, payload.node_id, "create", "nodes", doc["_id"], after=doc)
    return doc


@app.put("/api/nodes/{node_id}")
async def update_node(node_id: str, payload: NodeIn,
                      user: dict = Depends(auth.require_role("admin"))):
    before = await _get_node(node_id)
    update = payload.model_dump(exclude_none=True)
    _check_slug(payload.slug)
    if payload.slug and await db.nodes().find_one({"slug": payload.slug, "node_id": {"$ne": node_id}}):
        raise HTTPException(400, "Slug already in use")
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
    # Brief 2 fields left out of the body stay as they are (the partner app edits some of them)
    doc = {"node_id": node_id, **payload.model_dump(exclude_none=True), "updated_at": _now()}
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


async def _internal_capture_open(node_id: str, user: dict):
    """When the node's partner app is live, capture happens there; the internal capture
    screens are read-only. Admin corrections keep working."""
    node = await _get_node(node_id)
    if node.get("partner_app_live") and user["role"] != "admin":
        raise HTTPException(403, "This node captures in its own app. The internal capture screens are read-only.")
    return node


@app.post("/api/nodes/{node_id}/captures")
async def create_capture(node_id: str, date: str,
                         user: dict = Depends(auth.require_role("operations", "admin"))):
    auth.check_node_access(user, node_id)
    node = await _internal_capture_open(node_id, user)
    if node.get("partner_app_live") and not await db.daily_captures().find_one({"node_id": node_id, "date": date, **ACTIVE}):
        raise HTTPException(403, "This node captures in its own app. Admin corrects existing captures only.")
    return await capture.open_capture(node_id, date, user)


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
    """Key the day's sheet (see capture.key_entries). Operations may fix a sheet on the same
    (SAST) day it was first captured; after that it is admin only."""
    cap = await db.daily_captures().find_one({"_id": capture_id})
    if not cap:
        raise HTTPException(404, "Capture not found")
    auth.check_node_access(user, cap["node_id"])
    await _internal_capture_open(cap["node_id"], user)
    cfg = await _get_cfg(cap["node_id"])
    return await capture.key_entries(cap, payload, user, cfg, can_correct_any_day=user["role"] == "admin")


@app.get("/api/nodes/{node_id}/captures")
async def list_captures(node_id: str, month: Optional[str] = None, include_voided: bool = False,
                        user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    filt: dict = {"node_id": node_id}
    if month:
        filt["date"] = {"$regex": f"^{month}"}
    docs = await _all(db.daily_captures(), _shown(filt, include_voided), sort=[("date", -1)])
    return [{k: v for k, v in d.items() if k != "photo_b64"} for d in docs]


@app.post("/api/captures/{capture_id}/void")
async def void_capture(capture_id: str, payload: CorrectionIn,
                       user: dict = Depends(auth.require_role("admin"))):
    """Void a day's capture and every movement it wrote. The photo is retained."""
    cap = await db.daily_captures().find_one({"_id": capture_id})
    if not cap:
        raise HTTPException(404, "Capture not found")
    auth.check_node_access(user, cap["node_id"])
    return await corrections.void_record("daily_captures", capture_id, user, payload.reason,
                                         corrections.capture_cascade(capture_id))


# ============================== ledgers ============================== #

@app.get("/api/nodes/{node_id}/powder")
async def powder_ledger(node_id: str, include_voided: bool = False,
                        user: dict = Depends(auth.current_user)):
    """Per-grade warehouse + production-floor balances. Each colour is a distinct material."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    entries = await _all(db.powder_ledger(), _shown({"node_id": node_id}, include_voided), sort=[("date", 1), ("created_at", 1)])
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
async def paraffin(node_id: str, include_voided: bool = False,
                   user: dict = Depends(auth.current_user)):
    """Paraffin (release agent) stock: received less consumed by moulding, plus movements."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    bal = await recon.paraffin_balance(node_id, cfg)
    entries = await _all(db.paraffin_ledger(), _shown({"node_id": node_id}, include_voided),
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
async def fittings(node_id: str, include_voided: bool = False,
                   user: dict = Depends(auth.current_user)):
    """Per-type warehouse balance + issued-vs-expected (tanks produced x fittings-per-tank)."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    entries = await _all(db.fittings_ledger(), _shown({"node_id": node_id}, include_voided), sort=[("date", 1), ("created_at", 1)])
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
    filt: dict = {"node_id": node_id, **ACTIVE}
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
async def finished_goods(node_id: str, include_voided: bool = False,
                         user: dict = Depends(auth.current_user)):
    """Tank floor (moulded, not yet booked) + finished-goods warehouse (booked, not dispatched)."""
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    entries = await _all(db.finished_goods(), _shown({"node_id": node_id}, include_voided),
                         sort=[("date", 1), ("created_at", 1)])
    return {"entries": entries, "positions": await fg_positions(node_id, cfg)}


async def fg_positions(node_id: str, cfg: dict) -> list[dict]:
    """Per (type, grade), or per (type, colour, grade) when the node tracks colour stock."""
    floor = await recon.tank_floor(node_id, cfg=cfg)
    store = await recon.fg_warehouse(node_id, cfg=cfg)
    keys = sorted(set(list(floor.keys()) + list(store.keys())))
    out = []
    for k in keys:
        row = {"tank_type": k[0], "grade": k[-1], "floor": floor.get(k, 0), "store": store.get(k, 0),
               "total": floor.get(k, 0) + store.get(k, 0)}
        if len(k) == 3:
            row["colour"] = k[1]
        out.append(row)
    return out


@app.post("/api/nodes/{node_id}/finished-goods/adjustment")
async def fg_adjustment(node_id: str, payload: LedgerAdjustIn,
                        user: dict = Depends(auth.require_role("audit", "admin"))):
    auth.check_node_access(user, node_id)
    if not payload.tank_type or payload.quantity is None or not payload.grade:
        raise HTTPException(400, "tank_type, grade, quantity required")
    scope = "tank_floor" if payload.scope == "floor" else "fg_warehouse"
    d = {"_id": uuid4().hex, "node_id": node_id, "date": payload.date,
         "tank_type": payload.tank_type, "colour": payload.colour, "grade": payload.grade,
         "type": "count_adjustment", "scope": scope, "quantity": payload.quantity,
         "reference": None, "notes": payload.notes, "created_at": _now()}
    await db.finished_goods().insert_one(d)
    await audit.log(user, node_id, "create", "finished_goods_ledger", d["_id"], after=d)
    return d


@app.get("/api/nodes/{node_id}/scrap")
async def scrap(node_id: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.scrap_log(), {"node_id": node_id, **ACTIVE}, sort=[("date", -1)])
    if user["role"] != "admin":
        for d in docs:
            d.pop("material_cost_lost", None)
    return docs


# Adjustments are the only ledger rows voided one by one. Rows written by a capture or a
# delivery are voided through that capture or delivery, so the pair can never split.
ADJUSTABLE_LEDGERS = {"powder_ledger", "fittings_ledger", "finished_goods_ledger"}


@app.post("/api/ledger/{collection}/{row_id}/void")
async def void_ledger_adjustment(collection: str, row_id: str, payload: CorrectionIn,
                                 user: dict = Depends(auth.require_role("audit", "admin"))):
    """Correct an adjustment by voiding it and entering a new one."""
    if collection not in ADJUSTABLE_LEDGERS:
        raise HTTPException(400, f"Unknown ledger {collection}")
    row = await corrections.coll(collection).find_one({"_id": row_id})
    if not row:
        raise HTTPException(404, "Ledger row not found")
    auth.check_node_access(user, row["node_id"])
    if row.get("type") != "count_adjustment":
        raise HTTPException(400, "Only adjustments are voided here. Void the capture or delivery that wrote this row.")
    return await corrections.void_record(collection, row_id, user, payload.reason)


# ============================== delivery notes ============================== #

async def _check_dn_stock(node_id: str, cfg: dict, payload: DeliveryNoteIn,
                          freed: dict | None = None):
    """Every line must be a known tank with quantity > 0, and in stock (per colour when the
    node tracks colour). `freed` adds back stock a reissue is about to return."""
    if not payload.lines:
        raise HTTPException(400, "A delivery needs at least one line")
    names = {t["code"]: t["name"] for t in cfg["tank_types"]}
    want: dict = {}
    for l in payload.lines:
        if l.tank_type not in names:
            raise HTTPException(400, f"Unknown tank type {l.tank_type}")
        if l.quantity <= 0:
            raise HTTPException(400, "Each line needs a quantity above zero")
        k = recon.fg_key(cfg, l.tank_type, l.grade, l.colour)
        want[k] = want.get(k, 0) + l.quantity
    await commerce.check_stock(node_id, cfg, want, freed)


async def _write_dn(node: dict, cfg: dict, payload: DeliveryNoteIn, user: dict, session=None,
                    extra: dict | None = None) -> dict:
    """An internal delivery note is the stock-out and the priced record. Since Brief 2 it sits
    on the commercial chain like every other: a legacy order (terms, priced ex VAT as keyed)
    and a legacy invoice for exactly its lines, so payments allocate to it the same way."""
    node_id = node["node_id"]
    cust = await db.customers().find_one({"node_id": node_id, "name": payload.client_name}, session=session)
    if not cust:
        cust = {"_id": uuid4().hex, "node_id": node_id, "name": payload.client_name, "contact_person": "",
                "email": "", "phone": "", "billing_address": payload.client_details, "vat_number": "",
                "payment_basis": "terms", "payment_terms_days": cfg.get("payment_terms_days", 30),
                "price_tier": "retail", "xero_contact_id": None, "active": True,
                "sites": [{"site_id": uuid4().hex[:10], "label": "Main", "address": payload.client_details,
                           "town": "", "maps_link": "", "lat": None, "lng": None, "pinned": False,
                           "contact_name": "", "contact_phone": "", "directions": ""}],
                "date": payload.date, "created_at": _now(), "created_by": user["email"]}
        await db.customers().insert_one(cust, session=session)
    lines = []
    for i, l in enumerate(payload.lines):
        lines.append(pricing.price_line({"line_id": f"L{i + 1}", "kind": "tank", "tank_type": l.tank_type,
                                         "colour": l.colour, "grade": l.grade, "qty": l.quantity,
                                         "unit_price": l.unit_price, "price_includes_vat": False,
                                         "qty_cancelled": 0}, cfg))
    order = {"_id": uuid4().hex, "node_id": node_id, "order_number": None, "date": payload.date,
             "customer_id": cust["_id"], "customer_name": payload.client_name,
             "site": (cust.get("sites") or [None])[0], "quote_id": None, "payment_basis": "terms",
             "pay_on_delivery": False, "cod_method": None, "price_tier": "retail",
             "price_list_version_id": None, "lines": lines, **pricing.totals(lines, cfg),
             "requested_date": None, "notes": "", "status": "open", "legacy": True, "origin": "internal",
             "created_by": user["email"], "created_at": _now()}
    await db.orders().insert_one(order, session=session)
    dn = await commerce.write_dn(node, cfg, order, {
        "date": payload.date, "client_details": payload.client_details,
        "lines": [{"order_line_id": ol["line_id"], "quantity": ol["qty"]} for ol in lines]},
        user, session=session, skip_checks=True,
        extra={"legacy": True, "origin": "internal", "delivery_status": "delivered",
               "client_name": payload.client_name, **(extra or {})})
    await db.orders().update_one({"_id": order["_id"]}, {"$set": {"display_number": dn["dn_number"]}}, session=session)
    inv = await commerce.create_invoice(node, cfg, order, lines, user, dn=dn, session=session, legacy=True)
    await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {"invoice_id": inv["_id"]}}, session=session)
    dn["invoice_id"] = inv["_id"]
    pdf = pdf_gen.delivery_note_pdf(node, cfg, dn)
    dn["content_b64"] = base64.b64encode(pdf).decode()
    dn["pdf_url"] = f"/api/delivery-notes/{dn['_id']}/pdf"
    await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {"content_b64": dn["content_b64"],
                                                                      "pdf_url": dn["pdf_url"]}}, session=session)
    return dn


def _dn_out(dn: dict) -> dict:
    return {k: v for k, v in dn.items() if k not in ("content_b64", "driver_token")}


@app.post("/api/nodes/{node_id}/delivery-notes")
async def create_delivery_note(node_id: str, payload: DeliveryNoteIn,
                               user: dict = Depends(auth.require_role("operations", "admin"))):
    """A delivery is the stock-out movement AND the priced record we reconcile against.
    It deducts the tanks from finished-goods stock and carries its value in-app; the PDF
    is a plain delivery note with no prices."""
    auth.check_node_access(user, node_id)
    node = await _get_node(node_id)
    if node.get("partner_app_live"):
        raise HTTPException(403, "This node delivers from its own app. Delivery notes are created there.")
    cfg = await _get_cfg(node_id)
    await _check_dn_stock(node_id, cfg, payload)
    async with corrections.txn() as s:
        dn = await _write_dn(node, cfg, payload, user, s)
        await audit.log(user, node_id, "create", "delivery_notes", dn["_id"], after=_dn_out(dn),
                        session=s)
    return _dn_out(dn)


@app.get("/api/nodes/{node_id}/delivery-notes")
async def list_delivery_notes(node_id: str, include_voided: bool = False,
                              user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.delivery_notes(), _shown({"node_id": node_id}, include_voided),
                      sort=[("dn_number", -1)])
    counts: dict = {}
    async for dd in db.delivery_documents().find({"node_id": node_id}, {"delivery_id": 1}):
        counts[dd["delivery_id"]] = counts.get(dd["delivery_id"], 0) + 1
    return [{**_dn_out(d), "document_count": counts.get(d["_id"], 0)} for d in docs]


async def _correctable_dn(dn_id: str, user: dict) -> dict:
    dn = await db.delivery_notes().find_one({"_id": dn_id})
    if not dn:
        raise HTTPException(404, "Delivery note not found")
    auth.check_node_access(user, dn["node_id"])
    if dn.get("void"):
        raise HTTPException(400, f"{dn['dn_number']} is void")
    return dn


async def _block_if_paid(dn: dict):
    inv_ids = [i["_id"] async for i in db.invoices().find({"dn_id": dn["_id"], **ACTIVE})]
    if await db.payments().find_one({"$or": [{"matched_delivery_id": dn["_id"]},
                                             {"allocations.invoice_id": {"$in": inv_ids}}], **ACTIVE}):
        raise HTTPException(400, f"{dn['dn_number']} has a matched payment. Unmatch it first.")


async def _void_dn_chain(dn: dict, user: dict, reason: str, session=None):
    """Voiding a delivery also voids the invoice that covers exactly it (terms or legacy) and,
    for an internal delivery, its legacy order."""
    async for inv in db.invoices().find({"dn_id": dn["_id"], **ACTIVE}, session=session):
        await corrections.mark_void("invoices", inv["_id"], user, reason, session=session, extra={"status": "void"})
    if dn.get("origin") == "internal" and dn.get("order_id"):
        await db.orders().update_one({"_id": dn["order_id"]}, {"$set": {"status": "cancelled"}}, session=session)


@app.patch("/api/delivery-notes/{dn_id}")
async def edit_delivery_note(dn_id: str, payload: DeliveryNoteEditIn,
                             user: dict = Depends(auth.require_role("admin"))):
    """Header-only edit (client name/details). Anything that moves stock or value is a reissue."""
    reason = corrections.require_reason(payload.reason)
    dn = await _correctable_dn(dn_id, user)
    update = {k: v for k, v in (("client_name", payload.client_name),
                                ("client_details", payload.client_details)) if v is not None}
    if not update or all(dn.get(k) == v for k, v in update.items()):
        raise HTTPException(400, "Nothing to change")
    if "client_name" in update and not update["client_name"].strip():
        raise HTTPException(400, "Client name required")
    node = await _get_node(dn["node_id"])
    cfg = await _get_cfg(dn["node_id"])
    update["content_b64"] = base64.b64encode(pdf_gen.delivery_note_pdf(node, cfg, {**dn, **update})).decode()
    await db.delivery_notes().update_one({"_id": dn_id}, {"$set": update})
    await audit.log(user, dn["node_id"], "edit", "delivery_notes", dn_id,
                    before={"record": dn, "reason": reason}, after=update)
    post = await corrections.after_correction(dn["node_id"], "delivery_notes", dn_id, "edit",
                                              dn["date"], moves_stock=False)
    return {**_dn_out({**dn, **update}), **post}


@app.post("/api/delivery-notes/{dn_id}/void")
async def void_delivery_note(dn_id: str, payload: CorrectionIn,
                             user: dict = Depends(auth.require_role("admin"))):
    """Void a delivery: the note keeps its number and its tanks return to stock."""
    reason = corrections.require_reason(payload.reason)
    dn = await _correctable_dn(dn_id, user)
    await _block_if_paid(dn)
    async with corrections.txn() as s:
        await _void_dn_chain(dn, user, reason, s)
    out = await corrections.void_record("delivery_notes", dn_id, user, payload.reason,
                                        corrections.delivery_cascade(dn_id))
    if dn.get("order_id"):
        await commerce.refresh_order_status(dn["order_id"])
    return {k: v for k, v in out.items() if k != "driver_token"}


@app.post("/api/delivery-notes/{dn_id}/reissue")
async def reissue_delivery_note(dn_id: str, payload: DeliveryNoteReissueIn,
                                user: dict = Depends(auth.require_role("admin"))):
    """Void the old note (returning its stock) and issue a new one under the next number,
    in one transaction. The pair link both ways: replaces / superseded_by."""
    reason = corrections.require_reason(payload.reason)
    old = await _correctable_dn(dn_id, user)
    await _block_if_paid(old)
    node_id = old["node_id"]
    node = await _get_node(node_id)
    cfg = await _get_cfg(node_id)
    freed: dict = {}
    async for e in db.finished_goods().find({"reference": dn_id, "type": "dispatched", **ACTIVE}):
        k = recon.fg_key(cfg, e["tank_type"], e["grade"], e.get("colour"))
        freed[k] = freed.get(k, 0) + e["quantity"]
    new_in = DeliveryNoteIn(**payload.model_dump(exclude={"reason"}))
    await _check_dn_stock(node_id, cfg, new_in, freed)
    async with corrections.txn() as s:
        new = await _write_dn(node, cfg, new_in, user, s, extra={"replaces": old["dn_number"]})
        await _void_dn_chain(old, user, reason, s)
        await corrections.mark_void("delivery_notes", dn_id, user, reason,
                                    corrections.delivery_cascade(dn_id), s,
                                    extra={"superseded_by": new["dn_number"]})
        await audit.log(user, node_id, "reissue", "delivery_notes", new["_id"],
                        before={"replaces": old["dn_number"], "replaces_id": dn_id, "reason": reason},
                        after=_dn_out(new), session=s)
    post = await corrections.after_correction(node_id, "delivery_notes", dn_id, "reissue",
                                              min(old["date"], new["date"]))
    return {**_dn_out(new), **post}


@app.get("/api/delivery-notes/{dn_id}/pdf")
async def delivery_note_pdf_endpoint(dn_id: str, user: dict = Depends(auth.current_user)):
    dn = await db.delivery_notes().find_one({"_id": dn_id})
    if not dn:
        raise HTTPException(404, "Delivery note not found")
    auth.check_node_access(user, dn["node_id"])
    return Response(base64.b64decode(dn["content_b64"]), media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{dn["dn_number"]}.pdf"'})


# ---------- delivery documents (invoice, payment notification, etc.) ---------- #

async def _get_delivery(dn_id: str, user: dict) -> dict:
    dn = await db.delivery_notes().find_one({"_id": dn_id})
    if not dn:
        raise HTTPException(404, "Delivery not found")
    auth.check_node_access(user, dn["node_id"])
    return dn


@app.post("/api/delivery-notes/{dn_id}/documents")
async def upload_delivery_document(dn_id: str, file: UploadFile = File(...),
                                   user: dict = Depends(auth.require_role("operations", "admin"))):
    """Attach a supporting document (invoice, proof of payment, etc.) to a delivery."""
    dn = await _get_delivery(dn_id, user)
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(400, "File too large (max 10 MB)")
    doc = {"_id": uuid4().hex, "node_id": dn["node_id"], "delivery_id": dn_id,
           "filename": file.filename or "document",
           "content_type": file.content_type or "application/octet-stream",
           "size": len(content), "content_b64": base64.b64encode(content).decode(),
           "uploaded_by": user["email"], "uploaded_at": _now()}
    await db.delivery_documents().insert_one(doc)
    await audit.log(user, dn["node_id"], "create", "delivery_documents", doc["_id"],
                    after={k: v for k, v in doc.items() if k != "content_b64"})
    return {k: v for k, v in doc.items() if k != "content_b64"}


@app.get("/api/delivery-notes/{dn_id}/documents")
async def list_delivery_documents(dn_id: str, user: dict = Depends(auth.current_user)):
    await _get_delivery(dn_id, user)
    docs = await _all(db.delivery_documents(), {"delivery_id": dn_id}, sort=[("uploaded_at", 1)])
    return [{k: v for k, v in d.items() if k != "content_b64"} for d in docs]


@app.get("/api/delivery-documents/{doc_id}/content")
async def get_delivery_document(doc_id: str, user: dict = Depends(auth.current_user)):
    doc = await db.delivery_documents().find_one({"_id": doc_id})
    if not doc:
        raise HTTPException(404, "Document not found")
    auth.check_node_access(user, doc["node_id"])
    return Response(base64.b64decode(doc["content_b64"]),
                    media_type=doc.get("content_type", "application/octet-stream"),
                    headers={"Content-Disposition": f'inline; filename="{doc["filename"]}"'})


@app.delete("/api/delivery-documents/{doc_id}")
async def delete_delivery_document(doc_id: str,
                                   user: dict = Depends(auth.require_role("operations", "admin"))):
    doc = await db.delivery_documents().find_one({"_id": doc_id})
    if not doc:
        raise HTTPException(404, "Document not found")
    auth.check_node_access(user, doc["node_id"])
    await db.delivery_documents().delete_one({"_id": doc_id})
    await audit.log(user, doc["node_id"], "delete", "delivery_documents", doc_id,
                    before={k: v for k, v in doc.items() if k != "content_b64"})
    return {"deleted": True}


# ============================== payments ============================== #
# Brief 2: a payment is allocated to one or more invoices of the same customer. "Matching" a
# bank receipt to a delivery in the internal app allocates it to that delivery's invoice.

@app.post("/api/nodes/{node_id}/payments")
async def create_payment(node_id: str, payload: PaymentIn,
                         user: dict = Depends(auth.require_role("audit", "admin"))):
    auth.check_node_access(user, node_id)
    async with corrections.txn() as s:
        p = await commerce.record_payment(node_id, {"date": payload.date, "amount": payload.amount,
                                                    "bank_reference": payload.bank_reference, "method": "eft"},
                                          user, session=s, auto_allocate=False,
                                          # an unallocated receipt is flagged by the sweep, not at
                                          # capture: matching usually follows
                                          flag_unallocated=False)
    return p


# tolerance absorbs cent-level VAT rounding; genuine short/over payments still flag
PAY_ROUND_TOL = commerce.PAY_TOL


@app.post("/api/payments/{payment_id}/match")
async def match_payment(payment_id: str, payload: PaymentMatchIn,
                        user: dict = Depends(auth.require_role("audit", "admin"))):
    """Match a bank receipt to a delivery: allocate it to that delivery's invoice and compute
    the Fenix/partner split (proportional to what is paid on the invoice)."""
    p = await db.payments().find_one({"_id": payment_id, **ACTIVE})
    if not p:
        raise HTTPException(404, "Payment not found")
    auth.check_node_access(user, p["node_id"])
    if p.get("allocations"):
        raise HTTPException(400, "Payment is already matched. Unmatch it first.")
    dn = await db.delivery_notes().find_one({"_id": payload.delivery_id, **ACTIVE})
    if not dn or dn["node_id"] != p["node_id"]:
        raise HTTPException(400, "Delivery not found on this node")
    inv = await db.invoices().find_one({"dn_id": dn["_id"], **ACTIVE})
    if not inv and dn.get("order_id"):
        inv = await db.invoices().find_one({"order_id": dn["order_id"], "dn_id": None, **ACTIVE})
    if not inv:
        raise HTTPException(400, f"{dn['dn_number']} has no invoice to allocate to")
    cfg = await _get_cfg(p["node_id"])
    total_paid = round(inv.get("amount_paid", 0.0) + p["amount"], 2)
    flags_raised = []
    if total_paid + PAY_ROUND_TOL < inv["total"]:
        dn_status, status = "part_paid", "flagged"
        flags_raised.append(await recon.raise_flag(
            p["node_id"], "short_paid",
            f"Delivery {dn['dn_number']}: paid R{total_paid:.2f} of R{inv['total']:.2f} "
            f"(short R{inv['total'] - total_paid:.2f}).",
            {"delivery_id": dn["_id"], "payment_id": payment_id}, p["date"]))
    elif total_paid > inv["total"] + PAY_ROUND_TOL:
        dn_status, status = "flagged", "flagged"
        flags_raised.append(await recon.raise_flag(
            p["node_id"], "over_paid",
            f"Delivery {dn['dn_number']}: paid R{total_paid:.2f} against R{inv['total']:.2f} "
            f"(over by R{total_paid - inv['total']:.2f}).",
            {"delivery_id": dn["_id"], "payment_id": payment_id}, p["date"]))
    else:
        dn_status, status = "paid", "matched"
    before = dict(p)
    allocations = [{"invoice_id": inv["_id"], "amount": p["amount"]}]
    async with corrections.txn() as s:
        update = {"allocations": allocations, "unallocated": 0.0, "customer_id": inv.get("customer_id"),
                  "matched_delivery_id": dn["_id"], "status": status}
        update["split"] = await commerce.payment_split({**p, **update}, cfg, s)
        await db.payments().update_one({"_id": payment_id}, {"$set": update}, session=s)
        await commerce.refresh_invoice(inv["_id"], s)
        await db.delivery_notes().update_one({"_id": dn["_id"]},
                                             {"$set": {"status": dn_status, "amount_paid": total_paid}}, session=s)
        await audit.log(user, p["node_id"], "match", "payments", payment_id, before=before, after=update, session=s)
    # matching is the fix for an unallocated-payment flag
    await db.flags().update_many(
        {"node_id": p["node_id"], "type": {"$in": ["payment_unmatched", "payment_unallocated"]},
         "references.payment_id": payment_id, "status": "open"},
        {"$set": {"status": "resolved", "resolved_by": user["email"],
                  "resolution_note": f"Matched to {dn['dn_number']}."}})
    if dn.get("order_id"):
        await commerce.refresh_order_status(dn["order_id"])
    return {**p, **update, "delivery_status": dn_status, "flags_raised": flags_raised}


@app.post("/api/payments/{payment_id}/allocate")
async def allocate_payment(payment_id: str, payload: dict,
                           user: dict = Depends(auth.require_role("audit", "admin"))):
    """Reallocate a payment across invoices of the same customer (Fenix audit)."""
    reason = corrections.require_reason(payload.get("reason"))
    p = await db.payments().find_one({"_id": payment_id})
    if not p:
        raise HTTPException(404, "Payment not found")
    auth.check_node_access(user, p["node_id"])
    out = await commerce.reallocate(payment_id, payload.get("allocations") or [], user, reason)
    await recon.run_sweeps(p["node_id"])
    return out


@app.get("/api/nodes/{node_id}/payments")
async def list_payments(node_id: str, include_voided: bool = False,
                        user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    docs = await _all(db.payments(), _shown({"node_id": node_id}, include_voided), sort=[("date", -1)])
    if user["role"] == "operations":
        for d in docs:
            d.pop("split", None)
    return docs


async def _correctable_payment(payment_id: str, user: dict, unmatched: bool) -> dict:
    p = await db.payments().find_one({"_id": payment_id})
    if not p:
        raise HTTPException(404, "Payment not found")
    auth.check_node_access(user, p["node_id"])
    if p.get("void"):
        raise HTTPException(400, "Payment is void")
    if unmatched and p.get("allocations"):
        raise HTTPException(400, "Payment is matched. Unmatch it first.")
    return p


@app.patch("/api/payments/{payment_id}")
async def edit_payment(payment_id: str, payload: PaymentEditIn,
                       user: dict = Depends(auth.require_role("audit", "admin"))):
    """Fix a keyed bank receipt while it is still unmatched."""
    reason = corrections.require_reason(payload.reason)
    p = await _correctable_payment(payment_id, user, unmatched=True)
    update = {k: v for k, v in (("date", payload.date), ("amount", payload.amount),
                                ("bank_reference", payload.bank_reference))
              if v is not None and p.get(k) != v}
    if not update:
        raise HTTPException(400, "Nothing to change")
    if "amount" in update:
        update["unallocated"] = update["amount"]
    if "bank_reference" in update:
        update["reference"] = update["bank_reference"]
    await db.payments().update_one({"_id": payment_id}, {"$set": update})
    await audit.log(user, p["node_id"], "edit", "payments", payment_id,
                    before={"record": p, "reason": reason}, after=update)
    post = await corrections.after_correction(p["node_id"], "payments", payment_id, "edit",
                                              min(p["date"], update.get("date", p["date"])),
                                              moves_stock=False)
    return {**p, **update, **post}


@app.post("/api/payments/{payment_id}/void")
async def void_payment(payment_id: str, payload: CorrectionIn,
                       user: dict = Depends(auth.require_role("audit", "admin"))):
    corrections.require_reason(payload.reason)
    await _correctable_payment(payment_id, user, unmatched=True)
    return await corrections.void_record("payments", payment_id, user, payload.reason,
                                         moves_stock=False)


@app.post("/api/payments/{payment_id}/unmatch")
async def unmatch_payment(payment_id: str, payload: CorrectionIn,
                          user: dict = Depends(auth.require_role("audit", "admin"))):
    """Undo a match (every allocation). Each invoice's paid amount and status, and the mirrored
    delivery status, are recomputed. Flags the original match raised stay open for audit."""
    reason = corrections.require_reason(payload.reason)
    p = await _correctable_payment(payment_id, user, unmatched=False)
    if not p.get("allocations"):
        raise HTTPException(400, "Payment is not matched")
    dn_id = p.get("matched_delivery_id")
    dn = await db.delivery_notes().find_one({"_id": dn_id}) if dn_id else None
    async with corrections.txn() as s:
        update = {"allocations": [], "unallocated": p["amount"], "matched_delivery_id": None,
                  "split": None, "status": "unmatched"}
        await db.payments().update_one({"_id": payment_id}, {"$set": update}, session=s)
        for a in p["allocations"]:
            await commerce.refresh_invoice(a["invoice_id"], s)
        dn_update = None
        if dn:
            fresh = await db.delivery_notes().find_one({"_id": dn_id}, session=s)
            dn_update = {"amount_paid": fresh.get("amount_paid", 0.0), "status": fresh.get("status")}
        await audit.log(user, p["node_id"], "unmatch", "payments", payment_id,
                        before={"record": p, "delivery": {k: dn.get(k) for k in ("dn_number", "amount_paid", "status")} if dn else None,
                                "reason": reason},
                        after={**update, "delivery": dn_update}, session=s)
    post = await corrections.after_correction(p["node_id"], "payments", payment_id, "unmatch",
                                              p["date"], moves_stock=False)
    return {**p, **update, "delivery": dn_update, **post}


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


@app.post("/api/flags/{flag_id}/reopen")
async def reopen_flag(flag_id: str, payload: CorrectionIn,
                      user: dict = Depends(auth.require_role("admin"))):
    """Flags are never deleted. A resolved flag can be reopened with a note; the prior
    resolution is kept in the flag's history."""
    reason = corrections.require_reason(payload.reason)
    f = await db.flags().find_one({"_id": flag_id})
    if not f:
        raise HTTPException(404, "Flag not found")
    auth.check_node_access(user, f["node_id"])
    if f["status"] != "resolved":
        raise HTTPException(400, "Flag is not resolved")
    prior = {"resolved_by": f.get("resolved_by"), "resolution_note": f.get("resolution_note"),
             "resolved_at": f.get("resolved_at"), "reopened_by": user["email"],
             "reopened_at": _now(), "reopen_note": reason}
    update = {"status": "open", "resolved_by": None, "resolution_note": None, "resolved_at": None}
    await db.flags().update_one({"_id": flag_id}, {"$set": update, "$push": {"history": prior}})
    await audit.log(user, f["node_id"], "reopen", "flags", flag_id, before=f,
                    after={**update, "history_added": prior})
    post = await corrections.after_correction(f["node_id"], "flags", flag_id, "reopen",
                                              f.get("date_raised"), moves_stock=False)
    return {**f, **update, "history": [*f.get("history", []), prior], **post}


# ============================== physical counts ============================== #

@app.post("/api/nodes/{node_id}/counts")
async def create_count(node_id: str, payload: PhysicalCountIn,
                       user: dict = Depends(auth.require_role("operations", "audit", "admin"))):
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
    sys_tank_floor = await recon.tank_floor(node_id, date, cfg)
    sys_store = await recon.fg_warehouse(node_id, date, cfg)
    sys_fittings = await recon.fittings_warehouse(node_id, date)

    flags_raised = []
    variances: dict = {"powder_warehouse": [], "powder_floor": [], "tanks": [], "fittings": []}

    async def flag(desc, ref):
        flags_raised.append(await recon.raise_flag(node_id, "count_mismatch", desc, ref, date))

    # powder per grade: warehouse and floor each counted and reconciled separately
    counted_wh = {l.powder_type: l.warehouse_kg for l in payload.powder_counted}
    # floor powder reconciles per colour grade, with every black grade pooled (R1)
    counted_floor: dict = {}
    for l in payload.powder_counted:
        k = recon.floor_key(cfg, l.powder_type)
        counted_floor[k] = counted_floor.get(k, 0.0) + l.floor_kg
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
    counted_store: dict = {}
    for l in payload.fg_warehouse_counted:
        k = recon.fg_key(cfg, l.tank_type, l.grade, l.colour)
        counted_store[k] = counted_store.get(k, 0) + l.quantity
    counted_tfloor: dict = {}
    for l in payload.tank_floor_counted:
        k = recon.fg_key(cfg, l.tank_type, l.grade, l.colour)
        counted_tfloor[k] = counted_tfloor.get(k, 0) + l.quantity
    keys = set(list(sys_tank_floor.keys()) + list(sys_store.keys()) +
               list(counted_store.keys()) + list(counted_tfloor.keys()))
    for k in keys:
        sys_total = sys_tank_floor.get(k, 0) + sys_store.get(k, 0)
        cnt_total = counted_store.get(k, 0) + counted_tfloor.get(k, 0)
        var = cnt_total - sys_total
        variances["tanks"].append({"tank_type": k[0], "grade": k[-1],
                                   **({"colour": k[1]} if len(k) == 3 else {}), "system": sys_total,
                                   "counted": cnt_total, "variance": var,
                                   "store_counted": counted_store.get(k, 0),
                                   "floor_counted": counted_tfloor.get(k, 0)})
        if abs(var) > tol_tank:
            what = f"{k[0]} {k[1] + ' ' if len(k) == 3 and k[1] else ''}grade {k[-1]}"
            await flag(f"{date}: {what} counted {cnt_total} (store + floor) vs system "
                       f"{sys_total} (variance {var:+d}). Counting only the store throws a false variance.",
                       {"count_id": count_id, "what": "tanks", "tank_type": k[0], "grade": k[-1],
                        **({"colour": k[1]} if len(k) == 3 else {})})

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
            "tank_floor": [{"tank_type": k[0], "grade": k[-1], **({"colour": k[1]} if len(k) == 3 else {}),
                            "quantity": v} for k, v in sorted(sys_tank_floor.items())],
            "fg_warehouse": [{"tank_type": k[0], "grade": k[-1], **({"colour": k[1]} if len(k) == 3 else {}),
                              "quantity": v} for k, v in sorted(sys_store.items())],
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
async def list_counts(node_id: str, include_voided: bool = False,
                      user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    return await _all(db.physical_counts(), _shown({"node_id": node_id}, include_voided),
                      sort=[("date", -1)])


@app.post("/api/counts/{count_id}/void")
async def void_count(count_id: str, payload: CorrectionIn,
                     user: dict = Depends(auth.require_role("admin"))):
    """Void a stocktake. Flags it raised stay open; audit resolves them against the void."""
    cnt = await db.physical_counts().find_one({"_id": count_id})
    if not cnt:
        raise HTTPException(404, "Count not found")
    auth.check_node_access(user, cnt["node_id"])
    return await corrections.void_record("physical_counts", count_id, user, payload.reason)


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
                          {"node_id": node_id, "date": {"$regex": f"^{month}"}, **ACTIVE})
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

    unmatched = await _all(db.payments(), {"node_id": node_id, "unallocated": {"$gt": 0.004}, **ACTIVE})
    unpaid = await _all(db.delivery_notes(), {"node_id": node_id, **ACTIVE,
                                              "status": {"$in": ["unpaid", "part_paid", "flagged"]}})
    open_invoices = await _all(db.invoices(), {"node_id": node_id, **ACTIVE,
                                               "status": {"$in": ["unpaid", "part_paid", "flagged"]}},
                               sort=[("due_date", 1)])
    return {
        "month": month,
        "days": days,
        "open_flags": open_flags,
        "unmatched_payments": unmatched,
        "unpaid_deliveries": [_dn_out(d) for d in unpaid],
        "open_invoices": open_invoices,
    }


# ============================== reporting ============================== #

@app.get("/api/nodes/{node_id}/reports/daily")
async def daily_report(node_id: str, date: str, user: dict = Depends(auth.current_user)):
    auth.check_node_access(user, node_id)
    cfg = await _get_cfg(node_id)
    consume = {t["code"]: t["weight_kg"] + t.get("lid_weight_kg", 0.0) for t in cfg["tank_types"]}
    day = {"node_id": node_id, "date": date, **ACTIVE}
    runs = await _all(db.production_runs(), day)
    powder = await _all(db.powder_ledger(), day)
    dns = await _all(db.delivery_notes(), day)
    pays = await _all(db.payments(), day)
    cap = await db.daily_captures().find_one(day)
    flags_open = await _all(db.flags(), {"node_id": node_id, "date_raised": date, "status": "open"})
    fg_store = await recon.fg_warehouse(node_id, date, cfg)
    return {
        "date": date,
        "tanks_produced": [{"tank_type": r["tank_type"], "a": r["quantity_a"],
                            "b": r["quantity_b"], "reject": r["quantity_reject"]} for r in runs],
        "powder_received_kg": sum(e["kg"] for e in powder if e["type"] == "received"),
        "powder_issued_kg": sum(e["kg"] for e in powder if e["type"] == "issued"),
        "implied_kg": sum((r["quantity_a"] + r["quantity_b"] + r["quantity_reject"])
                          * consume.get(r["tank_type"], 0) for r in runs),
        "finished_goods_on_hand": [{"tank_type": k[0], "grade": k[-1],
                                    **({"colour": k[1]} if len(k) == 3 else {}), "quantity": v}
                                   for k, v in sorted(fg_store.items())],
        "deliveries": [{"dn_number": d["dn_number"], "client_name": d["client_name"],
                        "lines": d["lines"], "total": d.get("total", 0),
                        "status": d.get("status", "unpaid")} for d in dns],
        "payments_received": [{"amount": p["amount"], "status": p.get("status", "")} for p in pays],
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
    filt = {"node_id": node_id, "date": {"$regex": f"^{month}"}, **ACTIVE}

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
        db.delivery_notes(), {"node_id": node_id, "status": {"$in": ["unpaid", "part_paid"]}, **ACTIVE}))

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
    async for r in db.production_runs().find(ACTIVE):
        c = consume_by_node.get(r["node_id"], {}).get(r["tank_type"], 0)
        total += (r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]) * c
    return {"total_kg": round(total, 1)}


# ============================== partner apps (Fenix side) ============================== #

@app.get("/api/nodes/{node_id}/partner-users")
async def list_partner_users(node_id: str, user: dict = Depends(auth.require_role("admin"))):
    return [partner_auth.public_user(u) async for u in db.partner_users().find({"node_id": node_id})]


@app.post("/api/nodes/{node_id}/partner-users")
async def create_partner_user(node_id: str, payload: PartnerUserIn,
                              user: dict = Depends(auth.require_role("admin"))):
    """Admin > Nodes: create the node's first partner admin (or a Fenix capturer on the node)."""
    await _get_node(node_id)
    email = payload.email.lower().strip()
    if await db.partner_users().find_one({"node_id": node_id, "email": email}):
        raise HTTPException(400, "That email already has a login on this node")
    if not payload.password or len(payload.password) < 8:
        raise HTTPException(400, "Password of at least 8 characters required")
    doc = {"_id": uuid4().hex, "email": email, "name": payload.name, "node_id": node_id,
           "password_hash": partner_auth.hash_password(payload.password), "role": payload.role,
           "employer": payload.employer, "active": True, "created_at": _now(), "created_by": user["email"]}
    await db.partner_users().insert_one(doc)
    await audit.log(user, node_id, "create", "partner_users", doc["_id"], after=doc)
    return partner_auth.public_user(doc)


@app.get("/api/nodes/{node_id}/commercial/tiles")
async def node_commercial_tiles(node_id: str, period: str = "month", start: Optional[str] = None,
                                end: Optional[str] = None, user: dict = Depends(auth.current_user)):
    """The partner dashboard tiles, read-only, for Fenix. Fenix sees every figure."""
    auth.check_node_access(user, node_id)
    return await board.tiles(node_id, board.period_range(period, start, end), True, True)


@app.get("/api/nodes/{node_id}/commercial/board/{kind}")
async def node_commercial_board(node_id: str, kind: str, filters: str = "{}", sort: str = "{}",
                                user: dict = Depends(auth.current_user)):
    import json
    auth.check_node_access(user, node_id)
    if kind not in board.ROWS:
        raise HTTPException(404, "Unknown board")
    rows = board.apply(kind, await board.ROWS[kind](node_id), json.loads(filters or "{}"), json.loads(sort or "{}"))
    return {"rows": board.public(rows), "totals": board.totals(kind, rows)}


@app.get("/api/nodes/{node_id}/customers/{customer_id}/statement.pdf")
async def internal_statement(node_id: str, customer_id: str, start: Optional[str] = None, end: Optional[str] = None,
                             user: dict = Depends(auth.current_user)):
    """The same partner-branded statement the node's app prints."""
    import partner_pdf
    import xero_mt
    auth.check_node_access(user, node_id)
    node = await _get_node(node_id)
    cfg = await _get_cfg(node_id)
    c = await commerce.get_customer(node_id, customer_id)
    end = end or commerce.today()
    st = await board.statement(node_id, customer_id, start or commerce.add_days(end, -90), end)
    pdf = partner_pdf.statement({**node, "_logo_png": await xero_mt.logo_png(node_id)}, cfg, c, st)
    return Response(pdf, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="Statement {c["name"]}.pdf"'})


@app.get("/api/nodes/{node_id}/commercial/{collection}")
async def node_commercial_records(node_id: str, collection: str, user: dict = Depends(auth.current_user)):
    """Quotes, orders, customers, cash, bills and debtors of a node, read-only."""
    auth.check_node_access(user, node_id)
    if collection == "cash":
        return {"on_hand": await commerce.cash_on_hand(node_id),
                "deposits": await _all(db.cash_deposits(), {"node_id": node_id}, sort=[("date", -1)])}
    if collection == "debtors":
        return await board.debtors(node_id)
    colls = {"quotes": db.quotes(), "orders": db.orders(), "customers": db.customers(),
             "bills": db.bills(), "invoices": db.invoices()}
    if collection not in colls:
        raise HTTPException(404, "Unknown collection")
    return await _all(colls[collection], {"node_id": node_id}, sort=[("date", -1)])


@app.get("/api/bill-documents/{doc_id}")
async def bill_document(doc_id: str, user: dict = Depends(auth.require_role("admin", "audit"))):
    """Fenix sees every node's bills, documents included, read-only."""
    import bills
    d = await db.bill_documents().find_one({"_id": doc_id}, {"node_id": 1})
    if not d:
        raise HTTPException(404, "Document not found")
    auth.check_node_access(user, d["node_id"])
    content, name, mime = await bills.load_document(doc_id)
    return Response(content, media_type=mime, headers={"Content-Disposition": f'inline; filename="{name}"'})


@app.get("/api/network/commercial")
async def network_commercial(user: dict = Depends(auth.require_role("admin", "audit"))):
    """Across nodes: invoiced, collected, outstanding, COD not collected, cash not banked,
    released unpaid, refund due, Fenix draw due. (Draws received are not recorded in the
    platform yet, so they are not shown.)"""
    out = []
    async for node in db.nodes().find({"status": "active"}):
        nid = node["node_id"]
        if not await db.node_config().find_one({"node_id": nid}):
            continue
        invoiced = collected = outstanding = 0.0
        async for i in db.invoices().find({"node_id": nid, **ACTIVE}):
            invoiced += i["total"]
            collected += i.get("amount_paid", 0.0)
            outstanding += max(0.0, i["total"] - i.get("amount_paid", 0.0))
        draw_due = 0.0
        async for p in db.payments().find({"node_id": nid, **ACTIVE}):
            draw_due += (p.get("split") or {}).get("fenix_exworks_value", 0.0) or 0.0
        cash = sum(h["amount"] for h in await commerce.cash_on_hand(nid))
        open_flags: dict = {}
        async for f in db.flags().find({"node_id": nid, "status": "open",
                                        "type": {"$in": ["cod_not_collected", "cash_not_banked", "released_unpaid",
                                                         "refund_due", "payment_unallocated", "dn_not_invoiced"]}}):
            open_flags[f["type"]] = open_flags.get(f["type"], 0) + 1
        refund = 0.0
        async for o in db.orders().find({"node_id": nid, "refund_due": {"$gt": 0}}):
            refund += o["refund_due"]
        cod = 0.0
        async for d in db.delivery_notes().find({"node_id": nid, **ACTIVE, "collection.method": "not_collected"}):
            cod += d.get("collect_amount") or 0
        out.append({"node_id": nid, "name": node["name"], "invoiced": round(invoiced, 2),
                    "collected": round(collected, 2), "outstanding": round(outstanding, 2),
                    "cod_not_collected": round(cod, 2), "cash_not_banked": round(cash, 2),
                    "refund_due": round(refund, 2), "fenix_draw_due": round(draw_due, 2),
                    "open_flags": open_flags,
                    "partner_app_live": bool(node.get("partner_app_live"))})
    return out


# ============================== audit ============================== #

@app.get("/api/audit")
async def audit_trail(node_id: Optional[str] = None, collection: Optional[str] = None,
                      action: Optional[str] = None, by: Optional[str] = None,
                      date_from: Optional[str] = None, date_to: Optional[str] = None,
                      limit: int = 200, user: dict = Depends(auth.require_role("admin"))):
    """Filter by node, collection, action, user and date range (YYYY-MM-DD, inclusive)."""
    filt: dict = {}
    for k, v in (("node_id", node_id), ("collection", collection), ("action", action), ("by", by)):
        if v:
            filt[k] = v
    try:
        at: dict = {}
        if date_from:
            at["$gte"] = datetime.fromisoformat(date_from)
        if date_to:
            at["$lt"] = datetime.fromisoformat(date_to) + timedelta(days=1)
    except ValueError:
        raise HTTPException(400, "Dates must be YYYY-MM-DD")
    if at:
        filt["at"] = at
    cursor = db.audit_log().find(filt).sort("at", -1).limit(min(limit, 1000))
    return [d async for d in cursor]
