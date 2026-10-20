"""Commercial chain shared by every surface: quotes, orders, invoices, delivery notes,
payments and their allocation, the Fenix split, cash control and the order statuses the
dashboard runs on.

Route handlers (internal /api, partner /api/p, driver /api/d) stay thin and call these.
Nothing here is forked per surface.

Order of events is decided by the order's payment basis:
  upfront, pay first : order -> one invoice for the full order (+ pay link) -> paid -> deliveries up to the value paid
  upfront, COD       : order -> one invoice -> delivery -> driver collects -> complete
  terms              : order -> deliveries -> one invoice per delivery -> paid within terms
"""
from __future__ import annotations

import base64
import secrets as pysecrets
from datetime import date as date_cls, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

import audit
import corrections
import db
import maps
import pricing
import recon
from corrections import ACTIVE
from pricing import r2

PAY_TOL = 1.0     # cent-level VAT rounding; genuine short and over payments still flag
SAST = timezone(timedelta(hours=2))


def now() -> datetime:
    return datetime.utcnow()


def today() -> str:
    return datetime.now(SAST).date().isoformat()


def add_days(d: str, n: int) -> str:
    return (date_cls.fromisoformat(d) + timedelta(days=n)).isoformat()


async def get_node(node_id: str) -> dict:
    node = await db.nodes().find_one({"node_id": node_id})
    if not node:
        raise HTTPException(404, f"Node {node_id} not found")
    return node


async def get_cfg(node_id: str) -> dict:
    cfg = await db.node_config().find_one({"node_id": node_id})
    if not cfg:
        raise HTTPException(404, f"No config for node {node_id}")
    return cfg


def prefix(node: dict) -> str:
    return node.get("prefix") or node["node_id"].upper()


async def number(node: dict, kind: str, label: str, session=None) -> str:
    seq = await db.next_number(node["node_id"], kind, session=session)
    return f"{prefix(node)}-{label}-{seq:04d}"


def fenix_model(cfg: dict) -> str:
    return cfg.get("commercial_model", "fenix_owns_tanks")


def tank_names(cfg: dict) -> dict:
    return {t["code"]: t["name"] for t in cfg["tank_types"]}


def colour_names(cfg: dict) -> dict:
    return {c["code"]: c["name"] for c in cfg.get("colours", []) or []}


def is_tank(line: dict) -> bool:
    return line.get("kind", "tank") == "tank"


def line_label(cfg: dict, ln: dict) -> str:
    if not is_tank(ln):
        return ln.get("name") or ln.get("code", "Charge")
    col = colour_names(cfg).get(ln.get("colour") or "", ln.get("colour") or "")
    g = "" if ln.get("grade", "A") == "A" else " (B grade)"
    return f"{tank_names(cfg).get(ln['tank_type'], ln['tank_type'])}{' ' + col if col else ''}{g}"


# ============================== lines ============================== #

async def build_lines(cfg: dict, node_id: str, raw: list[dict], tier_code: str,
                      version: dict | None) -> list[dict]:
    """Validate and price quote/order lines. Unit prices default from the tier's locked version
    and stay editable per line. Tank lines need a known type, an allowed colour, a grade."""
    if not raw:
        raise HTTPException(400, "Add at least one line")
    tanks = {t["code"]: t for t in cfg["tank_types"]}
    colours = {c["code"]: c for c in cfg.get("colours", []) or []}
    charges = {c["code"]: c for c in cfg.get("charges", []) or []}
    incl = pricing.tier(cfg, tier_code).get("prices_include_vat", False)
    out = []
    for ln in raw:
        kind = ln.get("kind", "tank")
        qty = ln.get("qty", ln.get("quantity", 0 if kind == "tank" else 1))
        if not qty or qty <= 0:
            raise HTTPException(400, "Each line needs a quantity above zero")
        if kind == "tank":
            t = tanks.get(ln.get("tank_type"))
            if not t:
                raise HTTPException(400, f"Unknown tank type {ln.get('tank_type')}")
            col = ln.get("colour") or ""
            if colours:
                if col not in colours:
                    raise HTTPException(400, f"Pick a colour for {t['name']}")
                allowed = t.get("allowed_colours") or []
                if allowed and col not in allowed:
                    raise HTTPException(400, f"{t['name']} is not made in {colours[col]['name']}")
            grade = ln.get("grade", "A")
            unit = ln.get("unit_price")
            if unit in (None, ""):
                unit = pricing.default_unit(version, t["code"], grade)
                if unit is None:
                    raise HTTPException(400, f"No list price for {t['name']} grade {grade}: price it by hand")
            line = {"line_id": ln.get("line_id") or uuid4().hex[:10], "kind": "tank",
                    "tank_type": t["code"], "colour": col, "grade": grade, "qty": int(qty),
                    "unit_price": float(unit),
                    "price_includes_vat": bool(ln.get("price_includes_vat", incl)),
                    "made_to_order": bool(colours.get(col, {}).get("availability") == "on_request"),
                    "qty_cancelled": int(ln.get("qty_cancelled", 0) or 0)}
        else:
            ch = charges.get(ln.get("code"))
            if not ch:
                raise HTTPException(400, f"Unknown charge {ln.get('code')}")
            unit = ln.get("unit_price")
            if unit in (None, ""):
                unit = ch.get("amount")
                if unit is None:
                    raise HTTPException(400, f"{ch['name']} is quoted per job: enter the amount")
            line = {"line_id": ln.get("line_id") or uuid4().hex[:10], "kind": "charge",
                    "code": ch["code"], "name": ln.get("name") or ch["name"], "qty": qty,
                    "unit_price": float(unit),
                    "price_includes_vat": bool(ln.get("price_includes_vat", ch.get("includes_vat", True))),
                    "qty_cancelled": 0}
        out.append(pricing.price_line(line, cfg))
    return out


# ============================== customers ============================== #

async def get_customer(node_id: str, customer_id: str) -> dict:
    c = await db.customers().find_one({"_id": customer_id, "node_id": node_id})
    if not c:
        raise HTTPException(404, "Customer not found")
    return c


def site_of(customer: dict, site_id: str | None) -> dict | None:
    sites = customer.get("sites") or []
    if site_id:
        for s in sites:
            if s["site_id"] == site_id:
                return s
        raise HTTPException(400, "Delivery site not found on this customer")
    return None


async def make_site(raw: dict) -> dict:
    """Site from a form: a pasted Maps link is resolved and parsed; typed or device
    coordinates are taken as given. The original link is always kept."""
    site = {"site_id": raw.get("site_id") or uuid4().hex[:10],
            "label": raw.get("label") or "Site", "address": raw.get("address", ""),
            "town": raw.get("town", ""), "contact_name": raw.get("contact_name", ""),
            "contact_phone": raw.get("contact_phone", ""), "directions": raw.get("directions", ""),
            "maps_link": raw.get("maps_link") or "", "lat": raw.get("lat"), "lng": raw.get("lng")}
    if site["maps_link"] and (site["lat"] is None or site["lng"] is None):
        loc = await maps.locate(site["maps_link"])
        site.update(lat=loc["lat"], lng=loc["lng"], resolved_link=loc["resolved_link"])
    site["pinned"] = site["lat"] is not None and site["lng"] is not None
    return site


async def create_customer(node_id: str, raw: dict, user: dict, allow_terms: bool) -> dict:
    cfg = await get_cfg(node_id)
    if not (raw.get("name") or "").strip():
        raise HTTPException(400, "Customer name required")
    basis = raw.get("payment_basis") or "upfront"
    tier = raw.get("price_tier") or "retail"
    if not allow_terms and (basis != "upfront" or tier != "retail"):
        raise HTTPException(403, "Only a partner admin puts a customer on terms or a price tier")
    pricing.tier(cfg, tier)
    sites = [await make_site(s) for s in raw.get("sites") or []]
    doc = {"_id": uuid4().hex, "node_id": node_id, "name": raw["name"].strip(),
           "contact_person": raw.get("contact_person", ""), "email": raw.get("email", ""),
           "phone": raw.get("phone", ""), "billing_address": raw.get("billing_address", ""),
           "vat_number": raw.get("vat_number", ""), "payment_basis": basis,
           "payment_terms_days": raw.get("payment_terms_days") or cfg.get("payment_terms_days", 30),
           "price_tier": tier, "xero_contact_id": None, "active": True, "sites": sites,
           "date": today(), "created_at": now(), "created_by": user["email"]}
    await db.customers().insert_one(doc)
    await audit.log(user, node_id, "create", "customers", doc["_id"], after=doc)
    return doc


