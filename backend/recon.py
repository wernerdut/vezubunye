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
  R3  every delivery note must link to an invoice.
  R4  every invoice must match a payment; unpaid past terms / short / over flagged.
  R7  physical counts reconcile store + floor to system; variance flags.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

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


def tank_consumption(t: dict, qty: int) -> tuple[float, float]:
    """Return (black_kg, colour_kg) consumed by `qty` moulded tanks of this type.
    Body W splits 50/50; lid L is black."""
    w, l = t["weight"], t["lid"]
    black = qty * (w / 2 + l)
    colour = qty * (w / 2)
    return black, colour


async def total_moulded(node_id: str, up_to_date: str | None = None) -> dict:
    """code -> {a, b, reject, total} moulded (all grades)."""
    filt: dict = {"node_id": node_id}
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
    filt: dict = {"node_id": node_id}
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


async def powder_floor(node_id: str, cfg: dict, up_to_date: str | None = None) -> dict:
    """Two derived pools: {'black': kg, 'colour': kg}.
    issued(black) - consumed_black ; issued(non-black) - consumed_colour ; +/- floor adjustments."""
    blacks = black_codes(cfg)
    tmap = tank_map(cfg)
    issued_black = issued_colour = 0.0
    adj_black = adj_colour = 0.0
    filt: dict = {"node_id": node_id}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    async for e in db.powder_ledger().find(filt):
        pt = e.get("powder_type", "?")
        is_black = pt in blacks
        if e["type"] == "issued":
            if is_black:
                issued_black += e["kg"]
            else:
                issued_colour += e["kg"]
        elif e["type"] == "count_adjustment" and e.get("scope") == "floor":
            if is_black:
                adj_black += e["kg"]
            else:
                adj_colour += e["kg"]

    consumed_black = consumed_colour = 0.0
    for code, agg in (await total_moulded(node_id, up_to_date)).items():
        t = tmap.get(code)
        if not t:
            continue
        b, c = tank_consumption(t, agg["total"])
        consumed_black += b
        consumed_colour += c

    return {
        "black": round(issued_black + adj_black - consumed_black, 2),
        "colour": round(issued_colour + adj_colour - consumed_colour, 2),
    }


async def total_powder_on_site(node_id: str, cfg: dict, up_to_date: str | None = None) -> dict:
    """Per powder type: received - issued + issued - consumed = received - consumed, but consumption
    is by pool (black/colour), not by individual colour. Returns per-type warehouse plus pool floors
    so the count screen can sum store + floor per pool."""
    wh = await powder_warehouse(node_id, up_to_date)
    floor = await powder_floor(node_id, cfg, up_to_date)
    return {"warehouse": wh, "floor": floor}


# ---------- Rule 1: powder floor identity ---------- #

async def check_powder_floor(node_id: str, date: str, capture_id: str) -> list[str]:
    """Each floor pool must stay >= 0: you cannot mould more than was issued (plus what was on the
    floor). A negative pool means powder left the building without becoming a tank."""
    cfg = await get_config(node_id)
    floor = await powder_floor(node_id, cfg, date)
    raised = []
    for pool in ("black", "colour"):
        if floor[pool] < -EPS:
            raised.append(await raise_flag(
                node_id, "powder_variance",
                f"{date}: {pool} powder on the production floor is {floor[pool]:.1f} kg — "
                f"more moulded than issued. Every kg consumed must have been issued from the store.",
                {"capture_id": capture_id, "pool": pool, "date": date}, date))
    return raised


# ---------- fittings ---------- #

async def fittings_warehouse(node_id: str, up_to_date: str | None = None) -> dict:
    filt: dict = {"node_id": node_id}
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
    filt: dict = {"node_id": node_id, "type": "issued"}
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

async def _fg_ledger_sum(node_id: str, move_type: str, up_to_date: str | None = None) -> dict:
    filt: dict = {"node_id": node_id, "type": move_type}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    out: dict = {}
    async for e in db.finished_goods().find(filt):
        key = (e["tank_type"], e["grade"])
        out[key] = out.get(key, 0) + e["quantity"]
    return out


async def tank_floor(node_id: str, up_to_date: str | None = None) -> dict:
    """(type, grade) -> moulded - booked (tanks on the floor not yet in the store)."""
    moulded = await total_moulded(node_id, up_to_date)
    booked = await _fg_ledger_sum(node_id, "booked", up_to_date)
    adj = {k: v for k, v in (await _fg_adjustments(node_id, "tank_floor", up_to_date)).items()}
    out: dict = {}
    for code, agg in moulded.items():
        out[(code, "A")] = out.get((code, "A"), 0) + agg["a"]
        out[(code, "B")] = out.get((code, "B"), 0) + agg["b"]
    for key, q in booked.items():
        out[key] = out.get(key, 0) - q
    for key, q in adj.items():
        out[key] = out.get(key, 0) + q
    return out


