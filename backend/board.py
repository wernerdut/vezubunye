"""Management dashboard: summary tiles and the orders, deliveries and invoices boards.

The board is the screen a node owner runs the business from. Rows are derived from the
commercial records (commerce.summarise), then filtered, searched and sorted here so saved
views and CSV downloads return exactly what the screen shows. Every tile maps to a filter.
"""
from __future__ import annotations

import csv
import io
from datetime import date as date_cls, timedelta
from uuid import uuid4

import commerce
import db
import pricing
import recon
from corrections import ACTIVE
from pricing import r2

# ---------- periods ---------- #


def period_range(period: str, start: str | None = None, end: str | None = None) -> tuple[str, str]:
    t = date_cls.fromisoformat(commerce.today())
    if period == "today":
        return t.isoformat(), t.isoformat()
    if period == "week":
        s = t - timedelta(days=t.weekday())
        return s.isoformat(), t.isoformat()
    if period == "last_month":
        first = t.replace(day=1)
        last = first - timedelta(days=1)
        return last.replace(day=1).isoformat(), last.isoformat()
    if period == "custom" and start and end:
        return start, end
    return t.replace(day=1).isoformat(), t.isoformat()   # month (default)


def _in(d: str | None, rng: tuple[str, str]) -> bool:
    return bool(d) and rng[0] <= d[:10] <= rng[1]


# ---------- rows ---------- #

def _pinned(site: dict | None) -> bool | None:
    """True/False for a delivery site, None when there is no site (collected at the factory)."""
    if not site:
        return None
    return site.get("lat") is not None and site.get("lng") is not None


async def order_rows(node_id: str) -> list[dict]:
    cfg = await commerce.get_cfg(node_id)
    b = await commerce.load_order_bundle(node_id)
    rows = []
    for o in b["orders"]:
        dns = b["dns"].get(o["_id"], [])
        invs = b["invoices"].get(o["_id"], [])
        s = commerce.summarise(o, dns, invs, cfg, b["links"].get(o["_id"]))
        site = o.get("site") or {}
        tank_lines = [l for l in s["lines"] if commerce.is_tank(l)]
        active_dns = [d for d in dns if not d.get("void")]
        rows.append({
            "_id": o["_id"], "order_number": o.get("order_number") or o.get("display_number") or "",
            "legacy": o.get("legacy", False), "date": o["date"], "customer_id": o.get("customer_id"),
            "customer": o["customer_name"], "site": site.get("label", ""), "town": site.get("town", ""),
            "address": site.get("address", ""), "pinned": _pinned(site),
            "requested_date": o.get("requested_date"), "value": s["value"], "invoiced": s["invoiced"],
            "paid": s["paid"], "balance": s["balance"], "payment_status": s["payment_status"],
            "overdue": s["overdue"], "cod_outstanding": s["cod_outstanding"],
            "delivery_status": s["delivery_status"], "delivered_label": s["delivered_label"],
            "qty_remaining": s["qty_remaining"], "order_status": s["order_status"],
            "basis": "cod" if o.get("pay_on_delivery") else o["payment_basis"],
            "methods": s["methods"], "age_days": s["age_days"], "next_action": s["next_action"],
            "made_to_order": s["made_to_order"], "refund_due": o.get("refund_due"),
            "tank_types": sorted({l["tank_type"] for l in tank_lines}),
            "colours": sorted({l.get("colour", "") for l in tank_lines if l.get("colour")}),
            "ready_to_invoice": o["payment_basis"] == "terms" and any(
                l.get("qty_delivered", 0) > l.get("qty_invoiced", 0) for l in tank_lines),
            "releasable_value": s["releasable_value"],
            "remaining_value": r2(sum(pricing.unit_value_incl(l, cfg) * l.get("qty_remaining", 0) for l in tank_lines)),
            "lines": s["lines"],
            "timeline": {
                "dns": [{"_id": d["_id"], "dn_number": d["dn_number"], "date": d["date"],
                         "delivery_status": d.get("delivery_status"), "void": bool(d.get("void")),
                         "driver": (d.get("driver") or {}).get("name"), "pod": bool(d.get("pod")),
                         "collect_amount": d.get("collect_amount"),
                         "collected": (d.get("collection") or {}).get("amount"),
                         "invoiced": bool(d.get("invoice_id")) or o["payment_basis"] != "terms"} for d in dns],
                "invoices": [{"_id": i["_id"], "number": i["display_number"], "date": i["date"],
                              "total": i["total"], "paid": i.get("amount_paid", 0), "status": i["status"]}
                             for i in invs],
            },
            "_search": " ".join(filter(None, [o.get("order_number"), o.get("quote_number"), o["customer_name"],
                                              site.get("label"), site.get("town"), site.get("address")]
                                       + [d["dn_number"] for d in dns] + [i["display_number"] for i in invs])).lower(),
            "_dn_planned": [d.get("planned_date") for d in active_dns],
            "_dn_status": [d.get("delivery_status") for d in active_dns],
        })
    return rows