# ============================== orders ============================== #

def order_lines_value(order: dict) -> float:
    return r2(sum(ln["line_total"] for ln in order["lines"]))


async def create_order(node: dict, cfg: dict, customer: dict, raw: dict, user: dict,
                       quote: dict | None = None, session=None, legacy: bool = False,
                       extra: dict | None = None) -> dict:
    """An order locks its prices at creation. Upfront orders get their one invoice now."""
    node_id = node["node_id"]
    tier = (quote or {}).get("price_tier") or customer.get("price_tier", "retail")
    version = None
    if quote:
        lines = [dict(ln, qty_cancelled=0) for ln in quote["lines"]]
        version_id = quote.get("price_list_version_id")
    else:
        version = await pricing.current_version(node_id, tier)
        lines = await build_lines(cfg, node_id, raw.get("lines") or [], tier, version)
        version_id = version["_id"] if version else None
    site = site_of(customer, raw.get("site_id")) if raw.get("site_id") else (
        (quote or {}).get("site") or ((customer.get("sites") or [None])[0]))
    basis = customer.get("payment_basis", "upfront")
    cod = bool(raw.get("pay_on_delivery")) and basis == "upfront"
    order = {
        "_id": uuid4().hex, "node_id": node_id,
        "order_number": None if legacy else await number(node, "ord", "ORD", session),
        "date": raw.get("date") or today(), "customer_id": customer["_id"],
        "customer_name": customer["name"], "site": site,
        "quote_id": (quote or {}).get("_id"), "quote_number": (quote or {}).get("quote_number"),
        "payment_basis": basis, "pay_on_delivery": cod,
        "cod_method": raw.get("cod_method", "either") if cod else None,
        "price_tier": tier, "price_list_version_id": version_id,
        "lines": lines, **pricing.totals(lines, cfg),
        "requested_date": raw.get("requested_date"), "notes": raw.get("notes", ""),
        "status": "open", "legacy": legacy, "created_by": user["email"], "created_at": now(),
        **(extra or {}),
    }
    await db.orders().insert_one(order, session=session)
    if basis == "upfront" and not legacy:
        await create_invoice(node, cfg, order, order["lines"], user, session=session)
    await audit.log(user, node_id, "create", "orders", order["_id"], after=order, session=session)
    return order


async def order_or_404(node_id: str, order_id: str) -> dict:
    o = await db.orders().find_one({"_id": order_id, "node_id": node_id})
    if not o:
        raise HTTPException(404, "Order not found")
    return o


# ============================== invoices ============================== #

async def create_invoice(node: dict, cfg: dict, order: dict, lines: list[dict], user: dict,
                         dn: dict | None = None, session=None, legacy: bool = False,
                         extra: dict | None = None) -> dict:
    """One invoice for a full upfront order, or one per delivery note on terms. Lines are the
    order's lines (priced at order prices) for the quantities invoiced."""
    node_id = node["node_id"]
    inv_date = (dn or {}).get("date") if dn else (order.get("date") or today())
    inv_date = inv_date or today()
    customer = await db.customers().find_one({"_id": order.get("customer_id")}, session=session) or {}
    terms_days = customer.get("payment_terms_days") or cfg.get("payment_terms_days", 30)
    due = add_days(inv_date, terms_days) if order["payment_basis"] == "terms" else inv_date
    inv = {
        "_id": uuid4().hex, "node_id": node_id,
        "invoice_number": None if legacy else await number(node, "inv", "INV", session),
        "order_id": order["_id"], "order_number": order.get("order_number"),
        "dn_id": (dn or {}).get("_id"), "dn_number": (dn or {}).get("dn_number"),
        "customer_id": order.get("customer_id"), "customer_name": order["customer_name"],
        "date": inv_date, "due_date": due, "lines": lines, **pricing.totals(lines, cfg),
        "amount_paid": 0.0, "status": "unpaid", "legacy": legacy,
        "xero": {"status": "not_posted" if not legacy else "never"},
        "created_by": user["email"], "created_at": now(), **(extra or {}),
    }
    inv["balance"] = inv["total"]
    inv["display_number"] = inv["invoice_number"] or (dn or {}).get("dn_number") or ""
    if fenix_model(cfg) == "fenix_owns_tanks":
        inv["fenix_value"] = recon.compute_split(inv, cfg, inv["total"])["fenix_exworks_value"]
    await db.invoices().insert_one(inv, session=session)
    if not legacy:
        await audit.log(user, node_id, "create", "invoices", inv["_id"], after=inv, session=session)
    return inv


def inv_status(paid: float, total: float) -> str:
    if paid <= 0.004:
        return "unpaid"
    if paid + PAY_TOL < total:
        return "part_paid"
    if paid > total + PAY_TOL:
        return "flagged"
    return "paid"


async def refresh_invoice(invoice_id: str, session=None) -> dict | None:
    """Recompute amount paid and status from the active allocations, and mirror the result on
    a terms or legacy invoice's delivery note so Brief 1 screens keep reading true."""
    inv = await db.invoices().find_one({"_id": invoice_id}, session=session)
    if not inv:
        return None
    paid = 0.0
    async for p in db.payments().find({"allocations.invoice_id": invoice_id, **ACTIVE}, session=session):
        for a in p.get("allocations") or []:
            if a["invoice_id"] == invoice_id:
                paid += a["amount"]
    paid = r2(paid)
    upd = {"amount_paid": paid, "balance": r2(inv["total"] - paid)}
    if not inv.get("void"):
        upd["status"] = inv_status(paid, inv["total"])
    await db.invoices().update_one({"_id": invoice_id}, {"$set": upd}, session=session)
    if inv.get("dn_id") and not inv.get("void"):
        await db.delivery_notes().update_one({"_id": inv["dn_id"]},
                                             {"$set": {"amount_paid": paid, "status": upd["status"]}},
                                             session=session)
    return {**inv, **upd}


async def open_invoices(node_id: str, customer_id: str, session=None) -> list[dict]:
    out = []
    async for inv in db.invoices().find({"node_id": node_id, "customer_id": customer_id, **ACTIVE,
                                         "status": {"$in": ["unpaid", "part_paid"]}},
                                        session=session).sort([("date", 1), ("created_at", 1)]):
        out.append(inv)
    return out


# ============================== split ============================== #

def split_for(inv: dict, cfg: dict, amount: float) -> dict:
    """Fenix's draw on an invoice falls due in proportion to what is paid on it:
    draw = Fenix value x amount / invoice total, on the gross amount (Yoco fees are the
    partner's). On a fully paid invoice this equals Brief 1's figure exactly. A powder_sale
    node computes no split."""
    if fenix_model(cfg) != "fenix_owns_tanks":
        return {"fenix_exworks_value": 0.0, "partner_balance": r2(amount), "model": "powder_sale"}
    value = inv.get("fenix_value")
    if value is None:
        value = recon.compute_split(inv, cfg, inv["total"])["fenix_exworks_value"]
    share = r2(value * amount / inv["total"]) if inv["total"] else 0.0
    return {"fenix_exworks_value": share, "partner_balance": r2(amount - share)}


async def payment_split(payment: dict, cfg: dict, session=None) -> dict | None:
    allocs = payment.get("allocations") or []
    if not allocs:
        return None
    fenix = 0.0
    for a in allocs:
        inv = await db.invoices().find_one({"_id": a["invoice_id"]}, session=session)
        if inv:
            fenix += split_for(inv, cfg, a["amount"])["fenix_exworks_value"]
    fenix = r2(fenix)
    out = {"fenix_exworks_value": fenix, "partner_balance": r2(payment["amount"] - fenix)}
    if fenix_model(cfg) != "fenix_owns_tanks":
        out["model"] = "powder_sale"
    return out


# ============================== payments ============================== #

def default_allocation(amount: float, invoices: list[dict]) -> list[dict]:
    """Oldest open invoice first."""
    out, left = [], r2(amount)
    for inv in invoices:
        if left <= 0:
            break
        take = r2(min(left, inv["total"] - inv.get("amount_paid", 0.0)))
        if take > 0:
            out.append({"invoice_id": inv["_id"], "amount": take})
            left = r2(left - take)
    return out