async def fg_warehouse(node_id: str, up_to_date: str | None = None) -> dict:
    """(type, grade) -> booked - dispatched (the finished-goods store)."""
    booked = await _fg_ledger_sum(node_id, "booked", up_to_date)
    dispatched = await _fg_ledger_sum(node_id, "dispatched", up_to_date)
    adj = await _fg_adjustments(node_id, "fg_warehouse", up_to_date)
    out: dict = dict(booked)
    for key, q in dispatched.items():
        out[key] = out.get(key, 0) - q
    for key, q in adj.items():
        out[key] = out.get(key, 0) + q
    return {k: v for k, v in out.items()}


async def _fg_adjustments(node_id: str, scope: str, up_to_date: str | None = None) -> dict:
    filt: dict = {"node_id": node_id, "type": "count_adjustment", "scope": scope}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    out: dict = {}
    async for e in db.finished_goods().find(filt):
        key = (e["tank_type"], e["grade"])
        out[key] = out.get(key, 0) + e["quantity"]
    return out


async def check_finished_goods(node_id: str, date: str, reference: dict | None = None) -> list[str]:
    """Negative tank-floor (booked > moulded) or negative store (dispatched > booked) breaks identity."""
    raised = []
    for label, pos in (("on the floor", await tank_floor(node_id, date)),
                       ("in the store", await fg_warehouse(node_id, date))):
        for (tank_type, grade), qty in pos.items():
            if qty < 0:
                raised.append(await raise_flag(
                    node_id, "finished_goods_mismatch",
                    f"{date}: {tank_type} grade {grade} {label} is {qty} — "
                    f"more moved out than in. Per-type per-grade ledger broken.",
                    {**(reference or {}), "tank_type": tank_type, "grade": grade, "where": label}, date))
    return raised


# ---------- Rule 3 & 4 sweeps (unchanged) ---------- #

async def sweep_delivery_notes(node_id: str) -> list[str]:
    raised = []
    async for dn in db.delivery_notes().find({"node_id": node_id, "linked_invoice_id": None}):
        fid = await raise_flag(
            node_id, "delivery_without_invoice",
            f"Delivery note {dn['dn_number']} ({dn['date']}, {dn['client_name']}) has no linked invoice. "
            "No tank leaves without both.",
            {"delivery_note_id": dn["_id"]}, dn["date"])
        raised.append(fid)
    return raised


async def sweep_unpaid_invoices(node_id: str) -> list[str]:
    cfg = await get_config(node_id)
    terms = cfg.get("payment_terms_days", 30)
    cutoff = (datetime.utcnow() - timedelta(days=terms)).strftime("%Y-%m-%d")
    raised = []
    async for inv in db.invoices().find({"node_id": node_id,
                                         "status": {"$in": ["unpaid", "part_paid"]},
                                         "date": {"$lt": cutoff}}):
        fid = await raise_flag(
            node_id, "invoice_unpaid",
            f"Invoice {inv['invoice_number']} ({inv['date']}, {inv['client_name']}, "
            f"R{inv['total']:.2f}) is {inv['status']} past {terms}-day terms.",
            {"invoice_id": inv["_id"]}, inv["date"])
        raised.append(fid)
    return raised


async def sweep_unmatched_payments(node_id: str) -> list[str]:
    raised = []
    async for p in db.payments().find({"node_id": node_id, "status": "unmatched"}):
        fid = await raise_flag(
            node_id, "payment_unmatched",
            f"Payment of R{p['amount']:.2f} on {p['date']} (ref '{p['bank_reference']}') "
            "is not matched to an invoice.",
            {"payment_id": p["_id"]}, p["date"])
        raised.append(fid)
    return raised


async def run_sweeps(node_id: str) -> dict:
    return {
        "delivery_without_invoice": await sweep_delivery_notes(node_id),
        "invoice_unpaid": await sweep_unpaid_invoices(node_id),
        "payment_unmatched": await sweep_unmatched_payments(node_id),
    }


# ---------- Rule 5: payment split (unchanged) ---------- #

def compute_split(invoice: dict, cfg: dict, amount: float) -> dict:
    """Fenix draws ex-works value: full for A lines, b_grade_exworks_pct for B lines."""
    prices = {t["code"]: t["ex_works_price"] for t in cfg["tank_types"]}
    b_pct = cfg.get("b_grade_exworks_pct", 100.0) / 100.0
    fenix = 0.0
    for line in invoice["lines"]:
        ex = prices.get(line["tank_type"], 0.0)
        factor = 1.0 if line["grade"] == "A" else b_pct
        fenix += line["quantity"] * ex * factor
    return {
        "fenix_exworks_value": round(fenix, 2),
        "partner_balance": round(amount - fenix, 2),
    }