async def delivery_rows(node_id: str) -> list[dict]:
    rows = []
    async for d in db.delivery_notes().find({"node_id": node_id}, {"content_b64": 0, "driver_token": 0}):
        site = d.get("site") or {}
        coll = d.get("collection") or {}
        rows.append({
            "_id": d["_id"], "dn_number": d["dn_number"], "order_id": d.get("order_id"),
            "order_number": d.get("order_number") or "", "customer_id": d.get("customer_id"),
            "customer": d["client_name"], "site": site.get("label", ""), "town": site.get("town", ""),
            "address": site.get("address", ""), "pinned": _pinned(site),
            "date": d["date"], "planned_date": d.get("planned_date") or d["date"],
            "driver": (d.get("driver") or {}).get("name", ""),
            "delivery_status": "void" if d.get("void") else d.get("delivery_status", "delivered"),
            "pod": bool(d.get("pod")), "to_collect": d.get("collect_amount"),
            "collected": coll.get("amount") if coll else None,
            "collection_method": coll.get("method") if coll else None,
            "tanks": sum(l["quantity"] for l in d["lines"]),
            "tank_types": sorted({l["tank_type"] for l in d["lines"]}),
            "colours": sorted({l.get("colour", "") for l in d["lines"] if l.get("colour")}),
            "void": bool(d.get("void")),
            "_search": " ".join(filter(None, [d["dn_number"], d.get("order_number"), d["client_name"],
                                              site.get("label"), site.get("town"), site.get("address")])).lower(),
        })
    return rows


async def invoice_rows(node_id: str) -> list[dict]:
    on = commerce.today()
    rows = []
    async for i in db.invoices().find({"node_id": node_id}):
        bal = r2(i["total"] - i.get("amount_paid", 0))
        overdue_days = (date_cls.fromisoformat(on) - date_cls.fromisoformat(i["due_date"])).days \
            if i.get("due_date") and bal > commerce.PAY_TOL and not i.get("void") else 0
        rows.append({
            "_id": i["_id"], "number": i["display_number"], "order_id": i["order_id"],
            "order_number": i.get("order_number") or "", "customer_id": i.get("customer_id"),
            "customer": i["customer_name"], "date": i["date"], "due_date": i.get("due_date"),
            "total": i["total"], "paid": i.get("amount_paid", 0.0), "balance": bal,
            "status": "void" if i.get("void") else i["status"], "days_overdue": max(0, overdue_days),
            "overdue": overdue_days > 0, "legacy": i.get("legacy", False),
            "xero_status": (i.get("xero") or {}).get("status"),
            "_search": " ".join(filter(None, [i["display_number"], i.get("order_number"), i.get("dn_number"),
                                              i["customer_name"]])).lower(),
        })
    return rows


ROWS = {"orders": order_rows, "deliveries": delivery_rows, "invoices": invoice_rows}


# ---------- filter / sort ---------- #

def _any(val, wanted) -> bool:
    if not wanted:
        return True
    if isinstance(val, list):
        return any(v in wanted for v in val)
    return val in wanted