async def record_payment(node_id: str, raw: dict, user: dict, session=None,
                         allocations: list[dict] | None = None, auto_allocate: bool = True,
                         flag_unallocated: bool = True) -> dict:
    """Create a payment and allocate it. `allocations` is explicit; otherwise the payment goes
    to the given invoice, else oldest open invoice of the customer first. Any remainder stays
    on the customer as credit and raises payment_unallocated."""
    cfg = await get_cfg(node_id)
    amount = r2(raw["amount"])
    if amount <= 0:
        raise HTTPException(400, "Amount must be above zero")
    customer_id = raw.get("customer_id")
    if raw.get("invoice_id") and allocations is None:
        inv = await db.invoices().find_one({"_id": raw["invoice_id"], "node_id": node_id, **ACTIVE},
                                           session=session)
        if not inv:
            raise HTTPException(400, "Invoice not found on this node")
        customer_id = customer_id or inv.get("customer_id")
        allocations = [{"invoice_id": inv["_id"], "amount": amount}]
    if allocations is None and auto_allocate and customer_id:
        allocations = default_allocation(amount, await open_invoices(node_id, customer_id, session))
    allocations = [a for a in (allocations or []) if a.get("amount", 0) > 0]
    for a in allocations:
        inv = await db.invoices().find_one({"_id": a["invoice_id"], "node_id": node_id, **ACTIVE},
                                           session=session)
        if not inv:
            raise HTTPException(400, "Allocation to an invoice not on this node")
        if customer_id and inv.get("customer_id") and inv["customer_id"] != customer_id:
            raise HTTPException(400, "A payment is allocated only to the same customer's invoices")
        customer_id = customer_id or inv.get("customer_id")
    alloc_total = r2(sum(a["amount"] for a in allocations))
    if alloc_total > amount + 0.004:
        raise HTTPException(400, "Allocated more than the payment")
    method = raw.get("method", "eft")
    p = {"_id": uuid4().hex, "node_id": node_id, "date": raw.get("date") or today(),
         "amount": amount, "method": method, "source": raw.get("source"),
         "reference": raw.get("reference") or raw.get("bank_reference", ""),
         "bank_reference": raw.get("bank_reference") or raw.get("reference", ""),
         "customer_id": customer_id, "allocations": allocations,
         "unallocated": r2(amount - alloc_total),
         "holder": raw.get("holder") if method == "cash" else None,
         "deposit_id": None, "dn_id": raw.get("dn_id"),
         "external_id": raw.get("external_id"), "xero_payment_id": raw.get("xero_payment_id"),
         "posted_to_xero": False, "captured_by": user["email"], "created_at": now(),
         # Brief 1 compatibility: a single-invoice allocation reads as a matched delivery
         "matched_delivery_id": None, "status": "unmatched"}
    p["split"] = await payment_split(p, cfg, session)
    await _compat_fields(p, session)
    await db.payments().insert_one(p, session=session)
    for a in allocations:
        await refresh_invoice(a["invoice_id"], session)
    await audit.log(user, node_id, "create", "payments", p["_id"], after=p, session=session)
    if p["unallocated"] > 0.004 and flag_unallocated:
        await recon.raise_flag(node_id, "payment_unallocated",
                               f"Payment of R{amount:,.2f} on {p['date']} (ref '{p['reference']}') "
                               f"has R{p['unallocated']:,.2f} not allocated to an invoice.",
                               {"payment_id": p["_id"]}, p["date"])
    await check_short_over(node_id, allocations, p, session)
    return p


async def _compat_fields(p: dict, session=None):
    """matched_delivery_id / status kept for the internal Payments tab."""
    allocs = p.get("allocations") or []
    if not allocs:
        p.update(matched_delivery_id=None, status="unmatched")
        return
    inv = await db.invoices().find_one({"_id": allocs[0]["invoice_id"]}, session=session)
    p["matched_delivery_id"] = (inv or {}).get("dn_id")
    p["status"] = "matched" if p["unallocated"] <= 0.004 else "flagged"


async def check_short_over(node_id: str, allocations: list[dict], p: dict, session=None) -> list[str]:
    raised = []
    for a in allocations:
        inv = await db.invoices().find_one({"_id": a["invoice_id"]}, session=session)
        if not inv:
            continue
        if inv["amount_paid"] > inv["total"] + PAY_TOL:
            raised.append(await recon.raise_flag(
                node_id, "over_paid",
                f"Invoice {inv['display_number']}: paid R{inv['amount_paid']:.2f} against "
                f"R{inv['total']:.2f} (over by R{inv['amount_paid'] - inv['total']:.2f}).",
                {"invoice_id": inv["_id"], "payment_id": p["_id"]}, p["date"]))
    return raised


async def reallocate(payment_id: str, allocations: list[dict], user: dict, reason: str) -> dict:
    p = await db.payments().find_one({"_id": payment_id, **ACTIVE})
    if not p:
        raise HTTPException(404, "Payment not found")
    cfg = await get_cfg(p["node_id"])
    before = dict(p)
    total = r2(sum(a["amount"] for a in allocations))
    if total > p["amount"] + 0.004:
        raise HTTPException(400, "Allocated more than the payment")
    for a in allocations:
        inv = await db.invoices().find_one({"_id": a["invoice_id"], "node_id": p["node_id"], **ACTIVE})
        if not inv:
            raise HTTPException(400, "Allocation to an invoice not on this node")
    async with corrections.txn() as s:
        upd = {"allocations": allocations, "unallocated": r2(p["amount"] - total)}
        tmp = {**p, **upd}
        upd["split"] = await payment_split(tmp, cfg, s)
        await _compat_fields(tmp, s)
        upd["matched_delivery_id"], upd["status"] = tmp["matched_delivery_id"], tmp["status"]
        await db.payments().update_one({"_id": payment_id}, {"$set": upd}, session=s)
        for inv_id in {a["invoice_id"] for a in (p.get("allocations") or []) + allocations}:
            await refresh_invoice(inv_id, s)
        await audit.log(user, p["node_id"], "reallocate", "payments", payment_id,
                        before={"record": before, "reason": reason}, after=upd, session=s)
    return {**p, **upd}


# ============================== cash control ============================== #

async def cash_on_hand(node_id: str) -> list[dict]:
    """Undeposited cash per holder (staff member or driver)."""
    holders: dict = {}
    async for p in db.payments().find({"node_id": node_id, "method": "cash", "deposit_id": None, **ACTIVE}):
        h = p.get("holder") or {"type": "staff", "name": p.get("captured_by", "?")}
        key = f"{h.get('type')}:{h.get('name')}"
        e = holders.setdefault(key, {"holder": h, "amount": 0.0, "receipts": 0, "oldest": p["date"],
                                     "payment_ids": []})
        e["amount"] = r2(e["amount"] + p["amount"])
        e["receipts"] += 1
        e["oldest"] = min(e["oldest"], p["date"])
        e["payment_ids"].append(p["_id"])
    return sorted(holders.values(), key=lambda x: x["oldest"])


async def bank_cash(node_id: str, raw: dict, user: dict) -> dict:
    ids = raw.get("payment_ids") or []
    if not ids:
        raise HTTPException(400, "Pick the cash receipts this deposit covers")
    receipts = [p async for p in db.payments().find({"_id": {"$in": ids}, "node_id": node_id,
                                                      "method": "cash", **ACTIVE})]
    if len(receipts) != len(set(ids)):
        raise HTTPException(400, "Every receipt must be an active cash receipt on this node")
    if any(p.get("deposit_id") for p in receipts):
        raise HTTPException(400, "A receipt in this deposit is already banked")
    covered = r2(sum(p["amount"] for p in receipts))
    amount = r2(raw["amount"])
    dep = {"_id": uuid4().hex, "node_id": node_id, "date": raw.get("date") or today(),
           "amount": amount, "payment_ids": ids, "receipts_total": covered,
           "reference": raw.get("reference", ""), "slip_file_id": raw.get("slip_file_id"),
           "by": user["email"], "created_at": now()}
    async with corrections.txn() as s:
        await db.cash_deposits().insert_one(dep, session=s)
        await db.payments().update_many({"_id": {"$in": ids}}, {"$set": {"deposit_id": dep["_id"]}},
                                        session=s)
        await audit.log(user, node_id, "create", "cash_deposits", dep["_id"], after=dep, session=s)
    if abs(amount - covered) > 0.004:
        dep["flag"] = await recon.raise_flag(
            node_id, "cash_deposit_mismatch",
            f"Cash deposit of R{amount:,.2f} on {dep['date']} covers receipts totalling "
            f"R{covered:,.2f} (difference R{amount - covered:+,.2f}).",
            {"deposit_id": dep["_id"]}, dep["date"])
    return dep


# ============================== delivery notes ============================== #

def driver_token() -> str:
    return pysecrets.token_urlsafe(24)


