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


async def run() -> list:
    return [{r["key"]: await delete_sale(r["key"], r["slug"], r["invoice"], r.get("customer_contains", ""))}
            for r in REQUESTS]