def apply(kind: str, rows: list[dict], f: dict, sort: dict | None = None) -> list[dict]:
    f = f or {}
    today = commerce.today()
    out = []
    for r in rows:
        if kind == "orders":
            ps = f.get("payment_status") or []
            if ps:
                ok = (r["payment_status"] in ps or ("overdue" in ps and r["overdue"])
                      or ("cod_outstanding" in ps and r["cod_outstanding"]))
                if not ok:
                    continue
            if not _any(r["delivery_status"], f.get("delivery_status")):
                continue
            if not _any(r["order_status"], f["order_status"] if "order_status" in f else ["open"]):
                continue
            if not _any(r["basis"], f.get("basis")):
                continue
            if f.get("method") and not _any(r["methods"], f["method"]):
                continue
            if f.get("to_deliver") and not (r["qty_remaining"] > 0 and (
                    r["basis"] in ("terms", "cod") or (r["releasable_value"] or 0) > 0)):
                continue
            if f.get("ready_to_invoice") and not r["ready_to_invoice"]:
                continue
            if f.get("out_today") and not any(s == "out_for_delivery" for s in r["_dn_status"]):
                continue
            if f.get("requested_from") and (r["requested_date"] or "") < f["requested_from"]:
                continue
            if f.get("requested_to") and (r["requested_date"] or "9999") > f["requested_to"]:
                continue
        if kind == "deliveries":
            if not _any(r["delivery_status"], f.get("delivery_status")):
                continue
            if f.get("today") and r["planned_date"] != today:
                continue
            if f.get("to_collect") and not (r["to_collect"] and r["collected"] is None and not r["void"]):
                continue
            if f.get("unpinned") and not (r["pinned"] is False and r["delivery_status"] in ("scheduled", "out_for_delivery")):
                continue
            if "void" not in (f.get("delivery_status") or []) and r["void"] and not f.get("include_void"):
                continue
        if kind == "invoices":
            st = f.get("status") or []
            if st and not (r["status"] in st or ("overdue" in st and r["overdue"])):
                continue
            if r["status"] == "void" and "void" not in st and not f.get("include_void"):
                continue
        if f.get("made_to_order") and not r.get("made_to_order"):
            continue
        if f.get("tank_type") and not _any(r.get("tank_types", []), _as_list(f["tank_type"])):
            continue
        if f.get("colour") and not _any(r.get("colours", []), _as_list(f["colour"])):
            continue
        if f.get("customer_id") and r.get("customer_id") != f["customer_id"]:
            continue
        if f.get("town") and (r.get("town") or "").lower() != f["town"].lower():
            continue
        if f.get("date_from") and r["date"] < f["date_from"]:
            continue
        if f.get("date_to") and r["date"] > f["date_to"]:
            continue
        if f.get("q") and f["q"].lower() not in r["_search"]:
            continue
        out.append(r)
    if sort and sort.get("key"):
        key, desc = sort["key"], sort.get("dir", "asc") == "desc"
        def k(r):
            v = r.get(key)
            if isinstance(v, dict):
                v = v.get("label")
            if isinstance(v, list):
                v = ",".join(v)
            return (v is None, v if v is not None else "")
        present = [r for r in out if r.get(key) is not None]
        missing = [r for r in out if r.get(key) is None]
        out = sorted(present, key=k, reverse=desc) + missing
    return out


def _as_list(v):
    return v if isinstance(v, list) else [v]


def totals(kind: str, rows: list[dict]) -> dict:
    if kind == "orders":
        return {k: r2(sum(r[k] for r in rows)) for k in ("value", "invoiced", "paid", "balance")} | {"count": len(rows)}
    if kind == "invoices":
        return {k: r2(sum(r[k] for r in rows)) for k in ("total", "paid", "balance")} | {"count": len(rows)}
    return {"count": len(rows), "to_collect": r2(sum(r["to_collect"] or 0 for r in rows if r["collected"] is None)),
            "collected": r2(sum(r["collected"] or 0 for r in rows))}


def public(rows: list[dict]) -> list[dict]:
    return [{k: v for k, v in r.items() if k == "_id" or not k.startswith("_")} for r in rows]