async def dispatched_by_line(order_id: str, session=None, exclude_dn: str | None = None) -> dict:
    """order line_id -> quantity on active delivery notes."""
    out: dict = {}
    filt = {"order_id": order_id, **ACTIVE}
    if exclude_dn:
        filt["_id"] = {"$ne": exclude_dn}
    async for dn in db.delivery_notes().find(filt, session=session):
        for ln in dn["lines"]:
            if ln.get("order_line_id"):
                out[ln["order_line_id"]] = out.get(ln["order_line_id"], 0) + ln["quantity"]
    return out


async def check_stock(node_id: str, cfg: dict, want: dict, freed: dict | None = None):
    """want: fg_key -> qty. Blocks a delivery when that type, colour and grade is not on hand,
    even if another colour is."""
    store = await recon.fg_warehouse(node_id, cfg=cfg)
    for k, q in (freed or {}).items():
        store[k] = store.get(k, 0) + q
    names = tank_names(cfg)
    cols = colour_names(cfg)
    for k, qty in want.items():
        have = store.get(k, 0)
        if qty > have:
            what = names.get(k[0], k[0])
            if len(k) == 3 and k[1]:
                what += f" {cols.get(k[1], k[1])}"
            raise HTTPException(400, f"Only {have} × {what} grade {k[-1]} in stock — cannot deliver {qty}")


def dn_value_incl(dn_lines: list[dict], order: dict, cfg: dict) -> float:
    """Value of delivered quantities at the order's prices, incl VAT."""
    by_id = {ln["line_id"]: ln for ln in order["lines"]}
    v = 0.0
    for dl in dn_lines:
        ol = by_id.get(dl.get("order_line_id"))
        if ol:
            v += dl["quantity"] * pricing.unit_value_incl(ol, cfg)
    return r2(v)


async def order_paid(order_id: str, session=None) -> float:
    paid = 0.0
    async for inv in db.invoices().find({"order_id": order_id, **ACTIVE}, session=session):
        paid += inv.get("amount_paid", 0.0)
    return r2(paid)


async def write_dn(node: dict, cfg: dict, order: dict, raw: dict, user: dict, session=None,
                   extra: dict | None = None, freed: dict | None = None,
                   skip_checks: bool = False) -> dict:
    """Number and insert a delivery note against an order, plus its stock-out rows (the tanks
    leave finished goods now: they are on the truck). Applies the remaining-quantity and stock
    checks and, on upfront pay-first orders, the release rule."""
    node_id = node["node_id"]
    by_id = {ln["line_id"]: ln for ln in order["lines"]}
    disp = await dispatched_by_line(order["_id"], session)
    lines, want = [], {}
    for rl in raw.get("lines") or []:
        q = int(rl.get("quantity", 0) or 0)
        if q <= 0:
            continue
        ol = by_id.get(rl.get("order_line_id"))
        if not ol or not is_tank(ol):
            raise HTTPException(400, "Delivery line not on this order")
        remaining = ol["qty"] - ol.get("qty_cancelled", 0) - disp.get(ol["line_id"], 0)
        if q > remaining and not skip_checks:
            raise HTTPException(400, f"Only {remaining} × {line_label(cfg, ol)} left to deliver on this order")
        priced = pricing.price_line(ol, cfg, q)
        lines.append({"order_line_id": ol["line_id"], "tank_type": ol["tank_type"],
                      "colour": ol.get("colour", ""), "grade": ol.get("grade", "A"),
                      "quantity": q, "unit_price": priced["unit_ex"],
                      "line_total": priced["line_total"], "made_to_order": ol.get("made_to_order", False)})
        k = recon.fg_key(cfg, ol["tank_type"], ol.get("grade", "A"), ol.get("colour"))
        want[k] = want.get(k, 0) + q
    if not lines:
        raise HTTPException(400, "A delivery needs at least one line")
    if not skip_checks:
        await check_stock(node_id, cfg, want, freed)

    value = dn_value_incl(lines, order, cfg)
    release = None
    collect_amount = None
    if order["payment_basis"] == "upfront" and not order.get("legacy"):
        if order.get("pay_on_delivery"):
            paid = await order_paid(order["_id"], session)
            pending = 0.0
            async for other in db.delivery_notes().find({"order_id": order["_id"], **ACTIVE,
                                                         "collection": None}, session=session):
                pending += other.get("collect_amount") or 0.0
            balance = r2(order["total"] - paid - pending)
            # the last delivery collects whatever is left, charge lines included
            last = all(ol["qty"] - ol.get("qty_cancelled", 0) - disp.get(ol["line_id"], 0)
                       - sum(l["quantity"] for l in lines if l["order_line_id"] == ol["line_id"]) <= 0
                       for ol in order["lines"] if is_tank(ol))
            collect_amount = r2(max(0.0, balance if last else min(value, balance)))
        else:
            paid = await order_paid(order["_id"], session)
            out_value = 0.0
            async for other in db.delivery_notes().find({"order_id": order["_id"], **ACTIVE}, session=session):
                out_value += dn_value_incl(other["lines"], order, cfg)
            if r2(out_value + value) > paid + PAY_TOL and not skip_checks:
                if not raw.get("release_reason"):
                    raise HTTPException(
                        400, f"Paid R{paid:,.2f}; this delivery takes the order to "
                             f"R{out_value + value:,.2f} delivered. Take payment first, or a partner "
                             "admin releases it with a reason.")
                if user.get("role") != "partner_admin":
                    raise HTTPException(403, "Only a partner admin releases a delivery beyond what is paid")
                release = {"by": user["email"], "reason": raw["release_reason"],
                           "paid": paid, "value_after": r2(out_value + value)}

    site = raw.get("site") or order.get("site") or {}
    if raw.get("site_id"):
        cust = await db.customers().find_one({"_id": order["customer_id"]}, session=session)
        site = site_of(cust or {}, raw["site_id"]) or site
    totals = pricing.totals([pricing.price_line(by_id[l["order_line_id"]], cfg, l["quantity"])
                             for l in lines], cfg)
    dn_no = await number(node, "dn", "DN", session)
    dn_id = uuid4().hex
    d = raw.get("date") or raw.get("planned_date") or today()
    cust_name = order["customer_name"]
    details = "\n".join(x for x in ((site or {}).get("address"), (site or {}).get("town")) if x)
    dn = {
        "_id": dn_id, "node_id": node_id, "dn_number": dn_no, "date": d,
        "client_name": cust_name, "client_details": raw.get("client_details", details),
        "customer_id": order.get("customer_id"), "order_id": order["_id"],
        "order_number": order.get("order_number"), "lines": lines,
        "subtotal": totals["subtotal"], "vat_rate": totals["vat_rate"], "vat": totals["vat"],
        "total": totals["total"], "amount_paid": 0.0, "status": "unpaid",
        "site": site, "planned_date": raw.get("planned_date") or d,
        "driver": raw.get("driver"), "vehicle_reg": raw.get("vehicle_reg", ""),
        "delivery_status": "scheduled", "pod": None,
        "collect_amount": collect_amount, "collection": None,
        "driver_token": driver_token(), "release": release,
        "created_by": user["email"], "created_at": now(), **(extra or {}),
    }
    dn.update(recon.compute_split(dn, cfg, dn["total"]))
    for ln in lines:
        await db.finished_goods().insert_one({
            "_id": uuid4().hex, "node_id": node_id, "date": d, "tank_type": ln["tank_type"],
            "colour": ln.get("colour", ""), "grade": ln["grade"], "type": "dispatched",
            "quantity": ln["quantity"], "dn_number": dn_no, "reference": dn_id,
            "created_at": now()}, session=session)
    await db.delivery_notes().insert_one(dn, session=session)
    if release:
        await recon.raise_flag(node_id, "released_unpaid",
                               f"{dn_no} on order {order.get('order_number')} released with R{release['paid']:,.2f} "
                               f"paid against R{release['value_after']:,.2f} delivered. Reason: {release['reason']}",
                               {"dn_id": dn_id, "order_id": order["_id"]}, d)
    return dn


async def store_dn_pdf(node: dict, cfg: dict, dn: dict, session=None):
    import partner_pdf
    pdf = partner_pdf.delivery_note(node, cfg, dn, await _dn_context(dn, session))
    await db.delivery_notes().update_one({"_id": dn["_id"]},
                                         {"$set": {"content_b64": base64.b64encode(pdf).decode(),
                                                   "pdf_url": f"/api/delivery-notes/{dn['_id']}/pdf"}},
                                         session=session)


