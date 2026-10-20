"""Line and document maths, price tiers and price-list versions.

Tax maths: a price entered incl VAT keeps the customer's figure exact. The line total is
qty x unit incl; the ex-VAT amount is total / (1 + vat) to the cent and VAT is the
difference. A price entered ex VAT is grossed up at document level (VAT on the ex-VAT
subtotal), as Brief 1's delivery notes did, so legacy figures do not move by a cent.
"""
from __future__ import annotations

from datetime import date as date_cls
from uuid import uuid4

from fastapi import HTTPException

import db
from corrections import ACTIVE


def r2(x: float) -> float:
    return round(float(x) + 0.0, 2)


def vat_factor(cfg: dict) -> float:
    return 1.0 + float(cfg.get("vat_rate", 15.0)) / 100.0


def qty_of(line: dict) -> float:
    return line.get("qty", line.get("quantity", 0)) or 0


def price_line(line: dict, cfg: dict, qty: float | None = None) -> dict:
    """Return the line with unit_ex, line_ex, line_vat, line_total for `qty` (default its own)."""
    q = qty_of(line) if qty is None else qty
    f = vat_factor(cfg)
    unit = float(line.get("unit_price", 0.0) or 0.0)
    out = dict(line)
    if line.get("price_includes_vat"):
        total = r2(q * unit)
        ex = r2(total / f)
        out.update(unit_ex=r2(unit / f), unit_incl=r2(unit), line_ex=ex,
                   line_vat=r2(total - ex), line_total=total)
    else:
        ex = r2(q * unit)
        out.update(unit_ex=r2(unit), unit_incl=r2(unit * f), line_ex=ex,
                   line_vat=r2(ex * (f - 1)), line_total=r2(ex * f))
    return out


def totals(lines: list[dict], cfg: dict) -> dict:
    """Document totals. Incl-VAT lines contribute their exact totals; ex-VAT lines are summed
    and VAT is charged once on that subtotal."""
    f = vat_factor(cfg)
    incl_total = incl_ex = 0.0
    excl_ex = 0.0
    for ln in lines:
        if ln.get("price_includes_vat"):
            incl_total += ln["line_total"]
            incl_ex += ln["line_ex"]
        else:
            excl_ex += ln["line_ex"]
    excl_ex = r2(excl_ex)
    excl_vat = r2(excl_ex * (f - 1))
    subtotal = r2(incl_ex + excl_ex)
    total = r2(incl_total + excl_ex + excl_vat)
    return {"subtotal": subtotal, "vat": r2(total - subtotal), "total": total,
            "vat_rate": float(cfg.get("vat_rate", 15.0))}


def unit_value_incl(line: dict, cfg: dict) -> float:
    """What one unit of this line costs the customer, incl VAT (release rule, COD amounts)."""
    q = qty_of(line)
    if q and line.get("line_total") is not None:
        return line["line_total"] / q
    return price_line(line, cfg, 1)["line_total"]


# ---------- tiers and versions ---------- #

def tiers(cfg: dict) -> list[dict]:
    return cfg.get("price_tiers") or [{"code": "retail", "name": "Retail", "prices_include_vat": True}]


def tier(cfg: dict, code: str) -> dict:
    for t in tiers(cfg):
        if t["code"] == code:
            return t
    raise HTTPException(400, f"Unknown price tier {code}")


async def current_version(node_id: str, tier_code: str, on: str | None = None) -> dict | None:
    on = on or date_cls.today().isoformat()
    async for v in db.price_lists().find({"node_id": node_id, "tier": tier_code,
                                          "effective_from": {"$lte": on}, **ACTIVE}
                                         ).sort([("effective_from", -1), ("version", -1)]).limit(1):
        return v
    return None


async def new_version(node_id: str, tier_code: str, effective_from: str, prices: dict,
                      user: dict) -> dict:
    """Editing prices always creates a new version; old versions are never changed, so a quote
    or order that locked a version keeps its prices."""
    last = None
    async for v in db.price_lists().find({"node_id": node_id, "tier": tier_code}).sort("version", -1).limit(1):
        last = v
    doc = {"_id": uuid4().hex, "node_id": node_id, "tier": tier_code,
           "version": (last["version"] + 1) if last else 1,
           "effective_from": effective_from, "prices": prices,
           "created_by": user["email"], "date": effective_from}
    await db.price_lists().insert_one(doc)
    return doc


def default_unit(version: dict | None, tank_type: str, grade: str) -> float | None:
    """Tier price for a tank type and grade. No B price: B stock is priced by hand."""
    if not version:
        return None
    p = (version.get("prices") or {}).get(tank_type) or {}
    v = p.get(grade)
    return float(v) if v not in (None, "") else None