CSV_COLS = {
    "orders": ["order_number", "date", "customer", "site", "town", "requested_date", "value", "invoiced",
               "paid", "balance", "payment_status", "overdue", "delivery_status", "delivered_label", "basis",
               "methods", "age_days", "next_action"],
    "deliveries": ["dn_number", "order_number", "customer", "site", "town", "planned_date", "driver",
                   "delivery_status", "pod", "to_collect", "collected"],
    "invoices": ["number", "order_number", "customer", "date", "due_date", "total", "paid", "balance",
                 "status", "days_overdue"],
}


def to_csv(kind: str, rows: list[dict]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    cols = CSV_COLS[kind]
    w.writerow(cols)
    for r in rows:
        out = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, dict):
                v = v.get("label")
            if isinstance(v, list):
                v = " ".join(v)
            out.append("" if v is None else v)
        w.writerow(out)
    return buf.getvalue()


# ---------- saved views ---------- #

DEFAULT_VIEWS = [
    {"key": "to_deliver", "name": "To deliver", "kind": "orders",
     "filters": {"to_deliver": True}, "sort": {"key": "requested_date", "dir": "asc"}},
    {"key": "awaiting_payment", "name": "Awaiting payment", "kind": "orders",
     "filters": {"payment_status": ["unpaid", "part_paid"]}, "sort": {"key": "date", "dir": "asc"}},
    {"key": "overdue", "name": "Overdue", "kind": "orders",
     "filters": {"payment_status": ["overdue"]}, "sort": {"key": "balance", "dir": "desc"}},
    {"key": "part_delivered", "name": "Part delivered", "kind": "orders",
     "filters": {"delivery_status": ["part_delivered"]}, "sort": {"key": "date", "dir": "asc"}},
    {"key": "out_today", "name": "Out for delivery today", "kind": "orders",
     "filters": {"out_today": True}, "sort": {"key": "date", "dir": "asc"}},
    {"key": "cod", "name": "Collect on delivery", "kind": "orders",
     "filters": {"payment_status": ["cod_outstanding"]}, "sort": {"key": "date", "dir": "asc"}},
    {"key": "ready_to_invoice", "name": "Ready to invoice", "kind": "orders",
     "filters": {"ready_to_invoice": True}, "sort": {"key": "date", "dir": "asc"}},
    {"key": "made_to_order", "name": "Made to order", "kind": "orders",
     "filters": {"made_to_order": True}, "sort": {"key": "date", "dir": "asc"}},
]


async def views(node_id: str, user_email: str) -> list[dict]:
    own = [v async for v in db.dashboard_views().find({"node_id": node_id, "user": user_email})]
    return [{**v, "_id": f"default:{v['key']}", "default": True} for v in DEFAULT_VIEWS] + own


async def save_view(node_id: str, user_email: str, raw: dict) -> dict:
    doc = {"_id": raw.get("_id") if raw.get("_id") and not str(raw["_id"]).startswith("default:") else uuid4().hex,
           "node_id": node_id, "user": user_email, "name": raw["name"], "kind": raw.get("kind", "orders"),
           "filters": raw.get("filters") or {}, "sort": raw.get("sort") or {}, "columns": raw.get("columns") or []}
    await db.dashboard_views().replace_one({"_id": doc["_id"], "node_id": node_id, "user": user_email}, doc, upsert=True)
    return doc


# ---------- tiles ---------- #