async def _dn_context(dn: dict, session=None) -> dict:
    """Part-delivery numbering: 'Part delivery 2 of order MX-ORD-0042' and what is still to come."""
    if not dn.get("order_id"):
        return {}
    order = await db.orders().find_one({"_id": dn["order_id"]}, session=session)
    if not order:
        return {}
    dns = [d async for d in db.delivery_notes().find({"order_id": order["_id"], **ACTIVE},
                                                      session=session).sort("created_at", 1)]
    disp = {}
    for d in dns:
        for ln in d["lines"]:
            disp[ln.get("order_line_id")] = disp.get(ln.get("order_line_id"), 0) + ln["quantity"]
    to_come = sum(max(0, ol["qty"] - ol.get("qty_cancelled", 0) - disp.get(ol["line_id"], 0))
                  for ol in order["lines"] if is_tank(ol))
    idx = next((i for i, d in enumerate(dns) if d["_id"] == dn["_id"]), len(dns) - 1) + 1
    total_tanks = sum(ol["qty"] for ol in order["lines"] if is_tank(ol))
    this = sum(ln["quantity"] for ln in dn["lines"])
    part = len(dns) > 1 or this < total_tanks or to_come > 0
    return {"order": order, "part_index": idx if part else None, "to_come": to_come}


# ============================== order summary ============================== #

def summarise(order: dict, dns: list[dict], invoices: list[dict], cfg: dict,
              pay_links: list[dict] | None = None, on: str | None = None) -> dict:
    """Everything the order screen and the dashboard board show for one order, derived."""
    on = on or today()
    active_dns = [d for d in dns if not d.get("void")]
    active_inv = [i for i in invoices if not i.get("void")]
    disp: dict = {}
    delivered: dict = {}
    for d in active_dns:
        done = d.get("delivery_status", "delivered") == "delivered" or d.get("legacy")
        for ln in d["lines"]:
            lid = ln.get("order_line_id")
            disp[lid] = disp.get(lid, 0) + ln["quantity"]
            if done:
                delivered[lid] = delivered.get(lid, 0) + ln["quantity"]
    invoiced: dict = {}
    for inv in active_inv:
        for ln in inv["lines"]:
            invoiced[ln.get("line_id")] = invoiced.get(ln.get("line_id"), 0) + pricing.qty_of(ln)
    lines = []
    tot_ordered = tot_net = tot_dispatched = tot_delivered = 0
    made_to_order = False
    for ol in order["lines"]:
        if not is_tank(ol):
            lines.append({**ol, "qty_ordered": ol["qty"], "qty_invoiced": invoiced.get(ol["line_id"], 0)})
            continue
        net = ol["qty"] - ol.get("qty_cancelled", 0)
        rem = max(0, net - disp.get(ol["line_id"], 0))
        lines.append({**ol, "qty_ordered": ol["qty"], "qty_dispatched": disp.get(ol["line_id"], 0),
                      "qty_delivered": delivered.get(ol["line_id"], 0),
                      "qty_invoiced": invoiced.get(ol["line_id"], 0), "qty_remaining": rem})
        tot_ordered += ol["qty"]; tot_net += net
        tot_dispatched += disp.get(ol["line_id"], 0); tot_delivered += delivered.get(ol["line_id"], 0)
        if ol.get("made_to_order") and rem > 0:
            made_to_order = True
    remaining = max(0, tot_net - tot_dispatched)
    # value of the order net of cancelled quantities
    net_value = r2(sum(pricing.price_line(ol, cfg, ol["qty"] - ol.get("qty_cancelled", 0))["line_total"]
                       for ol in order["lines"]))
    inv_total = r2(sum(i["total"] for i in active_inv))
    paid = r2(sum(i.get("amount_paid", 0.0) for i in active_inv))
    balance = r2(inv_total - paid)
    overdue = any(i.get("due_date") and i["due_date"] < on and i["total"] - i.get("amount_paid", 0) > PAY_TOL
                  for i in active_inv)

    # delivery status
    statuses = {d.get("delivery_status", "delivered") for d in active_dns if not d.get("legacy")}
    if order.get("status") == "cancelled":
        dstat = "cancelled"
    elif tot_net == 0 and tot_ordered > 0:
        dstat = "cancelled"
    elif tot_ordered == 0 or (remaining == 0 and tot_delivered >= tot_net):
        dstat = "delivered"
    elif "out_for_delivery" in statuses:
        dstat = "out_for_delivery"
    elif statuses & {"scheduled", "failed"}:
        dstat = "scheduled"
    elif tot_delivered > 0:
        dstat = "part_delivered"
    else:
        dstat = "not_delivered"

    # payment status
    if not active_inv:
        pstat = "not_invoiced"
    elif paid <= 0.004:
        pstat = "unpaid"
    elif paid + PAY_TOL < inv_total:
        pstat = "part_paid"
    else:
        pstat = "paid"
    cod = bool(order.get("pay_on_delivery"))
    cod_outstanding = cod and pstat != "paid"

    fully_paid = paid + PAY_TOL >= net_value
    if order.get("status") == "cancelled":
        ostat = "cancelled"
    elif dstat in ("delivered", "cancelled") and fully_paid:
        ostat = "complete"
    else:
        ostat = "open"

    # next action
    link_sent = any(pl.get("sent_at") for pl in (pay_links or []))
    delivered_uninvoiced = any(l.get("qty_delivered", 0) > l.get("qty_invoiced", 0) for l in lines if is_tank(l))
    dispatched_value = r2(sum(dn_value_incl(d["lines"], order, cfg) for d in active_dns))
    basis = order["payment_basis"]
    if ostat in ("complete", "cancelled"):
        nxt = ("complete", "Complete")
    elif basis == "upfront" and not cod and paid <= 0.004 and not link_sent and remaining > 0:
        nxt = ("send_pay_link", "Send pay link")
    elif basis == "upfront" and not cod and remaining > 0 and dispatched_value + PAY_TOL >= paid:
        nxt = ("awaiting_payment", "Awaiting payment")
    elif remaining > 0 and dstat not in ("out_for_delivery",):
        nxt = ("schedule_delivery", "Schedule delivery")
    elif dstat == "out_for_delivery" and cod:
        nxt = ("collect_on_delivery", "Collect on delivery")
    elif dstat in ("out_for_delivery", "scheduled"):
        nxt = ("out_for_delivery", "Out for delivery") if dstat == "out_for_delivery" else ("dispatch", "Send to driver")
    elif basis == "terms" and delivered_uninvoiced:
        nxt = ("invoice_delivery", "Invoice delivery")
    elif overdue:
        nxt = ("overdue", "Overdue: follow up")
    elif balance > PAY_TOL or not fully_paid:
        nxt = ("awaiting_payment_terms", "Awaiting payment (terms)") if basis == "terms" else ("awaiting_payment", "Awaiting payment")
    else:
        nxt = ("complete", "Complete")
    if overdue and nxt[0] in ("awaiting_payment", "awaiting_payment_terms"):
        nxt = ("overdue", "Overdue: follow up")

    methods = sorted({m for i in active_inv for m in i.get("_methods", [])})
    age = (date_cls.fromisoformat(on) - date_cls.fromisoformat(order["date"])).days
    return {
        "lines": lines, "delivery_status": dstat, "payment_status": pstat, "overdue": overdue,
        "order_status": ostat, "cod_outstanding": bool(cod_outstanding),
        "value": net_value, "invoiced": inv_total, "paid": paid, "balance": r2(net_value - paid),
        "invoice_balance": balance, "qty_ordered": tot_net, "qty_delivered": tot_delivered,
        "qty_remaining": remaining, "delivered_label": f"{tot_delivered} of {tot_net} delivered",
        "made_to_order": made_to_order, "next_action": {"code": nxt[0], "label": nxt[1]},
        "methods": methods, "age_days": age, "dispatched_value": dispatched_value,
        "releasable_value": r2(max(0.0, paid - dispatched_value)) if basis == "upfront" and not cod else None,
    }


