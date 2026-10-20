"""Reconciliation rules. Nothing auto-clears; the audit role resolves every flag with a note.

Three stock positions per node, all derived (never stored):
  - Powder warehouse (per type): received - issued
  - Production floor: powder (black pool, colour pool) and tanks (moulded - booked)
  - Finished-goods warehouse (per type/grade): booked - dispatched

Recipe: each moulded tank (body weight W, lid weight L) consumes W/2 colour + (W/2 + L) black.

Rules:
  R1  powder floor pools (black, colour) must never go negative -> powder_variance.
  R1b tank floor (booked <= moulded) and FG store (dispatched <= booked) -> finished_goods_mismatch.
  Rf  fittings issued vs tanks produced x fittings_per_tank -> fittings_variance.
  R3  per order line, delivered must not exceed invoiced by the end of the next SAST day.
  R4  every invoice is paid by its due date; unpaid past due / short / over flagged.
  R7  physical counts reconcile store + floor to system; variance flags.

Every derivation reads only ACTIVE rows: a voided record never counts.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import corrections
import db

EPS = 0.001  # float comparison guard


async def raise_flag(node_id: str, flag_type: str, description: str,
                     references: dict | None = None, date: str | None = None):
    """Raise a flag unless an identical open flag already exists."""
    existing = await db.flags().find_one({
        "node_id": node_id, "type": flag_type, "status": "open",
        "references": references or {},
    })
    if existing:
        return existing["_id"]
    fid = uuid4().hex
    await db.flags().insert_one({
        "_id": fid,
        "node_id": node_id,
        "date_raised": date or datetime.utcnow().strftime("%Y-%m-%d"),
        "type": flag_type,
        "references": references or {},
        "description": description,
        "status": "open",
        "resolved_by": None,
        "resolution_note": None,
        "created_at": datetime.utcnow(),
    })
    return fid


async def get_config(node_id: str) -> dict:
    cfg = await db.node_config().find_one({"node_id": node_id})
    if not cfg:
        raise ValueError(f"No config for node {node_id}")
    return cfg


# ---------- recipe helpers ---------- #

def tank_map(cfg: dict) -> dict:
    """code -> {weight_kg, lid_weight_kg}."""
    return {t["code"]: {"weight": t.get("weight_kg", 0.0),
                        "lid": t.get("lid_weight_kg", 0.0)} for t in cfg["tank_types"]}


def black_codes(cfg: dict) -> set[str]:
    return {p["code"] for p in cfg.get("powder_products", []) if p.get("is_black")}


BLACK_POOL = "BLACK_POOL"


def black_pool_key(cfg: dict) -> str | None:
    """Black is a pool, not one product. Every is_black product feeds one floor balance for R1.
    A node with a single black product keeps that product's code as the key, so its numbers
    and flags read exactly as before; several black grades pool under BLACK_POOL."""
    blacks = sorted(black_codes(cfg))
    if not blacks:
        return None
    return blacks[0] if len(blacks) == 1 else BLACK_POOL


def floor_key(cfg: dict, powder_type: str) -> str:
    """Floor balance key for a powder grade: black grades map to the pool."""
    if powder_type in black_codes(cfg):
        return black_pool_key(cfg) or powder_type
    return powder_type


# ---------- colour as a stock dimension ---------- #

def track_colour(cfg: dict) -> bool:
    return bool(cfg.get("track_colour_stock"))


def run_colour(cfg: dict, run: dict) -> str:
    """The tank colour a production run made. New captures store it; older runs carry only the
    outer powder code, mapped back through the node's colours."""
    if run.get("tank_colour"):
        return run["tank_colour"]
    pc = run.get("colour") or ""
    for c in cfg.get("colours", []) or []:
        if c.get("powder_product") and c["powder_product"] == pc:
            return c["code"]
    return ""


def fg_key(cfg: dict, tank_type: str, grade: str, colour: str | None = "") -> tuple:
    """(type, grade) as Brief 1, or (type, colour, grade) when the node counts stock by colour.
    Rows written before colour existed sit in the unspecified-colour bucket ""."""
    if track_colour(cfg):
        return (tank_type, colour or "", grade)
    return (tank_type, grade)


async def total_moulded(node_id: str, up_to_date: str | None = None) -> dict:
    """code -> {a, b, reject, total} moulded (all grades, all colours)."""
    filt: dict = {"node_id": node_id, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    out: dict = {}
    async for r in db.production_runs().find(filt):
        t = out.setdefault(r["tank_type"], {"a": 0, "b": 0, "reject": 0, "total": 0})
        t["a"] += r["quantity_a"]; t["b"] += r["quantity_b"]; t["reject"] += r["quantity_reject"]
        t["total"] += r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]
    return out