async def tiles(node_id: str, rng: tuple[str, str], include_cash: bool, include_fenix: bool) -> dict:
    cfg = await commerce.get_cfg(node_id)
    today = commerce.today()
    rows = await order_rows(node_id)
    open_rows = [r for r in rows if r["order_status"] == "open"]

    # quotes
    q_open = q_value = q_exp = acc = dec = 0
    soon = commerce.add_days(today, 7)
    async for q in db.quotes().find({"node_id": node_id}):
        if q["status"] in ("draft", "sent"):
            q_open += 1
            q_value += q["total"]
            if q["valid_until"] <= soon:
                q_exp += 1
        decided = q.get(f"{q['status']}_at")
        if q["status"] in ("accepted", "declined", "expired") and decided and _in(decided.isoformat(), rng):
            if q["status"] == "accepted":
                acc += 1
            else:
                dec += 1
    quotes = {"open": q_open, "open_value": r2(q_value), "expiring_7d": q_exp,
              "conversion": round(acc / (acc + dec), 3) if acc + dec else None, "accepted": acc, "decided": acc + dec}

    # to deliver + shortfall
    remaining: dict = {}
    mto = 0
    for r in open_rows:
        for l in r["lines"]:
            if commerce.is_tank(l) and l.get("qty_remaining", 0) > 0:
                k = recon.fg_key(cfg, l["tank_type"], l.get("grade", "A"), l.get("colour"))
                remaining[k] = remaining.get(k, 0) + l["qty_remaining"]
                if l.get("made_to_order"):
                    mto += 1
    store = await recon.fg_warehouse(node_id, cfg=cfg)
    shortfall = [{"tank_type": k[0], "colour": k[1] if len(k) == 3 else None, "grade": k[-1],
                  "remaining": q, "on_hand": store.get(k, 0), "short": q - store.get(k, 0)}
                 for k, q in sorted(remaining.items()) if q > store.get(k, 0)]
    td = [r for r in open_rows if r["qty_remaining"] > 0]
    to_deliver = {"qty": sum(r["qty_remaining"] for r in td), "value": r2(sum(r["remaining_value"] for r in td)),
                  "oldest_days": max((r["age_days"] for r in td), default=0), "made_to_order_lines": mto,
                  "shortfall": shortfall}

    # deliveries
    sched_today = out_now = delivered = failed = unpinned = 0
    async for d in db.delivery_notes().find({"node_id": node_id, "legacy": {"$ne": True}, **ACTIVE},
                                            {"content_b64": 0}):
        st = d.get("delivery_status")
        if st == "scheduled" and (d.get("planned_date") or d["date"]) == today:
            sched_today += 1
        if st == "out_for_delivery":
            out_now += 1
        if st in ("scheduled", "out_for_delivery") and _pinned(d.get("site")) is False:
            unpinned += 1
        if st == "delivered" and d.get("delivered_at") and _in(d["delivered_at"].isoformat(), rng):
            delivered += 1
        if st == "failed" and d.get("failed_at") and _in(d["failed_at"].isoformat(), rng):
            failed += 1
    deliveries = {"scheduled_today": sched_today, "out_now": out_now, "delivered": delivered, "failed": failed,
                  "unpinned": unpinned}

    # invoices + aging
    inv_period = outstanding = overdue = 0.0
    aging = {"current": 0.0, "d30": 0.0, "d60": 0.0, "d90": 0.0}
    for r in await invoice_rows(node_id):
        if r["status"] == "void":
            continue
        if _in(r["date"], rng):
            inv_period += r["total"]
        if r["balance"] > commerce.PAY_TOL:
            outstanding += r["balance"]
            if r["overdue"]:
                overdue += r["balance"]
            aging[_bucket(r["days_overdue"])] += r["balance"]
    invoices = {"invoiced": r2(inv_period), "outstanding": r2(outstanding), "overdue": r2(overdue),
                "aging": {k: r2(v) for k, v in aging.items()}}

    # payments
    by_method: dict = {}
    fenix_due = 0.0
    async for p in db.payments().find({"node_id": node_id, **ACTIVE}):
        if _in(p["date"], rng):
            m = p.get("method", "eft") + ("_" + p["source"] if p.get("source") else "")
            by_method[m] = r2(by_method.get(m, 0.0) + p["amount"])
            fenix_due += (p.get("split") or {}).get("fenix_exworks_value", 0.0) or 0.0
    links_unpaid = {"count": 0, "value": 0.0, "oldest": None}
    async for pl in db.pay_links().find({"node_id": node_id, "status": {"$in": ["open", "sent"]}, "sent_at": {"$ne": None}}):
        links_unpaid["count"] += 1
        links_unpaid["value"] = r2(links_unpaid["value"] + pl["amount"])
        d = pl["sent_at"].date().isoformat()
        links_unpaid["oldest"] = min(links_unpaid["oldest"] or d, d)
    cod_out = r2(sum(r["balance"] for r in rows if r["cod_outstanding"] and r["order_status"] == "open"))
    payments = {"by_method": by_method, "received": r2(sum(by_method.values())),
                "pay_links_unpaid": links_unpaid, "cod_outstanding": cod_out}
    if include_cash:
        payments["cash_on_hand"] = [{k: v for k, v in h.items() if k != "payment_ids"}
                                    for h in await commerce.cash_on_hand(node_id)]
    out = {"period": {"from": rng[0], "to": rng[1]}, "quotes": quotes, "to_deliver": to_deliver,
           "deliveries": deliveries, "invoices": invoices, "payments": payments}
    if include_fenix and commerce.fenix_model(cfg) == "fenix_owns_tanks":
        out["fenix"] = {"draw_due": r2(fenix_due)}
    return out


