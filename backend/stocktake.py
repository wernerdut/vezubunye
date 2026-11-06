"""Partner stocktakes with Fenix sign-off (Werner, 6 Nov 2026).

The node's own people (Steven at Makulu) count in the partner app: tanks per type, colour and
grade (store and floor), powder per product (warehouse and floor) and fittings. The count is
blind: the form never shows system figures. Nothing moves until Fenix audit or admin (Pierre)
signs it off in the internal app. Sign-off sets every position to what was counted, with one
count_adjustment ledger row per difference (struck against the system as it stands at sign-off,
so captures back-dated in the meantime are included), and records the count in physical_counts
so later corrections dated on or before it raise post_count_correction as any count does. A
count sent back changes nothing; Steven counts again.

The first signed-off count on a node is its opening stock.
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException

import audit
import commerce
import corrections
import db
import recon
from corrections import ACTIVE


def stocktakes():
    return db.db()["stocktakes"]


def _int(v, what: str) -> int:
    try:
        n = int(v or 0)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what} must be a whole number")
    if n < 0:
        raise HTTPException(400, f"{what} cannot be negative")
    return n


def _kg(v, what: str) -> float:
    try:
        n = round(float(v or 0), 2)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{what} must be a number")
    if n < 0:
        raise HTTPException(400, f"{what} cannot be negative")
    return n


def clean(cfg: dict, raw: dict) -> dict:
    """Validate a submitted count. Lines for the same position are added together."""
    date = raw.get("date") or commerce.today()
    if date > commerce.today():
        raise HTTPException(400, "A stocktake cannot be dated in the future")
    types = {t["code"] for t in cfg.get("tank_types", [])}
    colours = {c["code"] for c in cfg.get("colours", []) or []}
    powders = {p["code"] for p in cfg.get("powder_products", [])}
    fittings = {f["code"] for f in cfg.get("fitting_types", [])}
    tanks: dict = {}
    for l in raw.get("tanks") or []:
        t, g, c = l.get("tank_type"), l.get("grade") or "A", (l.get("colour") or "")
        if t not in types:
            raise HTTPException(400, f"Unknown tank type {t}")
        if g not in ("A", "B"):
            raise HTTPException(400, "Grade is A or B")
        if recon.track_colour(cfg):
            if c not in colours:
                raise HTTPException(400, f"Pick a colour for every {t} line")
        else:
            c = ""
        k = (t, c, g)
        e = tanks.setdefault(k, {"tank_type": t, "colour": c, "grade": g, "store": 0, "floor": 0})
        e["store"] += _int(l.get("store"), "Tanks in the store")
        e["floor"] += _int(l.get("floor"), "Tanks on the floor")
    powder: dict = {}
    for l in raw.get("powder") or []:
        p = l.get("powder_type")
        if p not in powders:
            raise HTTPException(400, f"Unknown powder {p}")
        e = powder.setdefault(p, {"powder_type": p, "warehouse_kg": 0.0, "floor_kg": 0.0})
        e["warehouse_kg"] = round(e["warehouse_kg"] + _kg(l.get("warehouse_kg"), "Powder in the store"), 2)
        e["floor_kg"] = round(e["floor_kg"] + _kg(l.get("floor_kg"), "Powder on the floor"), 2)
    fits: dict = {}
    for l in raw.get("fittings") or []:
        f = l.get("fitting_type")
        if f not in fittings:
            raise HTTPException(400, f"Unknown fitting {f}")
        e = fits.setdefault(f, {"fitting_type": f, "qty": 0})
        e["qty"] += _int(l.get("qty"), "Fittings")
    return {"date": date, "tanks": list(tanks.values()), "powder": list(powder.values()),
            "fittings": list(fits.values()), "notes": (raw.get("notes") or "").strip()}


def _labels(cfg: dict) -> dict:
    return {"tank": {t["code"]: t.get("name", t["code"]) for t in cfg.get("tank_types", [])},
            "colour": {c["code"]: c.get("name", c["code"]) for c in cfg.get("colours", []) or []},
            "powder": {**{p["code"]: (p.get("colour") or p["code"]) for p in cfg.get("powder_products", [])},
                       recon.BLACK_POOL: "Black (all black grades)"},
            "fitting": {f["code"]: f.get("name", f["code"]) for f in cfg.get("fitting_types", [])}}


async def variances(node_id: str, cfg: dict, st: dict) -> list[dict]:
    """Counted against the system on the count date, one row per position that is not zero on
    both sides. Anything not counted is counted as zero."""
    date = st["date"]
    lab = _labels(cfg)
    store = await recon.fg_warehouse(node_id, date, cfg)
    floor = await recon.tank_floor(node_id, date, cfg)
    c_store: dict = {}
    c_floor: dict = {}
    for l in st["tanks"]:
        k = recon.fg_key(cfg, l["tank_type"], l["grade"], l["colour"])
        c_store[k] = c_store.get(k, 0) + l["store"]
        c_floor[k] = c_floor.get(k, 0) + l["floor"]
    rows = []
    for k in sorted(set(store) | set(floor) | set(c_store) | set(c_floor)):
        colour = k[1] if len(k) == 3 else ""
        name = " ".join(x for x in (lab["tank"].get(k[0], k[0]), lab["colour"].get(colour, colour), k[-1]) if x)
        for kind, sysd, cnt in (("tank_store", store, c_store), ("tank_floor", floor, c_floor)):
            s, c = sysd.get(k, 0), cnt.get(k, 0)
            if s or c:
                rows.append({"kind": kind, "tank_type": k[0], "colour": colour, "grade": k[-1], "label": name,
                             "system": s, "counted": c, "variance": c - s})
    wh = await recon.powder_warehouse(node_id, date)
    pfloor = await recon.powder_floor(node_id, cfg, date)
    c_wh = {l["powder_type"]: l["warehouse_kg"] for l in st["powder"]}
    c_pf: dict = {}
    for l in st["powder"]:
        k = recon.floor_key(cfg, l["powder_type"])
        c_pf[k] = round(c_pf.get(k, 0.0) + l["floor_kg"], 2)
    for kind, sysd, cnt in (("powder_warehouse", wh, c_wh), ("powder_floor", pfloor, c_pf)):
        for p in sorted(set(sysd) | set(cnt)):
            s, c = round(sysd.get(p, 0.0), 2), round(cnt.get(p, 0.0), 2)
            if abs(s) > 0.004 or abs(c) > 0.004:
                rows.append({"kind": kind, "powder_type": p, "label": lab["powder"].get(p, p),
                             "system": s, "counted": c, "variance": round(c - s, 2)})
    fw = await recon.fittings_warehouse(node_id, date)
    c_fw = {l["fitting_type"]: l["qty"] for l in st["fittings"]}
    for f in sorted(set(fw) | set(c_fw)):
        s, c = fw.get(f, 0), c_fw.get(f, 0)
        if s or c:
            rows.append({"kind": "fittings", "fitting_type": f, "label": lab["fitting"].get(f, f),
                         "system": s, "counted": c, "variance": c - s})
    return rows


async def is_opening(node_id: str) -> bool:
    return not await db.physical_counts().find_one({"node_id": node_id, **ACTIVE})


async def submit(node_id: str, cfg: dict, raw: dict, user: dict) -> dict:
    if await stocktakes().find_one({"node_id": node_id, "status": "submitted"}):
        raise HTTPException(400, "A stocktake is already waiting for sign-off. Withdraw it to count again.")
    st = {"_id": uuid4().hex, "node_id": node_id, **clean(cfg, raw), "status": "submitted",
          "opening": await is_opening(node_id), "submitted_by": user["email"],
          "submitted_by_name": user.get("name") or user["email"], "submitted_at": datetime.utcnow(),
          "review": None, "adjustment_ids": [], "physical_count_id": None}
    if not (st["tanks"] or st["powder"] or st["fittings"]):
        raise HTTPException(400, "Count at least one line")
    await stocktakes().insert_one(st)
    await audit.log(user, node_id, "create", "stocktakes", st["_id"], after=st)
    return st


async def withdraw(node_id: str, st_id: str, user: dict) -> dict:
    st = await stocktakes().find_one({"_id": st_id, "node_id": node_id})
    if not st:
        raise HTTPException(404, "Stocktake not found")
    if st["status"] != "submitted":
        raise HTTPException(400, f"This stocktake is {st['status']}")
    await stocktakes().update_one({"_id": st_id}, {"$set": {"status": "withdrawn", "withdrawn_at": datetime.utcnow()}})
    await audit.log(user, node_id, "withdraw", "stocktakes", st_id)
    return {**st, "status": "withdrawn"}


async def send_back(st_id: str, note: str, user: dict) -> dict:
    st = await stocktakes().find_one({"_id": st_id})
    if not st:
        raise HTTPException(404, "Stocktake not found")
    if st["status"] != "submitted":
        raise HTTPException(400, f"This stocktake is {st['status']}")
    if not (note or "").strip():
        raise HTTPException(400, "Say what to recount")
    review = {"by": user["email"], "at": datetime.utcnow(), "note": note.strip(), "decision": "returned"}
    await stocktakes().update_one({"_id": st_id}, {"$set": {"status": "returned", "review": review}})
    await audit.log(user, st["node_id"], "return", "stocktakes", st_id, after=review)
    return {**st, "status": "returned", "review": review}


async def approve(st_id: str, note: str, user: dict) -> dict:
    st = await stocktakes().find_one({"_id": st_id})
    if not st:
        raise HTTPException(404, "Stocktake not found")
    if st["status"] != "submitted":
        raise HTTPException(400, f"This stocktake is {st['status']}")
    node_id = st["node_id"]
    cfg = await recon.get_config(node_id)
    rows = await variances(node_id, cfg, st)
    blacks = sorted(recon.black_codes(cfg))
    who = st.get("submitted_by_name") or st["submitted_by"]
    note_txt = f"{'Opening stock' if st['opening'] else 'Stocktake'} {st['date']} counted by {who}, signed off by {user['email']}"
    now = datetime.utcnow()
    ids = []
    async with corrections.txn() as s:
        for r in rows:
            v = r["variance"]
            if (abs(v) < 0.005) if isinstance(v, float) else v == 0:
                continue
            base = {"_id": uuid4().hex, "node_id": node_id, "date": st["date"], "type": "count_adjustment",
                    "stocktake_id": st_id, "notes": note_txt, "created_at": now}
            if r["kind"] in ("tank_store", "tank_floor"):
                d = {**base, "tank_type": r["tank_type"], "colour": r["colour"], "grade": r["grade"],
                     "scope": "fg_warehouse" if r["kind"] == "tank_store" else "tank_floor",
                     "quantity": v, "reference": None}
                await db.finished_goods().insert_one(d, session=s)
                coll = "finished_goods_ledger"
            elif r["kind"] in ("powder_warehouse", "powder_floor"):
                pt = r["powder_type"]
                if pt == recon.BLACK_POOL:
                    pt = blacks[0]          # pooled: any black grade carries the floor adjustment
                d = {**base, "powder_type": pt, "scope": "warehouse" if r["kind"] == "powder_warehouse" else "floor",
                     "kg": v, "source_capture_id": None}
                await db.powder_ledger().insert_one(d, session=s)
                coll = "powder_ledger"
            else:
                d = {**base, "fitting_type": r["fitting_type"], "quantity": v, "source_capture_id": None}
                await db.fittings_ledger().insert_one(d, session=s)
                coll = "fittings_ledger"
            ids.append(d["_id"])
            await audit.log(user, node_id, "create", coll, d["_id"], after=d, session=s)
        pc = _physical_count(st, rows, user, now)
        await db.physical_counts().insert_one(pc, session=s)
        review = {"by": user["email"], "at": now, "note": (note or "").strip(), "decision": "approved"}
        await stocktakes().update_one({"_id": st_id}, {"$set": {
            "status": "approved", "review": review, "approved_variances": rows, "adjustment_ids": ids,
            "physical_count_id": pc["_id"]}}, session=s)
        await audit.log(user, node_id, "approve", "stocktakes", st_id, after=review, session=s)
    return {**st, "status": "approved", "review": review, "approved_variances": rows, "adjustment_ids": ids,
            "physical_count_id": pc["_id"]}


def _physical_count(st: dict, rows: list[dict], user: dict, now: datetime) -> dict:
    """The signed-off count in the internal Counts shape (store and floor summed per tank)."""
    tanks: dict = {}
    for r in rows:
        if r["kind"] in ("tank_store", "tank_floor"):
            k = (r["tank_type"], r["colour"], r["grade"])
            e = tanks.setdefault(k, {"tank_type": r["tank_type"], "grade": r["grade"],
                                     **({"colour": r["colour"]} if r["colour"] else {}),
                                     "system": 0, "counted": 0, "variance": 0, "store_counted": 0, "floor_counted": 0})
            e["system"] += r["system"]
            e["counted"] += r["counted"]
            e["variance"] += r["variance"]
            e["store_counted" if r["kind"] == "tank_store" else "floor_counted"] += r["counted"]
    pick = lambda kind: [{"powder_type": r["powder_type"], "system": r["system"], "counted": r["counted"],
                          "variance": r["variance"]} for r in rows if r["kind"] == kind]
    return {"_id": uuid4().hex, "node_id": st["node_id"], "date": st["date"],
            "powder_counted": [{"powder_type": p["powder_type"], "warehouse_kg": p["warehouse_kg"],
                                "floor_kg": p["floor_kg"]} for p in st["powder"]],
            "fg_warehouse_counted": [{"tank_type": t["tank_type"], "colour": t["colour"], "grade": t["grade"],
                                      "quantity": t["store"]} for t in st["tanks"]],
            "tank_floor_counted": [{"tank_type": t["tank_type"], "colour": t["colour"], "grade": t["grade"],
                                    "quantity": t["floor"]} for t in st["tanks"]],
            "fittings_counted": [{"fitting_type": f["fitting_type"], "warehouse_qty": f["qty"]} for f in st["fittings"]],
            "variances": {"powder_warehouse": pick("powder_warehouse"), "powder_floor": pick("powder_floor"),
                          "tanks": list(tanks.values()),
                          "fittings": [{"fitting_type": r["fitting_type"], "system": r["system"], "counted": r["counted"],
                                        "variance": r["variance"]} for r in rows if r["kind"] == "fittings"]},
            "stocktake_id": st["_id"], "opening": st["opening"], "adjusted_to_count": True,
            "counted_by": st["submitted_by"], "approved_by": user["email"], "created_at": now}


async def positions(node_id: str, cfg: dict) -> dict:
    """What the count form lists: tank lines with stock today (names only, never quantities:
    the count is blind), every powder product and every fitting."""
    store = await recon.fg_warehouse(node_id, None, cfg)
    floor = await recon.tank_floor(node_id, None, cfg)
    keys = sorted(k for k in set(store) | set(floor) if store.get(k, 0) or floor.get(k, 0))
    return {"tanks": [{"tank_type": k[0], "colour": k[1] if len(k) == 3 else "", "grade": k[-1]} for k in keys],
            "powder": [p["code"] for p in cfg.get("powder_products", [])],
            "fittings": [f["code"] for f in cfg.get("fitting_types", [])],
            "track_colour": recon.track_colour(cfg), "opening": await is_opening(node_id)}