# ---------- powder positions ---------- #

async def powder_warehouse(node_id: str, up_to_date: str | None = None) -> dict:
    """powder_type -> warehouse kg (received - issued +/- warehouse adjustments)."""
    filt: dict = {"node_id": node_id, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    bal: dict = {}
    async for e in db.powder_ledger().find(filt):
        pt = e.get("powder_type", "?")
        if e["type"] == "received":
            bal[pt] = bal.get(pt, 0.0) + e["kg"]
        elif e["type"] == "issued":
            bal[pt] = bal.get(pt, 0.0) - e["kg"]
        elif e["type"] == "count_adjustment" and e.get("scope", "warehouse") == "warehouse":
            bal[pt] = bal.get(pt, 0.0) + e["kg"]
    return {k: round(v, 2) for k, v in bal.items()}


async def powder_consumed(node_id: str, cfg: dict, up_to_date: str | None = None) -> dict:
    """powder_type -> kg consumed by moulding. Each tank (body W, lid L) of colour C draws
    W/2 of C plus W/2 + L of black. Black is drawn by every tank regardless of colour."""
    tmap = tank_map(cfg)
    black_code = black_pool_key(cfg)
    consumed: dict = {}
    filt: dict = {"node_id": node_id, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    async for r in db.production_runs().find(filt):
        t = tmap.get(r["tank_type"])
        if not t:
            continue
        n = r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]
        if black_code:
            consumed[black_code] = consumed.get(black_code, 0.0) + n * (t["weight"] / 2 + t["lid"])
        colour = floor_key(cfg, r.get("colour") or "")
        if colour:
            consumed[colour] = consumed.get(colour, 0.0) + n * (t["weight"] / 2)
    return consumed


async def powder_floor(node_id: str, cfg: dict, up_to_date: str | None = None) -> dict:
    """powder_type -> floor kg = issued - consumed +/- floor adjustments. One balance per colour
    grade, because each colour is a distinct material that is issued and drawn separately.
    Black grades share one pool (black_pool_key): any black grade issued is drawn down by the
    inner layers, lids and black outer layers of every tank moulded."""
    issued: dict = {}
    adj: dict = {}
    filt: dict = {"node_id": node_id, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    async for e in db.powder_ledger().find(filt):
        pt = floor_key(cfg, e.get("powder_type", "?"))
        if e["type"] == "issued":
            issued[pt] = issued.get(pt, 0.0) + e["kg"]
        elif e["type"] == "count_adjustment" and e.get("scope") == "floor":
            adj[pt] = adj.get(pt, 0.0) + e["kg"]
    consumed = await powder_consumed(node_id, cfg, up_to_date)
    types = set(list(issued.keys()) + list(adj.keys()) + list(consumed.keys()))
    return {pt: round(issued.get(pt, 0.0) + adj.get(pt, 0.0) - consumed.get(pt, 0.0), 2) for pt in types}


# ---------- Rule 1: powder floor identity ---------- #

async def check_powder_floor(node_id: str, date: str, capture_id: str) -> list[str]:
    """Each powder grade's floor must stay >= 0: you cannot mould more of a colour (or black)
    than was issued. A negative floor means that material left the store without becoming a tank."""
    cfg = await get_config(node_id)
    floor = await powder_floor(node_id, cfg, date)
    names = {p["code"]: (p.get("colour") or p["code"]) for p in cfg.get("powder_products", [])}
    names[BLACK_POOL] = "Black (all black grades)"
    raised = []
    for pt, kg in floor.items():
        if kg < -EPS:
            raised.append(await raise_flag(
                node_id, "powder_variance",
                f"{date}: {names.get(pt, pt)} powder on the production floor is {kg:.1f} kg — "
                f"more moulded than issued. Every kg consumed must have been issued from the store.",
                {"capture_id": capture_id, "powder_type": pt, "date": date}, date))
    return raised


# ---------- paraffin (release agent) ---------- #

async def paraffin_balance(node_id: str, cfg: dict, up_to_date: str | None = None) -> dict:
    """Single paraffin stock: received (+ adjustments) less consumed by moulding.
    Each tank moulded (A, B or reject) draws `paraffin_litres_per_tank` litres."""
    rate = float(cfg.get("paraffin_litres_per_tank", 0.0) or 0.0)
    filt: dict = {"node_id": node_id, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    received = 0.0
    async for e in db.paraffin_ledger().find(filt):
        if e.get("type") in ("received", "count_adjustment"):
            received += e.get("litres", 0.0)
    tanks = 0
    async for r in db.production_runs().find(filt):
        tanks += r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]
    consumed = tanks * rate
    return {"litres_per_tank": rate, "received": round(received, 1),
            "consumed": round(consumed, 1), "balance": round(received - consumed, 1),
            "tanks": tanks}


# ---------- fittings ---------- #

async def fittings_warehouse(node_id: str, up_to_date: str | None = None) -> dict:
    filt: dict = {"node_id": node_id, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    bal: dict = {}
    async for e in db.fittings_ledger().find(filt):
        ft = e.get("fitting_type", "?")
        if e["type"] == "received":
            bal[ft] = bal.get(ft, 0) + e["quantity"]
        elif e["type"] == "issued":
            bal[ft] = bal.get(ft, 0) - e["quantity"]
        else:  # count_adjustment carries sign
            bal[ft] = bal.get(ft, 0) + e["quantity"]
    return {k: round(v, 2) for k, v in bal.items()}


async def fittings_issued(node_id: str, up_to_date: str | None = None) -> dict:
    filt: dict = {"node_id": node_id, "type": "issued", **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    out: dict = {}
    async for e in db.fittings_ledger().find(filt):
        out[e["fitting_type"]] = out.get(e["fitting_type"], 0) + e["quantity"]
    return out


async def fittings_expected(node_id: str, cfg: dict, up_to_date: str | None = None) -> dict:
    """fitting_code -> expected issued = sum over tank types of produced(total) x fittings_per_tank."""
    fpt = cfg.get("fittings_per_tank", {}) or {}
    moulded = await total_moulded(node_id, up_to_date)
    out: dict = {}
    for tank_code, agg in moulded.items():
        per = fpt.get(tank_code, {}) or {}
        for fcode, qty in per.items():
            out[fcode] = out.get(fcode, 0) + agg["total"] * qty
    return out


async def check_fittings(node_id: str, date: str, capture_id: str) -> list[str]:
    """Fittings issued should equal tanks produced x fittings-per-tank, within tolerance."""
    cfg = await get_config(node_id)
    tol = (cfg.get("tolerances") or {}).get("fittings_qty", 0)
    issued = await fittings_issued(node_id, date)
    expected = await fittings_expected(node_id, cfg, date)
    names = {f["code"]: f.get("name", f["code"]) for f in cfg.get("fitting_types", [])}
    raised = []
    for fcode in set(list(issued.keys()) + list(expected.keys())):
        gap = issued.get(fcode, 0) - expected.get(fcode, 0)
        if abs(gap) > tol:
            raised.append(await raise_flag(
                node_id, "fittings_variance",
                f"{date}: fitting {names.get(fcode, fcode)} issued {issued.get(fcode, 0)} vs "
                f"expected {expected.get(fcode, 0)} for tanks produced (gap {gap:+d}, tolerance {tol}).",
                {"capture_id": capture_id, "fitting_type": fcode, "date": date}, date))
    return raised


# ---------- tank floor & finished-goods warehouse ---------- #

async def _cfg_or_empty(node_id: str, cfg: dict | None) -> dict:
    if cfg is not None:
        return cfg
    return await db.node_config().find_one({"node_id": node_id}) or {}


async def _fg_ledger_sum(node_id: str, move_type: str, up_to_date: str | None = None,
                         cfg: dict | None = None) -> dict:
    cfg = await _cfg_or_empty(node_id, cfg)
    filt: dict = {"node_id": node_id, "type": move_type, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    out: dict = {}
    async for e in db.finished_goods().find(filt):
        key = fg_key(cfg, e["tank_type"], e["grade"], e.get("colour"))
        out[key] = out.get(key, 0) + e["quantity"]
    return out


async def tank_floor(node_id: str, up_to_date: str | None = None, cfg: dict | None = None) -> dict:
    """(type, grade) -> moulded - booked (tanks on the floor not yet in the store).
    Keyed (type, colour, grade) when the node tracks colour stock."""
    cfg = await _cfg_or_empty(node_id, cfg)
    booked = await _fg_ledger_sum(node_id, "booked", up_to_date, cfg)
    adj = await _fg_adjustments(node_id, "tank_floor", up_to_date, cfg)
    out: dict = {}
    filt: dict = {"node_id": node_id, **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    async for r in db.production_runs().find(filt):
        col = run_colour(cfg, r)
        for grade, q in (("A", r["quantity_a"]), ("B", r["quantity_b"])):
            k = fg_key(cfg, r["tank_type"], grade, col)
            out[k] = out.get(k, 0) + q
    for key, q in booked.items():
        out[key] = out.get(key, 0) - q
    for key, q in adj.items():
        out[key] = out.get(key, 0) + q
    return out


async def fg_warehouse(node_id: str, up_to_date: str | None = None, cfg: dict | None = None) -> dict:
    """(type, grade) -> booked - dispatched (the finished-goods store).
    Keyed (type, colour, grade) when the node tracks colour stock."""
    cfg = await _cfg_or_empty(node_id, cfg)
    booked = await _fg_ledger_sum(node_id, "booked", up_to_date, cfg)
    dispatched = await _fg_ledger_sum(node_id, "dispatched", up_to_date, cfg)
    adj = await _fg_adjustments(node_id, "fg_warehouse", up_to_date, cfg)
    out: dict = dict(booked)
    for key, q in dispatched.items():
        out[key] = out.get(key, 0) - q
    for key, q in adj.items():
        out[key] = out.get(key, 0) + q
    return {k: v for k, v in out.items()}


async def _fg_adjustments(node_id: str, scope: str, up_to_date: str | None = None,
                          cfg: dict | None = None) -> dict:
    cfg = await _cfg_or_empty(node_id, cfg)
    filt: dict = {"node_id": node_id, "type": "count_adjustment", "scope": scope,
                  **corrections.ACTIVE}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    out: dict = {}
    async for e in db.finished_goods().find(filt):
        key = fg_key(cfg, e["tank_type"], e["grade"], e.get("colour"))
        out[key] = out.get(key, 0) + e["quantity"]
    return out


async def check_finished_goods(node_id: str, date: str, reference: dict | None = None) -> list[str]:
    """Negative tank-floor (booked > moulded) or negative store (dispatched > booked) breaks identity."""
    raised = []
    for label, pos in (("on the floor", await tank_floor(node_id, date)),
                       ("in the store", await fg_warehouse(node_id, date))):
        for key, qty in pos.items():
            tank_type, grade = key[0], key[-1]
            colour = key[1] if len(key) == 3 else None
            if qty < 0:
                what = f"{tank_type} {colour + ' ' if colour else ''}grade {grade}"
                ref = {**(reference or {}), "tank_type": tank_type, "grade": grade, "where": label}
                if colour is not None:
                    ref["colour"] = colour
                raised.append(await raise_flag(
                    node_id, "finished_goods_mismatch",
                    f"{date}: {what} {label} is {qty} — "
                    f"more moved out than in. Per-type per-grade ledger broken.", ref, date))
    return raised


# ---------- sweeps (R3, R4 and the Brief 2 controls) ---------- #

async def run_sweeps(node_id: str) -> dict:
    """R3 (delivered not invoiced), R4 (invoices unpaid past due), unallocated payments, cash
    not banked, quote expiry. The commercial records live in commerce; imported here lazily
    because commerce builds on this module."""
    import commerce
    return {
        "invoice_unpaid": await commerce.sweep_invoices_unpaid(node_id),
        "dn_not_invoiced": await commerce.sweep_dn_not_invoiced(node_id),
        "payment_unallocated": await commerce.sweep_unallocated(node_id),
        "cash_not_banked": await commerce.sweep_cash(node_id),
        "quotes_expired": await commerce.sweep_quotes(node_id),
    }


# ---------- Rule 5: payment split (unchanged) ---------- #

def compute_split(invoice: dict, cfg: dict, amount: float) -> dict:
    """Split a (VAT-inclusive) payment between Fenix and the partner.

    Fenix draws its ex-works value PLUS VAT (Fenix invoices the node ex-works + VAT):
    full ex-works for A lines, b_grade_exworks_pct for B lines, then grossed up by the
    VAT rate. The partner keeps the remainder of the payment. Both shares are
    VAT-inclusive and add back to `amount`.
    """
    prices = {t["code"]: t["ex_works_price"] for t in cfg["tank_types"]}
    b_pct = cfg.get("b_grade_exworks_pct", 100.0) / 100.0
    vat_factor = 1.0 + cfg.get("vat_rate", 15.0) / 100.0
    fenix = 0.0
    for line in invoice["lines"]:
        if line.get("kind") == "charge":     # charge lines (delivery etc.) never draw to Fenix
            continue
        ex = prices.get(line["tank_type"], 0.0)
        factor = 1.0 if line.get("grade", "A") == "A" else b_pct
        fenix += line.get("quantity", line.get("qty", 0)) * ex * factor
    fenix_incl_vat = fenix * vat_factor
    return {
        "fenix_exworks_value": round(fenix_incl_vat, 2),   # ex-works + VAT (what Fenix draws)
        "partner_balance": round(amount - fenix_incl_vat, 2),
    }