def _bucket(days: int) -> str:
    if days <= 0:
        return "current"
    if days <= 30:
        return "d30"
    if days <= 60:
        return "d60"
    return "d90"


# ---------- debtors and statements ---------- #

async def debtors(node_id: str) -> list[dict]:
    on = commerce.today()
    by: dict = {}
    async for c in db.customers().find({"node_id": node_id}):
        by[c["_id"]] = {"customer_id": c["_id"], "name": c["name"], "phone": c.get("phone", ""),
                        "basis": c.get("payment_basis"), "current": 0.0, "d30": 0.0, "d60": 0.0, "d90": 0.0,
                        "balance": 0.0, "overdue": 0.0, "credit": 0.0, "cod_uncollected": 0.0}
    async for i in db.invoices().find({"node_id": node_id, **ACTIVE}):
        e = by.get(i.get("customer_id"))
        if not e:
            continue
        bal = r2(i["total"] - i.get("amount_paid", 0))
        if bal <= commerce.PAY_TOL:
            continue
        days = (date_cls.fromisoformat(on) - date_cls.fromisoformat(i["due_date"])).days
        e[_bucket(days)] = r2(e[_bucket(days)] + bal)
        e["balance"] = r2(e["balance"] + bal)
        if days > 0:
            e["overdue"] = r2(e["overdue"] + bal)
    async for p in db.payments().find({"node_id": node_id, "unallocated": {"$gt": 0.004}, **ACTIVE}):
        e = by.get(p.get("customer_id"))
        if e:
            e["credit"] = r2(e["credit"] + p["unallocated"])
    async for d in db.delivery_notes().find({"node_id": node_id, **ACTIVE, "collect_amount": {"$gt": 0}},
                                            {"content_b64": 0}):
        coll = d.get("collection")
        if coll and coll.get("method") == "not_collected":
            e = by.get(d.get("customer_id"))
            if e:
                e["cod_uncollected"] = r2(e["cod_uncollected"] + d["collect_amount"])
    return sorted([e for e in by.values() if e["balance"] or e["credit"] or e["cod_uncollected"]],
                  key=lambda e: -e["balance"])


async def statement(node_id: str, customer_id: str, d_from: str, d_to: str) -> dict:
    events = []
    async for i in db.invoices().find({"node_id": node_id, "customer_id": customer_id, **ACTIVE}):
        events.append({"date": i["date"], "type": "Invoice", "ref": i["display_number"], "debit": i["total"], "credit": 0.0})
    async for p in db.payments().find({"node_id": node_id, "customer_id": customer_id, **ACTIVE}):
        events.append({"date": p["date"], "type": f"Payment ({p.get('method', 'eft').upper()})",
                       "ref": p.get("reference") or "", "debit": 0.0, "credit": p["amount"]})
    events.sort(key=lambda e: (e["date"], e["credit"] > 0))
    opening = r2(sum(e["debit"] - e["credit"] for e in events if e["date"] < d_from))
    bal = opening
    rows = []
    for e in events:
        if d_from <= e["date"] <= d_to:
            bal = r2(bal + e["debit"] - e["credit"])
            rows.append({**e, "balance": bal})
    deb = next((x for x in await debtors(node_id) if x["customer_id"] == customer_id), None) or \
        {"current": 0.0, "d30": 0.0, "d60": 0.0, "d90": 0.0}
    return {"date": commerce.today(), "from": d_from, "to": d_to, "opening": opening, "rows": rows,
            "closing": bal, "aging": {k: deb[k] for k in ("current", "d30", "d60", "d90")}}
