"""Reconciliation rules. Zero tolerance, nothing auto-clears.

Rule 1: powder drawn (kg) == tanks moulded (A+B+Reject) x weight, exactly.
Rule 2: opening FG + produced - delivered = closing, per tank type per grade
        (negative on-hand at any point means more left than existed).
Rule 3: every delivery note must link to an invoice.
Rule 4: every invoice must match a payment; unpaid past terms / short / over flagged.
Rule 7: physical counts reconcile to system; variance flags.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import db

EPS = 0.001  # float comparison only; the business tolerance is ZERO


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


def weights_map(cfg: dict) -> dict:
    return {t["code"]: t["weight_kg"] for t in cfg["tank_types"]}


# ---------- Rule 1: powder variance ---------- #

async def check_powder_vs_production(node_id: str, date: str, capture_id: str) -> list[str]:
    """Powder drawn on the day must exactly equal implied powder of all tanks moulded."""
    cfg = await get_config(node_id)
    weights = weights_map(cfg)
    flags_raised = []

    drawn = 0.0
    async for e in db.powder_ledger().find({"node_id": node_id, "date": date, "type": "drawn"}):
        drawn += e["kg"]

    implied = 0.0
    async for r in db.production_runs().find({"node_id": node_id, "date": date}):
        w = weights.get(r["tank_type"], 0)
        implied += (r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]) * w

    gap = drawn - implied
    if abs(gap) > EPS:
        direction = "more powder drawn than moulded" if gap > 0 else "more moulded than powder drawn"
        fid = await raise_flag(
            node_id, "powder_variance",
            f"{date}: powder drawn {drawn:.1f} kg vs implied {implied:.1f} kg "
            f"(gap {gap:+.1f} kg, {direction}). Zero tolerance: every kg drawn must be a moulded tank.",
            {"capture_id": capture_id, "date": date}, date,
        )
        flags_raised.append(fid)
    return flags_raised


# ---------- Rule 2: finished goods identity ---------- #

async def fg_on_hand(node_id: str, up_to_date: str | None = None) -> dict:
    """On-hand per (tank_type, grade) from the finished goods ledger."""
    filt: dict = {"node_id": node_id}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    on_hand: dict = {}
    async for e in db.finished_goods().find(filt):
        key = (e["tank_type"], e["grade"])
        # produced and count_adjustment add (adjustments carry their sign); delivered subtracts
        delta = -e["quantity"] if e["type"] == "delivered" else e["quantity"]
        on_hand[key] = on_hand.get(key, 0) + delta
    return on_hand


async def check_finished_goods(node_id: str, date: str, reference: dict | None = None) -> list[str]:
    """Negative on-hand means deliveries exceed production: ledger identity broken."""
    flags_raised = []
    on_hand = await fg_on_hand(node_id, date)
    for (tank_type, grade), qty in on_hand.items():
        if qty < 0:
            fid = await raise_flag(
                node_id, "finished_goods_mismatch",
                f"{date}: {tank_type} grade {grade} on-hand is {qty} — "
                f"more delivered than produced. Per-type per-grade ledger broken.",
                {**(reference or {}), "tank_type": tank_type, "grade": grade}, date,
            )
            flags_raised.append(fid)
    return flags_raised


# ---------- Rule 3 & 4 sweeps ---------- #

async def sweep_delivery_notes(node_id: str) -> list[str]:
    """Any delivery note not linked to an invoice raises a flag."""
    raised = []
    async for dn in db.delivery_notes().find({"node_id": node_id, "linked_invoice_id": None}):
        fid = await raise_flag(
            node_id, "delivery_without_invoice",
            f"Delivery note {dn['dn_number']} ({dn['date']}, {dn['client_name']}) has no linked invoice. "
            "No tank leaves without both.",
            {"delivery_note_id": dn["_id"]}, dn["date"],
        )
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
            {"invoice_id": inv["_id"]}, inv["date"],
        )
        raised.append(fid)
    return raised


async def sweep_unmatched_payments(node_id: str) -> list[str]:
    raised = []
    async for p in db.payments().find({"node_id": node_id, "status": "unmatched"}):
        fid = await raise_flag(
            node_id, "payment_unmatched",
            f"Payment of R{p['amount']:.2f} on {p['date']} (ref '{p['bank_reference']}') "
            "is not matched to an invoice.",
            {"payment_id": p["_id"]}, p["date"],
        )
        raised.append(fid)
    return raised


async def run_sweeps(node_id: str) -> dict:
    return {
        "delivery_without_invoice": await sweep_delivery_notes(node_id),
        "invoice_unpaid": await sweep_unpaid_invoices(node_id),
        "payment_unmatched": await sweep_unmatched_payments(node_id),
    }


# ---------- Rule 5: payment split ---------- #

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


# ---------- powder running balance ---------- #

async def powder_balance(node_id: str, up_to_date: str | None = None) -> float:
    filt: dict = {"node_id": node_id}
    if up_to_date:
        filt["date"] = {"$lte": up_to_date}
    bal = 0.0
    async for e in db.powder_ledger().find(filt):
        if e["type"] == "in":
            bal += e["kg"]
        elif e["type"] == "drawn":
            bal -= e["kg"]
        else:  # count_adjustment carries its sign
            bal += e["kg"]
    return round(bal, 2)