async def load_order_bundle(node_id: str, order_ids: list[str] | None = None) -> dict:
    """Orders with their DNs, invoices, payments' methods and pay links, in four queries."""
    filt: dict = {"node_id": node_id}
    if order_ids is not None:
        filt["_id"] = {"$in": order_ids}
    orders = [o async for o in db.orders().find(filt)]
    ids = [o["_id"] for o in orders]
    dns: dict = {}
    async for d in db.delivery_notes().find({"order_id": {"$in": ids}}, {"content_b64": 0}):
        dns.setdefault(d["order_id"], []).append(d)
    invs: dict = {}
    inv_by_id: dict = {}
    async for i in db.invoices().find({"order_id": {"$in": ids}}):
        i["_methods"] = []
        invs.setdefault(i["order_id"], []).append(i)
        inv_by_id[i["_id"]] = i
    if inv_by_id:
        async for p in db.payments().find({"node_id": node_id, "allocations.invoice_id": {"$in": list(inv_by_id)}, **ACTIVE},
                                          {"allocations": 1, "method": 1, "source": 1}):
            m = p.get("method", "eft") + ("_" + p["source"] if p.get("source") else "")
            for a in p.get("allocations") or []:
                if a["invoice_id"] in inv_by_id and m not in inv_by_id[a["invoice_id"]]["_methods"]:
                    inv_by_id[a["invoice_id"]]["_methods"].append(m)
    links: dict = {}
    async for pl in db.pay_links().find({"node_id": node_id, "invoice_id": {"$in": list(inv_by_id)}}):
        links.setdefault(inv_by_id[pl["invoice_id"]]["order_id"], []).append(pl)
    return {"orders": orders, "dns": dns, "invoices": invs, "links": links}


async def order_view(node_id: str, order: dict) -> dict:
    cfg = await get_cfg(node_id)
    b = await load_order_bundle(node_id, [order["_id"]])
    dns = b["dns"].get(order["_id"], [])
    invs = b["invoices"].get(order["_id"], [])
    s = summarise(order, dns, invs, cfg, b["links"].get(order["_id"]))
    pays = []
    if invs:
        async for p in db.payments().find({"allocations.invoice_id": {"$in": [i["_id"] for i in invs]}}):
            pays.append(p)
    return {**order, **s, "order_lines": order["lines"], "lines": s["lines"],
            "delivery_notes": sorted(dns, key=lambda d: d["created_at"]),
            "invoices": sorted(invs, key=lambda i: i["created_at"]),
            "payments": sorted(pays, key=lambda p: (p["date"], p["created_at"]))}


async def refresh_order_status(order_id: str, session=None):
    """Cache the derived order status on the order (so 'open' filters are cheap)."""
    o = await db.orders().find_one({"_id": order_id}, session=session)
    if not o or o.get("status") == "cancelled":
        return
    cfg = await get_cfg(o["node_id"])
    dns = [d async for d in db.delivery_notes().find({"order_id": order_id}, {"content_b64": 0}, session=session)]
    invs = [i async for i in db.invoices().find({"order_id": order_id}, session=session)]
    s = summarise(o, dns, invs, cfg)
    await db.orders().update_one({"_id": order_id}, {"$set": {"status": s["order_status"]}}, session=session)


# ============================== quotes ============================== #

async def create_quote(node: dict, cfg: dict, customer: dict, raw: dict, user: dict) -> dict:
    node_id = node["node_id"]
    tier = customer.get("price_tier", "retail")
    version = await pricing.current_version(node_id, tier)
    lines = await build_lines(cfg, node_id, raw.get("lines") or [], tier, version)
    d = raw.get("date") or today()
    q = {"_id": uuid4().hex, "node_id": node_id,
         "quote_number": await number(node, "quo", "QUO"), "rev": 1, "date": d,
         "customer_id": customer["_id"], "customer_name": customer["name"],
         "site": site_of(customer, raw.get("site_id")) if raw.get("site_id") else None,
         "lines": lines, **pricing.totals(lines, cfg),
         "valid_until": raw.get("valid_until") or add_days(d, int(cfg.get("quote_validity_days", 14))),
         "notes": raw.get("notes", ""), "status": "draft", "price_tier": tier,
         "price_list_version_id": version["_id"] if version else None,
         "pay_on_delivery_offered": bool(raw.get("pay_on_delivery_offered", True)),
         "revisions": [], "share_token": driver_token(),
         "created_by": user["email"], "created_at": now()}
    await db.quotes().insert_one(q)
    await audit.log(user, node_id, "create", "quotes", q["_id"], after=q)
    return q


async def revise_quote(node_id: str, quote: dict, cfg: dict, raw: dict, user: dict) -> dict:
    """Editing a sent quote keeps the number and bumps the revision; earlier revisions stay
    read-only on the quote. Prices stay locked to the version the quote was made on."""
    if quote["status"] in ("accepted",):
        raise HTTPException(400, "An accepted quote cannot be edited")
    version = await db.price_lists().find_one({"_id": quote.get("price_list_version_id")}) \
        if quote.get("price_list_version_id") else None
    lines = await build_lines(cfg, node_id, raw.get("lines") or quote["lines"], quote["price_tier"], version)
    snap = {k: quote.get(k) for k in ("rev", "date", "lines", "subtotal", "vat", "total",
                                      "valid_until", "notes", "status", "site")}
    customer = await get_customer(node_id, quote["customer_id"])
    upd = {"lines": lines, **pricing.totals(lines, cfg), "notes": raw.get("notes", quote["notes"]),
           "valid_until": raw.get("valid_until") or quote["valid_until"],
           "site": site_of(customer, raw["site_id"]) if raw.get("site_id") else quote.get("site")}
    if quote["status"] != "draft":
        upd.update(rev=quote["rev"] + 1, status="draft", date=today(),
                   valid_until=raw.get("valid_until") or add_days(today(), int(cfg.get("quote_validity_days", 14))))
        await db.quotes().update_one({"_id": quote["_id"]}, {"$set": upd, "$push": {"revisions": snap}})
    else:
        await db.quotes().update_one({"_id": quote["_id"]}, {"$set": upd})
    await audit.log(user, node_id, "edit", "quotes", quote["_id"], before=snap, after=upd)
    return await db.quotes().find_one({"_id": quote["_id"]})


async def set_quote_status(node: dict, quote: dict, status: str, user: dict, reason: str = "",
                           order_raw: dict | None = None) -> dict:
    node_id = node["node_id"]
    allowed = {"draft": {"sent", "declined"}, "sent": {"accepted", "declined", "sent"},
               "expired": set(), "declined": set(), "accepted": set()}
    if status not in allowed.get(quote["status"], set()):
        raise HTTPException(400, f"A {quote['status']} quote cannot be marked {status}")
    upd: dict = {"status": status, f"{status}_at": now()}
    order = None
    if status == "declined":
        upd["declined_reason"] = reason
    if status == "accepted":
        cfg = await get_cfg(node_id)
        customer = await get_customer(node_id, quote["customer_id"])
        raw = {**(order_raw or {}), "date": today()}
        if quote.get("site") and not raw.get("site_id"):
            raw["site_id"] = None
        async with corrections.txn() as s:
            order = await create_order(node, cfg, customer, raw, user, quote=quote, session=s)
        upd["order_id"] = order["_id"]
        upd["order_number"] = order["order_number"]
    await db.quotes().update_one({"_id": quote["_id"]}, {"$set": upd})
    await audit.log(user, node_id, status, "quotes", quote["_id"], before={"status": quote["status"]}, after=upd)
    return {**quote, **upd, "order": order}


async def sweep_quotes(node_id: str) -> list[str]:
    expired = []
    async for q in db.quotes().find({"node_id": node_id, "status": {"$in": ["draft", "sent"]},
                                     "valid_until": {"$lt": today()}}):
        await db.quotes().update_one({"_id": q["_id"]}, {"$set": {"status": "expired", "expired_at": now()}})
        expired.append(q["_id"])
    return expired


# ============================== order changes ============================== #

async def edit_order_lines(node: dict, order: dict, raw_lines: list[dict], user: dict, reason: str) -> dict:
    """Allowed above the delivered quantity. An upfront order's invoice is voided and reissued if
    its value changes; payments already received move to the new invoice."""
    node_id = node["node_id"]
    cfg = await get_cfg(node_id)
    version = await db.price_lists().find_one({"_id": order.get("price_list_version_id")}) \
        if order.get("price_list_version_id") else None
    lines = await build_lines(cfg, node_id, raw_lines, order["price_tier"], version)
    disp = await dispatched_by_line(order["_id"])
    keep = {ln["line_id"] for ln in lines}
    for lid, q in disp.items():
        nl = next((ln for ln in lines if ln["line_id"] == lid), None)
        if lid not in keep or (nl and nl["qty"] - nl.get("qty_cancelled", 0) < q):
            raise HTTPException(400, "A line cannot go below what has already been dispatched")
    upd = {"lines": lines, **pricing.totals(lines, cfg)}
    async with corrections.txn() as s:
        await db.orders().update_one({"_id": order["_id"]}, {"$set": upd}, session=s)
        if order["payment_basis"] == "upfront" and abs(upd["total"] - order["total"]) > 0.004:
            await reissue_order_invoice(node, cfg, {**order, **upd}, user, reason, s)
        await audit.log(user, node_id, "edit", "orders", order["_id"],
                        before={"record": order, "reason": reason}, after=upd, session=s)
    await refresh_order_status(order["_id"])
    return {**order, **upd}


