"""Dashboard aggregations: network totals and per-node monthly/yearly breakdowns.

Material is derived from the moulding recipe (see recon): each tank of body weight W and
lid L, colour C, draws W/2 of C plus W/2 + L of black. Lids count toward material.
Material *cost* (Rand) is admin-only and only included when include_cost is True.
"""
from __future__ import annotations

import calendar
from datetime import date as _date

import db
import recon

WEEKDAY = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _empty():
    return {"by_type": {}, "by_colour": {}, "produced": 0, "sold": 0, "kg": 0.0}


def _merge(into: dict, src: dict) -> None:
    """Fold one _empty()-shaped accumulator into another (day -> week -> month)."""
    for k, v in src["by_type"].items():
        b = into["by_type"].setdefault(k, {"a": 0, "b": 0, "reject": 0, "total": 0})
        for f in ("a", "b", "reject", "total"):
            b[f] += v[f]
    for k, v in src["by_colour"].items():
        into["by_colour"][k] = into["by_colour"].get(k, 0.0) + v
    into["produced"] += src["produced"]
    into["sold"] += src["sold"]
    into["kg"] += src["kg"]


def _add_run(acc: dict, r: dict, tmap: dict, black_code: str | None) -> None:
    t = tmap.get(r["tank_type"])
    n = r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]
    bt = acc["by_type"].setdefault(r["tank_type"], {"a": 0, "b": 0, "reject": 0, "total": 0})
    bt["a"] += r["quantity_a"]; bt["b"] += r["quantity_b"]; bt["reject"] += r["quantity_reject"]; bt["total"] += n
    acc["produced"] += n
    if t:
        acc["kg"] += n * (t["weight"] + t["lid"])
        if black_code:
            acc["by_colour"][black_code] = acc["by_colour"].get(black_code, 0.0) + n * (t["weight"] / 2 + t["lid"])
        col = r.get("colour") or ""
        if col:
            acc["by_colour"][col] = acc["by_colour"].get(col, 0.0) + n * (t["weight"] / 2)


async def node_totals(node_id: str, cfg: dict) -> dict:
    """All-time totals for one node: tanks, material kg, tanks-by-type."""
    tmap = recon.tank_map(cfg)
    by_type: dict = {}
    total_tanks = 0
    total_kg = 0.0
    async for r in db.production_runs().find({"node_id": node_id}):
        n = r["quantity_a"] + r["quantity_b"] + r["quantity_reject"]
        by_type[r["tank_type"]] = by_type.get(r["tank_type"], 0) + n
        total_tanks += n
        t = tmap.get(r["tank_type"])
        if t:
            total_kg += n * (t["weight"] + t["lid"])
    names = {t["code"]: t["name"] for t in cfg["tank_types"]}
    return {
        "total_tanks": total_tanks,
        "total_material_kg": round(total_kg, 1),
        "tanks_by_type": [{"tank_type": k, "name": names.get(k, k), "qty": v} for k, v in sorted(by_type.items())],
    }


async def network_dashboard(node_access, include_cost: bool) -> dict:
    """Per-node headline totals + grand total, across the nodes the user may see."""
    nodes_out = []
    grand_tanks = 0
    grand_kg = 0.0
    grand_cost = 0.0
    async for node in db.nodes().find({"status": "active"}):
        if node_access != "all" and node["node_id"] not in node_access:
            continue
        cfg = await db.node_config().find_one({"node_id": node["node_id"]})
        if not cfg:
            continue
        tot = await node_totals(node["node_id"], cfg)
        entry = {"node_id": node["node_id"], "name": node["name"],
                 "location": node.get("location", ""), **tot}
        if include_cost:
            entry["material_cost"] = round(tot["total_material_kg"] * cfg.get("material_cost_per_kg", 0.0), 2)
            grand_cost += entry["material_cost"]
        grand_tanks += tot["total_tanks"]
        grand_kg += tot["total_material_kg"]
        nodes_out.append(entry)
    grand = {"tanks": grand_tanks, "material_kg": round(grand_kg, 1)}
    if include_cost:
        grand["material_cost"] = round(grand_cost, 2)
    return {"nodes": nodes_out, "grand_total": grand}


