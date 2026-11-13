"""One-off removals of a whole sale, asked for by Werner, run once on startup.

delete_sale removes everything that belongs to one sale so the partner can capture it again from
scratch: the proforma (quote), the order, its invoices, delivery notes and their stock
movements, payments made against them, pay links, share links, POD files records and flags.
The customer stays. Nothing is lost: every removed document is copied into deleted_backup
first, under one key, and the key makes the run idempotent.

It refuses (changes nothing, logs why) when anything has reached Xero, when cash from it has
been banked, or when a payment also pays something outside this sale.
"""
from __future__ import annotations

from datetime import datetime

import audit
import corrections
import db

SYSTEM = {"email": "system:cleanup", "role": "system"}

# Werner, 9 Nov 2026: "delete it completely so he can start it from scratch" (Empire, MX-INV-0008)
REQUESTS = [{"key": "delete-sale-makx-MX-INV-0008", "slug": "makx", "invoice": "MX-INV-0008",
             "customer_contains": "empire"}]


def backups():
    return db.db()["deleted_backup"]


async def delete_sale(key: str, slug: str, invoice_number: str, customer_contains: str = "") -> dict:
    if await backups().find_one({"_id": key}):
        return {"skipped": "already done"}
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {"skipped": f"no node {slug}"}
    nid = node["node_id"]
    inv = await db.invoices().find_one({"node_id": nid, "display_number": invoice_number})
    if not inv:
        return {"skipped": f"{invoice_number} not found"}
    order = await db.orders().find_one({"_id": inv.get("order_id")}) if inv.get("order_id") else None
    if not order:
        return {"refused": f"{invoice_number} has no order"}
    name = (order.get("customer_name") or inv.get("customer_name") or "").lower()
    if customer_contains and customer_contains.lower() not in name:
        return {"refused": f"customer is {name!r}, expected {customer_contains!r}"}

    oid = order["_id"]
    quote = await db.quotes().find_one({"_id": order["quote_id"]}) if order.get("quote_id") else None
    invs = [i async for i in db.invoices().find({"order_id": oid})]
    inv_ids = {i["_id"] for i in invs}
    dns = [d async for d in db.delivery_notes().find({"order_id": oid})]
    dn_ids = {d["_id"] for d in dns}
    qid = quote["_id"] if quote else None
    pay_or = [{"allocations.invoice_id": {"$in": list(inv_ids)}}, {"dn_id": {"$in": list(dn_ids)}}]
    if qid:
        pay_or.append({"quote_id": qid})
    pays = [p async for p in db.payments().find({"node_id": nid, "$or": pay_or})]

    # refuse what cannot be undone here
    for i in invs:
        if (i.get("xero") or {}).get("invoice_id") or (i.get("xero") or {}).get("status") == "posted":
            return {"refused": f"{i.get('display_number')} is in Xero"}
    for p in pays:
        if p.get("posted_to_xero") or p.get("xero_payment_id"):
            return {"refused": f"payment {p['_id']} is in Xero"}
        if p.get("deposit_id"):
            return {"refused": f"cash payment {p['_id']} is already banked"}
        if any(a["invoice_id"] not in inv_ids for a in p.get("allocations") or []):
            return {"refused": f"payment {p['_id']} also pays another invoice"}

    rids = list(inv_ids | ({qid} if qid else set()))
    pl_or = [{"invoice_id": {"$in": list(inv_ids)}}, {"dn_id": {"$in": list(dn_ids)}}] + ([{"quote_id": qid}] if qid else [])
    flag_or = [{"references.order_id": oid}, {"references.dn_id": {"$in": list(dn_ids)}},
               {"references.invoice_id": {"$in": list(inv_ids)}}] + ([{"references.quote_id": qid}] if qid else [])
    found = {
        "orders": [order],
        "quotes": [quote] if quote else [],
        "invoices": invs,
        "delivery_notes": dns,
        "finished_goods_ledger": [f async for f in db.finished_goods().find(
            {"node_id": nid, "reference": {"$in": list(dn_ids)}})],
        "payments": pays,
        "pay_links": [pl async for pl in db.pay_links().find({"node_id": nid, "$or": pl_or})],
        "short_links": [s async for s in db.short_links().find({"rid": {"$in": rids}})],
        "pod_files": [f async for f in db.pod_files().find({"dn_id": {"$in": list(dn_ids)}})],
        "delivery_documents": [d async for d in db.delivery_documents().find({"delivery_id": {"$in": list(dn_ids)}})],
        "flags": [f async for f in db.flags().find({"node_id": nid, "$or": flag_or})],
    }
    colls = {"orders": db.orders(), "quotes": db.quotes(), "invoices": db.invoices(),
             "delivery_notes": db.delivery_notes(), "finished_goods_ledger": db.finished_goods(),
             "payments": db.payments(), "pay_links": db.pay_links(), "short_links": db.short_links(),
             "pod_files": db.pod_files(), "delivery_documents": db.delivery_documents(), "flags": db.flags()}
    summary = {k: len(v) for k, v in found.items() if v}
    summary["paid"] = round(sum(p["amount"] for p in pays), 2)
    summary["payment_methods"] = sorted({p.get("method", "") for p in pays})
    summary["numbers"] = [x for x in [quote and quote.get("quote_number"), order.get("order_number")]
                          + [i.get("display_number") for i in invs] + [d.get("dn_number") for d in dns] if x]
    async with corrections.txn() as s:
        await backups().insert_one({"_id": key, "node_id": nid, "at": datetime.utcnow(), "summary": summary,
                                    "docs": found}, session=s)
        for name_, docs in found.items():
            if docs:
                await colls[name_].delete_many({"_id": {"$in": [d["_id"] for d in docs]}}, session=s)
        await audit.log(SYSTEM, nid, "delete_sale", "orders", oid, before={"summary": summary}, session=s)
    return {"deleted": summary}