async def reissue_order_invoice(node: dict, cfg: dict, order: dict, user: dict, reason: str, session=None):
    old = await db.invoices().find_one({"order_id": order["_id"], "dn_id": None, **ACTIVE}, session=session)
    new = await create_invoice(node, cfg, order, order["lines"], user, session=session,
                               extra={"replaces": (old or {}).get("display_number")})
    if old:
        await corrections.mark_void("invoices", old["_id"], user, reason, session=session,
                                    extra={"superseded_by": new["display_number"], "status": "void"})
        async for p in db.payments().find({"allocations.invoice_id": old["_id"], **ACTIVE}, session=session):
            allocs = [{**a, "invoice_id": new["_id"]} if a["invoice_id"] == old["_id"] else a
                      for a in p["allocations"]]
            await db.payments().update_one({"_id": p["_id"]}, {"$set": {"allocations": allocs}}, session=session)
        await refresh_invoice(new["_id"], session)
    return new


async def cancel_remainder(node: dict, order: dict, user: dict, reason: str) -> dict:
    """Cancel undelivered quantities. On an upfront order paid beyond the value now ordered,
    raise refund_due for Fenix; the refund itself is done in Xero by hand in v1."""
    node_id = node["node_id"]
    cfg = await get_cfg(node_id)
    disp = await dispatched_by_line(order["_id"])
    lines = []
    for ol in order["lines"]:
        if is_tank(ol):
            ol = {**ol, "qty_cancelled": ol["qty"] - disp.get(ol["line_id"], 0)}
        lines.append(ol)
    upd: dict = {"lines": lines, "cancelled_reason": reason, "cancelled_by": user["email"]}
    if not any(disp.values()):
        upd["status"] = "cancelled"
    await db.orders().update_one({"_id": order["_id"]}, {"$set": upd})
    await audit.log(user, node_id, "cancel", "orders", order["_id"],
                    before={"record": order, "reason": reason}, after=upd)
    o = {**order, **upd}
    net_value = r2(sum(pricing.price_line(ol, cfg, ol["qty"] - ol.get("qty_cancelled", 0))["line_total"]
                       for ol in lines))
    paid = await order_paid(order["_id"])
    if order["payment_basis"] == "upfront" and paid > net_value + PAY_TOL:
        due = r2(paid - net_value)
        await db.orders().update_one({"_id": order["_id"]}, {"$set": {"refund_due": due}})
        o["refund_due"] = due
        await recon.raise_flag(node_id, "refund_due",
                               f"Order {order['order_number']}: R{paid:,.2f} paid against R{net_value:,.2f} "
                               f"now ordered after cancelling the remainder. Refund due R{due:,.2f}.",
                               {"order_id": order["_id"]}, today())
    await refresh_order_status(order["_id"])
    return o


async def invoice_dn(node: dict, cfg: dict, dn: dict, user: dict, session=None) -> dict:
    """Terms: one invoice per delivery note, covering exactly that DN's lines."""
    if await db.invoices().find_one({"dn_id": dn["_id"], **ACTIVE}, session=session):
        raise HTTPException(400, f"{dn['dn_number']} is already invoiced")
    order = await db.orders().find_one({"_id": dn["order_id"]}, session=session)
    if not order:
        raise HTTPException(400, "Delivery is not on an order")
    if order["payment_basis"] != "terms":
        raise HTTPException(400, "Upfront orders carry one invoice for the full order")
    by_id = {ln["line_id"]: ln for ln in order["lines"]}
    lines = [pricing.price_line({**by_id[l["order_line_id"]], "qty": l["quantity"]}, cfg)
             for l in dn["lines"] if l.get("order_line_id") in by_id]
    inv = await create_invoice(node, cfg, order, lines, user, dn=dn, session=session)
    await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {"invoice_id": inv["_id"]}}, session=session)
    return inv


async def void_invoice(invoice_id: str, user: dict, reason: str, reissue: bool = False) -> dict:
    inv = await db.invoices().find_one({"_id": invoice_id})
    if not inv or inv.get("void"):
        raise HTTPException(400, "Invoice not found or already void")
    if await db.payments().find_one({"allocations.invoice_id": invoice_id, **ACTIVE}) and not reissue:
        raise HTTPException(400, "Invoice has payments allocated. Reissue it to move them, or reallocate first.")
    node = await get_node(inv["node_id"])
    cfg = await get_cfg(inv["node_id"])
    order = await db.orders().find_one({"_id": inv["order_id"]})
    new = None
    async with corrections.txn() as s:
        if reissue:
            dn = await db.delivery_notes().find_one({"_id": inv["dn_id"]}, session=s) if inv.get("dn_id") else None
            new = await create_invoice(node, cfg, order, inv["lines"], user, dn=dn, session=s,
                                       extra={"replaces": inv["display_number"]})
            async for p in db.payments().find({"allocations.invoice_id": invoice_id, **ACTIVE}, session=s):
                allocs = [{**a, "invoice_id": new["_id"]} if a["invoice_id"] == invoice_id else a
                          for a in p["allocations"]]
                await db.payments().update_one({"_id": p["_id"]}, {"$set": {"allocations": allocs}}, session=s)
            if dn:
                await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {"invoice_id": new["_id"]}}, session=s)
        await corrections.mark_void("invoices", invoice_id, user, reason, session=s,
                                    extra={"status": "void", **({"superseded_by": new["display_number"]} if new else {})})
        if new:
            await refresh_invoice(new["_id"], s)
    if inv.get("xero", {}).get("invoice_id"):
        await db.invoices().update_one({"_id": invoice_id}, {"$set": {"xero.void_pending": True}})
    await refresh_order_status(inv["order_id"])
    return {"voided": inv["display_number"], "new": new}


# ============================== deliveries: status, POD, collection ============================== #

async def dn_or_404(node_id: str, dn_id: str) -> dict:
    dn = await db.delivery_notes().find_one({"_id": dn_id, "node_id": node_id}, {"content_b64": 0})
    if not dn:
        raise HTTPException(404, "Delivery note not found")
    if dn.get("void"):
        raise HTTPException(400, f"{dn['dn_number']} is void")
    return dn


async def set_delivery_status(dn: dict, status: str, actor: dict, reason: str = "",
                              planned_date: str | None = None) -> dict:
    cur = dn.get("delivery_status", "scheduled")
    ok = {"scheduled": {"out_for_delivery", "failed"}, "out_for_delivery": {"failed"},
          "failed": {"scheduled"}, "delivered": set()}
    if status == "delivered":
        raise HTTPException(400, "Delivered is set by capturing proof of delivery")
    if status not in ok.get(cur, set()):
        raise HTTPException(400, f"A delivery that is {cur.replace('_', ' ')} cannot go to {status.replace('_', ' ')}")
    upd: dict = {"delivery_status": status}
    if status == "out_for_delivery":
        upd["out_at"] = now()
    if status == "failed":
        if not reason.strip():
            raise HTTPException(400, "Say why the delivery could not be made")
        upd.update(failed_reason=reason, failed_at=now())
    if status == "scheduled":
        upd["planned_date"] = planned_date or today()
    await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": upd})
    await audit.log(actor, dn["node_id"], f"delivery_{status}", "delivery_notes", dn["_id"],
                    before={"delivery_status": cur}, after=upd)
    if dn.get("order_id"):
        await refresh_order_status(dn["order_id"])
    return {**dn, **upd}