async def node_dashboard(node_id: str, cfg: dict, year: str, include_cost: bool) -> dict:
    """Month-by-month for `year`, plus year totals and all-time totals."""
    tmap = recon.tank_map(cfg)
    black_code = next(iter(recon.black_codes(cfg)), None)
    mcost = cfg.get("material_cost_per_kg", 0.0)
    names = {t["code"]: t["name"] for t in cfg["tank_types"]}
    colour_name = {p["code"]: (p.get("colour") or p["code"]) for p in cfg.get("powder_products", [])}

    runs = [r async for r in db.production_runs().find({"node_id": node_id})]
    disp = [e async for e in db.finished_goods().find({"node_id": node_id, "type": "dispatched"})]

    years = sorted({r["date"][:4] for r in runs} | {e["date"][:4] for e in disp}, reverse=True)

    months: dict = {}
    year_acc = _empty()
    all_acc = _empty()
    for r in runs:
        _add_run(all_acc, r, tmap, black_code)
        if r["date"][:4] == year:
            _add_run(year_acc, r, tmap, black_code)
            _add_run(months.setdefault(r["date"][:7], _empty()), r, tmap, black_code)
    for e in disp:
        all_acc["sold"] += e["quantity"]
        if e["date"][:4] == year:
            year_acc["sold"] += e["quantity"]
            months.setdefault(e["date"][:7], _empty())["sold"] += e["quantity"]

    def serialize(acc: dict, month: str | None = None) -> dict:
        out: dict = {}
        if month:
            out["month"] = month
        out["tanks_by_type"] = [{"tank_type": k, "name": names.get(k, k), **v}
                                for k, v in sorted(acc["by_type"].items())]
        out["total_produced"] = acc["produced"]
        out["total_sold"] = acc["sold"]
        out["material_by_type"] = [
            {"tank_type": k, "name": names.get(k, k),
             "kg": round(v["total"] * (tmap.get(k, {}).get("weight", 0) + tmap.get(k, {}).get("lid", 0)), 1)}
            for k, v in sorted(acc["by_type"].items())]
        out["material_by_colour"] = [{"colour": colour_name.get(k, k), "kg": round(v, 1)}
                                     for k, v in sorted(acc["by_colour"].items())]
        out["total_material_kg"] = round(acc["kg"], 1)
        if include_cost:
            out["material_cost"] = round(acc["kg"] * mcost, 2)
        return out

    all_time = {"total_produced": all_acc["produced"], "total_sold": all_acc["sold"],
                "total_material_kg": round(all_acc["kg"], 1)}
    if include_cost:
        all_time["material_cost"] = round(all_acc["kg"] * mcost, 2)

    return {
        "year": year,
        "years": years,
        "months": [serialize(months[m], m) for m in sorted(months)],
        "year_totals": serialize(year_acc),
        "all_time": all_time,
    }


async def node_daily(node_id: str, cfg: dict, month: str, include_cost: bool) -> dict:
    """Day-by-day tanks produced/sold for `month` (YYYY-MM), grouped into ISO weeks.

    Every calendar day of the month is returned (zero days included) so the dashboard
    can show production for each day. Each week carries a subtotal; the month total caps it.
    """
    tmap = recon.tank_map(cfg)
    black_code = next(iter(recon.black_codes(cfg)), None)
    mcost = cfg.get("material_cost_per_kg", 0.0)
    names = {t["code"]: t["name"] for t in cfg["tank_types"]}

    day_acc: dict = {}
    async for r in db.production_runs().find({"node_id": node_id, "date": {"$regex": f"^{month}"}}):
        _add_run(day_acc.setdefault(r["date"], _empty()), r, tmap, black_code)
    async for e in db.finished_goods().find(
            {"node_id": node_id, "type": "dispatched", "date": {"$regex": f"^{month}"}}):
        day_acc.setdefault(e["date"], _empty())["sold"] += e["quantity"]

    def serialize(acc: dict, **extra) -> dict:
        out = dict(extra)
        out["tanks_by_type"] = [{"tank_type": k, "name": names.get(k, k), **v}
                                for k, v in sorted(acc["by_type"].items())]
        out["total_produced"] = acc["produced"]
        out["total_sold"] = acc["sold"]
        out["total_material_kg"] = round(acc["kg"], 1)
        if include_cost:
            out["material_cost"] = round(acc["kg"] * mcost, 2)
        return out

    yr, mo = int(month[:4]), int(month[5:7])
    ndays = calendar.monthrange(yr, mo)[1]
    weeks: list = []
    month_acc = _empty()
    cur_key = None
    for d in range(1, ndays + 1):
        dt = _date(yr, mo, d)
        ds = dt.isoformat()
        iso = dt.isocalendar()  # (iso_year, iso_week, iso_weekday)
        if cur_key != (iso[0], iso[1]):
            cur_key = (iso[0], iso[1])
            weeks.append({"week": iso[1], "start": ds, "end": ds, "days": [], "_acc": _empty()})
        wk = weeks[-1]
        acc = day_acc.get(ds, _empty())
        wk["days"].append(serialize(acc, date=ds, weekday=WEEKDAY[dt.weekday()]))
        wk["end"] = ds
        _merge(wk["_acc"], acc)
        _merge(month_acc, acc)

    return {
        "month": month,
        "weeks": [{"week": w["week"], "start": w["start"], "end": w["end"],
                   "days": w["days"], "subtotal": serialize(w["_acc"])} for w in weeks],
        "month_totals": serialize(month_acc),
    }