async def charges_onto_invoices(slug: str = "makx") -> dict:
    """Werner, 11 Nov 2026: terms invoices made before charges were included get their left-off
    charges added (MX-ORD-0022's R200 onto MX-INV-0026). Paid or Xero invoices are skipped."""
    import commerce
    import recon
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    cfg = await recon.get_config(node["node_id"])
    done, skipped = [], []
    async for o in db.orders().find({"node_id": node["node_id"], "payment_basis": "terms", "status": {"$ne": "cancelled"}}):
        if not await commerce.uninvoiced_charges(o, cfg):
            continue
        if not await db.invoices().find_one({"order_id": o["_id"], **corrections.ACTIVE}):
            continue          # nothing invoiced yet: the first invoice will carry them
        try:
            inv = await commerce.add_charges_to_invoice(node, cfg, o, SYSTEM)
            await commerce.refresh_order_status(o["_id"])
            done.append(f"{o.get('order_number')}: {inv.get('display_number')} now {inv['total']}")
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{o.get('order_number')}: {getattr(e, 'detail', e)}")
    return {"added": done, "skipped": skipped}


async def cash_was_eft(slug: str = "makx") -> dict:
    """Werner, 13 Nov 2026: with no EFT option in Record payment, Steven captured EFTs as cash,
    so cash on hand showed money he never held. Cash he recorded himself (not driver collections),
    not banked and not in Xero, becomes EFT waiting to be matched in Xero."""
    node = await db.nodes().find_one({"slug": slug})
    if not node:
        return {}
    done = []
    async for p in db.payments().find({"node_id": node["node_id"], "method": "cash", "deposit_id": None,
                                       "dn_id": None, "xero_payment_id": None, **corrections.ACTIVE}):
        h = p.get("holder") or {}
        if h.get("type") == "driver" or p.get("posted_to_xero") or p.get("xero_payment_ids"):
            continue
        ref = (p.get("reference") or "").strip()
        upd = {"method": "eft", "holder": None, "xero_status": "match_in_xero", "was_cash": True,
               "reference": ref or "EFT (captured as cash)"}
        await db.payments().update_one({"_id": p["_id"]}, {"$set": upd})
        await audit.log(SYSTEM, node["node_id"], "update", "payments", p["_id"],
                        before={"method": "cash", "holder": p.get("holder")},
                        after={**upd, "reason": "EFT captured as cash before EFT was offered"})
        done.append(f"{p.get('date')} R{p['amount']:,.2f} {ref}".strip())
    return {"changed": done}


async def cod_invoices_ahead(slug: str = "makx") -> list:
    """Read only: pay on delivery invoices made before delivery (before 13 Nov 2026), unpaid and
    not delivered yet. Listed so Werner can decide what to do with them; nothing is changed."""
    node = await db.nodes().find_one({"slug": slug})
    out = []
    if not node:
        return out
    async for o in db.orders().find({"node_id": node["node_id"], "pay_on_delivery": True, "status": "open"}):
        if await db.delivery_notes().find_one({"order_id": o["_id"], "delivery_status": "delivered", **corrections.ACTIVE}):
            continue
        async for i in db.invoices().find({"order_id": o["_id"], **corrections.ACTIVE}):
            if (i.get("amount_paid") or 0) > 0.004:
                continue
            x = i.get("xero") or {}
            out.append(f"{i.get('display_number')} {o.get('order_number')} {o.get('customer_name')} "
                       f"R{i['total']:,.2f} xero={'yes' if x.get('invoice_id') else 'no'}")
    return out


async def run() -> list:
    out = [{r["key"]: await delete_sale(r["key"], r["slug"], r["invoice"], r.get("customer_contains", ""))}
           for r in REQUESTS]
    out.append({"charges_onto_invoices": await charges_onto_invoices()})
    out.append({"cod_invoices_ahead": await cod_invoices_ahead()})
    if not await backups().find_one({"_id": "cash-was-eft-makx"}):
        r = await cash_was_eft()
        await backups().insert_one({"_id": "cash-was-eft-makx", "at": datetime.utcnow(), "summary": r, "docs": {}})
        out.append({"cash_was_eft": r})
    return out