async def record_collection(node: dict, dn: dict, raw: dict, actor: dict) -> dict:
    """What the driver collected on a pay-on-delivery note. Cash lands in cash on hand under the
    driver; a card-machine payment is a Yoco payment with source machine; not collected needs a
    reason and raises cod_not_collected."""
    if dn.get("collect_amount") is None:
        raise HTTPException(400, "Nothing to collect on this delivery")
    if dn.get("collection"):
        raise HTTPException(400, "Collection already recorded")
    method = raw.get("method")
    node_id = node["node_id"]
    inv = await db.invoices().find_one({"order_id": dn["order_id"], "dn_id": None, **ACTIVE})
    coll: dict = {"method": method, "at": now(), "by": actor["email"]}
    if method in ("cash", "yoco_machine"):
        amount = r2(raw.get("amount") or 0)
        if amount <= 0:
            raise HTTPException(400, "Enter the amount collected")
        holder = {"type": "driver", "name": (dn.get("driver") or {}).get("name") or actor.get("name", ""),
                  "phone": (dn.get("driver") or {}).get("phone", "")} if actor.get("role") == "driver" else \
            {"type": "staff", "name": actor.get("name") or actor["email"]}
        p = await record_payment(node_id, {
            "amount": amount, "method": "cash" if method == "cash" else "yoco",
            "source": "machine" if method == "yoco_machine" else None,
            "reference": raw.get("reference", ""), "customer_id": dn.get("customer_id"),
            "invoice_id": inv["_id"] if inv else None, "holder": holder, "dn_id": dn["_id"],
            "date": today()}, actor)
        coll.update(amount=amount, payment_id=p["_id"], reference=raw.get("reference", ""))
        if amount + PAY_TOL < dn["collect_amount"]:
            await recon.raise_flag(node_id, "short_paid",
                                   f"{dn['dn_number']}: collected R{amount:,.2f} of R{dn['collect_amount']:,.2f} on delivery.",
                                   {"dn_id": dn["_id"], "payment_id": p["_id"]}, today())
    elif method == "yoco_link":
        coll.update(amount=r2(raw.get("amount") or 0), payment_id=raw.get("payment_id"))
    elif method == "not_collected":
        if not (raw.get("reason") or "").strip():
            raise HTTPException(400, "Say why nothing was collected")
        coll.update(amount=0.0, reason=raw["reason"])
        await recon.raise_flag(node_id, "cod_not_collected",
                               f"{dn['dn_number']} ({dn['client_name']}): R{dn['collect_amount']:,.2f} to collect "
                               f"on delivery was not collected. Reason: {raw['reason']}",
                               {"dn_id": dn["_id"], "order_id": dn.get("order_id")}, today())
    else:
        raise HTTPException(400, "Collection method must be cash, yoco_machine, yoco_link or not_collected")
    await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {"collection": coll}})
    await audit.log(actor, node_id, "collection", "delivery_notes", dn["_id"], after=coll)
    return coll


async def capture_pod(dn: dict, raw: dict, actor: dict, photo: bytes | None = None,
                      content_type: str = "image/jpeg") -> dict:
    """Proof of delivery: photo of the signed DN, receiver, time, GPS when the device allows.
    On a pay-on-delivery note, Delivered cannot complete until the collection is recorded."""
    if dn.get("collect_amount") is not None and not dn.get("collection"):
        raise HTTPException(400, "Record what was collected before marking delivered")
    if dn.get("delivery_status") == "delivered" and dn.get("pod"):
        raise HTTPException(400, "Proof of delivery already captured")
    file_id = None
    if photo:
        file_id = uuid4().hex
        await db.pod_files().insert_one({"_id": file_id, "node_id": dn["node_id"], "dn_id": dn["_id"],
                                         "content_type": content_type,
                                         "content_b64": base64.b64encode(photo).decode(),
                                         "created_at": now()})
    pod = {"receiver_name": raw.get("receiver_name", ""), "at": now(), "by": actor["email"],
           "lat": raw.get("lat"), "lng": raw.get("lng"), "photo_file_id": file_id,
           "after_the_fact": bool(raw.get("after_the_fact"))}
    upd = {"pod": pod, "delivery_status": "delivered", "delivered_at": now()}
    await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": upd})
    await audit.log(actor, dn["node_id"], "delivery_delivered", "delivery_notes", dn["_id"], after=upd)
    if dn.get("order_id"):
        await refresh_order_status(dn["order_id"])
    return {**dn, **upd}


# ============================== sweeps ============================== #

async def sweep_invoices_unpaid(node_id: str) -> list[str]:
    """R4 on invoices: unpaid or part paid past the due date."""
    raised = []
    async for inv in db.invoices().find({"node_id": node_id, **ACTIVE,
                                         "status": {"$in": ["unpaid", "part_paid"]},
                                         "due_date": {"$lt": today()}}):
        order = await db.orders().find_one({"_id": inv["order_id"]}) or {}
        if order.get("pay_on_delivery") and inv["dn_id"] is None and order.get("payment_basis") == "upfront":
            # COD invoices are chased through cod_not_collected, not R4
            undelivered = await db.delivery_notes().find_one({"order_id": order["_id"], **ACTIVE,
                                                              "collection": None})
            if undelivered or not await db.delivery_notes().find_one({"order_id": order["_id"], **ACTIVE}):
                continue
        if order.get("payment_basis") == "upfront" and not order.get("legacy") and not order.get("pay_on_delivery"):
            # pay-first: nothing has left unpaid unless released, which raised its own flag
            if not await db.delivery_notes().find_one({"order_id": order.get("_id"), **ACTIVE}):
                continue
        raised.append(await recon.raise_flag(
            node_id, "delivery_unpaid" if inv.get("legacy") else "invoice_unpaid",
            f"Invoice {inv['display_number']} ({inv['date']}, {inv['customer_name']}, "
            f"R{inv['total']:,.2f}) is {inv['status'].replace('_', ' ')} past its due date {inv['due_date']}.",
            {"delivery_id": inv["dn_id"]} if inv.get("legacy") else {"invoice_id": inv["_id"]}, inv["date"]))
    return raised


def _grace_cutoff() -> str:
    """R3 grace: a delivery must be invoiced by the end of the next SAST day."""
    return add_days(today(), -1)


async def sweep_dn_not_invoiced(node_id: str) -> list[str]:
    """R3 rewritten for part deliveries: per order tank line, delivered must not exceed invoiced
    once the grace has passed. Breach flags the delivery note that took it over."""
    raised = []
    cutoff = _grace_cutoff()
    async for order in db.orders().find({"node_id": node_id, "legacy": {"$ne": True}}):
        invoiced: dict = {}
        async for inv in db.invoices().find({"order_id": order["_id"], **ACTIVE}):
            for ln in inv["lines"]:
                invoiced[ln.get("line_id")] = invoiced.get(ln.get("line_id"), 0) + pricing.qty_of(ln)
        running: dict = {}
        async for dn in db.delivery_notes().find({"order_id": order["_id"], **ACTIVE},
                                                 {"content_b64": 0}).sort("created_at", 1):
            breach = False
            for ln in dn["lines"]:
                lid = ln.get("order_line_id")
                running[lid] = running.get(lid, 0) + ln["quantity"]
                if running[lid] > invoiced.get(lid, 0):
                    breach = True
            if breach and dn["date"] < cutoff:
                raised.append(await recon.raise_flag(
                    node_id, "dn_not_invoiced",
                    f"{dn['dn_number']} ({dn['date']}, {dn['client_name']}) delivered more than is invoiced "
                    f"on order {order.get('order_number')}.",
                    {"dn_id": dn["_id"], "order_id": order["_id"]}, dn["date"]))
    return raised


async def sweep_unallocated(node_id: str) -> list[str]:
    raised = []
    async for p in db.payments().find({"node_id": node_id, "unallocated": {"$gt": 0.004}, **ACTIVE}):
        raised.append(await recon.raise_flag(
            node_id, "payment_unallocated",
            f"Payment of R{p['amount']:,.2f} on {p['date']} (ref '{p.get('reference', '')}') has "
            f"R{p['unallocated']:,.2f} not allocated to an invoice.", {"payment_id": p["_id"]}, p["date"]))
    # Brief 1 payments keyed before allocation existed
    async for p in db.payments().find({"node_id": node_id, "status": "unmatched",
                                       "allocations": {"$exists": False}, **ACTIVE}):
        raised.append(await recon.raise_flag(
            node_id, "payment_unallocated",
            f"Payment of R{p['amount']:,.2f} on {p['date']} (ref '{p.get('bank_reference', '')}') "
            "is not allocated to an invoice.", {"payment_id": p["_id"]}, p["date"]))
    return raised


async def sweep_cash(node_id: str) -> list[str]:
    cfg = await get_cfg(node_id)
    days = int(cfg.get("cash_banking_days", 2))
    cutoff = add_days(today(), -days)
    raised = []
    async for p in db.payments().find({"node_id": node_id, "method": "cash", "deposit_id": None,
                                       "date": {"$lt": cutoff}, **ACTIVE}):
        h = (p.get("holder") or {}).get("name") or p.get("captured_by")
        raised.append(await recon.raise_flag(
            node_id, "cash_not_banked",
            f"Cash of R{p['amount']:,.2f} received {p['date']} (held by {h}) is not banked after {days} days.",
            {"payment_id": p["_id"]}, p["date"]))
    return raised
