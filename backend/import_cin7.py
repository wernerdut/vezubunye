"""Import Makulu's customers and addresses from the Cin7 export into a partner node.

    python import_cin7.py makx_customers.csv makx_customer_addresses.csv            # dry run
    python import_cin7.py makx_customers.csv makx_customer_addresses.csv --apply

Idempotent: a customer already imported (matched on its Cin7 ids) is updated, not duplicated.
A customer keyed in by hand before the import is matched on phone, email or exact name and
gets the Cin7 ref, ids and history added; nothing already typed is overwritten.

- Refs (MKC-0001...) are kept; new customers continue the sequence, and customers keyed in
  before the import are renumbered after the last Cin7 ref.
- Phones are stored as E.164; the export already is.
- Everyone imports paying upfront: Cin7's "30 days" was its default, not real terms. Cin7's
  terms are kept on the customer for reference.
- Trade price tier only where Cin7 had them on the wholesale list (or the sister company).
  Names that only look like a trade business import as retail with `type_check` set.
- Customer data (names, phones, addresses) stays out of the repository: the CSVs are passed in.
"""
from __future__ import annotations

import asyncio
import csv
import io
import os
import re
import sys
from datetime import datetime
from uuid import uuid4

import audit
import customer_search
import db

NODE_ID = os.getenv("CIN7_NODE", "makulu")
PREFIX = "MKC"      # used when the file has no rows; otherwise taken from the refs in the file
SYSTEM = {"email": "import:cin7", "role": "system"}
CONFIRMED_TRADE = ("Cin7 wholesale price list", "Fenix Rising, sister company")


def read(src) -> list[dict]:
    """A file path, or the CSV text itself (an upload)."""
    if isinstance(src, (bytes, bytearray)):
        return list(csv.DictReader(io.StringIO(src.decode("utf-8-sig"))))
    with open(src, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


REQUIRED = {"customers": {"makx_ref", "name", "customer_type", "whatsapp_mobile", "cin7_ids"},
            "addresses": {"makx_ref", "address_type", "is_default", "line1", "city"}}


def joined(*parts: str) -> str:
    return ", ".join(p.strip() for p in parts if p and p.strip())


def num(v: str) -> float:
    try:
        return float(v or 0)
    except ValueError:
        return 0.0


def build(r: dict, addrs: list[dict], cfg: dict) -> dict:
    trade = r["customer_type"] == "Trade" and r["type_reason"] in CONFIRMED_TRADE
    deliveries = sorted([a for a in addrs if a["address_type"] == "delivery"], key=lambda a: a["is_default"] != "yes")
    sites = []
    for i, a in enumerate(deliveries):
        lat, lng = (float(a["pin_lat"]), float(a["pin_lng"])) if a["pin_lat"] and a["pin_lng"] else (None, None)
        sites.append({"site_id": uuid4().hex[:10],
                      "label": "Delivery" if len(deliveries) == 1 else f"Delivery {i + 1}",
                      "address": joined(a["line1"], a["line2"]), "town": a["city"] or a["province"],
                      "contact_name": "", "contact_phone": "", "directions": "", "maps_link": "",
                      "lat": lat, "lng": lng, "pinned": lat is not None, "source": a["source"],
                      "address_quality": a["address_quality"]})
    name = r["name"].strip()
    junk = bool(re.fullmatch(r"[0-9a-f-]{36}", name))
    last = r["last_order"] or ""
    return {
        "name": name, "contact_person": r["contact_name"], "phone": customer_search.phone_e164(r["whatsapp_mobile"]),
        "other_phone": customer_search.phone_e164(r["other_phone"]), "email": r["email"].strip(),
        "billing_address": joined(r["billing_line1"], r["billing_line2"], r["billing_city"], r["billing_province"],
                                  r["billing_postcode"]),
        "billing_city": r["billing_city"], "vat_number": r["vat_number"], "payment_basis": "upfront",
        "payment_terms_days": cfg.get("payment_terms_days", 30), "price_tier": "trade" if trade else "retail",
        "type_check": "Cin7 export suggests trade: check" if r["customer_type"] == "Trade" and not trade else None,
        "usually_collects": r["usually_collects"] == "yes",
        "marketing_consent": {"yes": True, "no": False}.get(r["marketing_consent"]),
        "active": not junk, "sites": sites, "ref": r["makx_ref"],
        "cin7_ids": r["cin7_ids"].split(), "last_order_date": last or None, "last_activity": last,
        "cin7": {"orders": int(num(r["orders"])), "first_order": r["first_order"] or None, "last_order": last or None,
                 "total_invoiced_incl_vat": num(r["total_invoiced_incl_vat_zar"]),
                 "unpaid_invoices_zar": num(r["unpaid_invoices_zar"]), "unpaid_invoices": r["unpaid_invoices"],
                 "unpaid_note": r["unpaid_note"], "payment_terms": r["cin7_payment_terms"],
                 "price_tier": r["cin7_price_tier"], "names_merged": r["cin7_names_merged"],
                 "shares_phone_or_email_with": r["shares_phone_or_email_with"],
                 "billing_address_quality": r["billing_address_quality"]},
    }


async def run(customers_csv, addresses_csv, apply: bool, node_id: str = NODE_ID, user: dict = SYSTEM) -> dict:
    cfg = await db.node_config().find_one({"node_id": node_id})
    node = await db.nodes().find_one({"node_id": node_id})
    if not cfg or not node:
        raise ValueError(f"Node {node_id} not found")
    rows = read(customers_csv)
    addresses = read(addresses_csv)
    for what, got in (("customers", rows), ("addresses", addresses)):
        missing = REQUIRED[what] - set(got[0] if got else [])
        if missing:
            raise ValueError(f"The {what} file is missing columns: {', '.join(sorted(missing))}")
    prefix = rows[0]["makx_ref"].rsplit("-", 1)[0] if rows else PREFIX
    by_ref: dict[str, list[dict]] = {}
    for a in addresses:
        by_ref.setdefault(a["makx_ref"], []).append(a)

    existing = [c async for c in db.customers().find({"node_id": node_id})]
    by_cin7 = {i: c for c in existing for i in c.get("cin7_ids") or []}
    manual = [c for c in existing if not c.get("cin7_ids")]
    stats = {"rows": len(rows), "new": 0, "updated": 0, "merged_with_existing": [], "inactive": 0,
             "trade": 0, "trade_to_check": 0, "with_delivery_sites": 0, "no_address": 0}
    claimed: set[str] = set()
    inserts = []
    for r in rows:
        doc = build(r, by_ref.get(r["makx_ref"], []), cfg)
        stats["inactive"] += not doc["active"]
        stats["trade"] += doc["price_tier"] == "trade"
        stats["trade_to_check"] += bool(doc["type_check"])
        stats["with_delivery_sites"] += bool(doc["sites"])
        stats["no_address"] += not doc["sites"] and not doc["billing_address"]
        hit = next((by_cin7[i] for i in doc["cin7_ids"] if i in by_cin7), None)
        if hit:
            stats["updated"] += 1
            if apply:
                keep = {k: hit[k] for k in ("sites", "payment_basis", "price_tier", "payment_terms_days", "active")
                        if hit.get(k) not in (None, "", [])}
                await db.customers().update_one({"_id": hit["_id"]}, {"$set": {**doc, **keep}})
            continue
        m = next((c for c in manual if c["_id"] not in claimed and (
            (doc["phone"] and customer_search.phone_e164(c.get("phone")) == doc["phone"])
            or (doc["email"] and (c.get("email") or "").strip().lower() == doc["email"].lower())
            or customer_search.norm(c["name"]) == customer_search.norm(doc["name"]))), None)
        if m:
            claimed.add(m["_id"])
            stats["merged_with_existing"].append(f"{doc['ref']} {doc['name']} -> existing {m['name']}")
            if apply:
                fill = {k: v for k, v in doc.items() if k not in ("sites", "active", "payment_basis", "price_tier",
                                                                  "payment_terms_days")
                        and (m.get(k) in (None, "", []) or k in ("ref", "cin7_ids", "cin7"))}
                addresses = {customer_search.norm(s.get("address")) for s in m.get("sites") or []}
                more = [s for s in doc["sites"] if customer_search.norm(s["address"]) not in addresses]
                await db.customers().update_one({"_id": m["_id"]}, {"$set": {**fill, "sites": (m.get("sites") or []) + more}})
            continue
        stats["new"] += 1
        inserts.append({"_id": uuid4().hex, "node_id": node_id, **doc, "xero_contact_id": None,
                        "date": r["first_order"] or datetime.utcnow().date().isoformat(),
                        "created_at": datetime.utcnow(), "created_by": user["email"], "imported_from": "cin7"})
    top = max(int(r["makx_ref"].split("-")[1]) for r in rows)
    if apply:
        for i in range(0, len(inserts), 500):
            await db.customers().insert_many(inserts[i:i + 500])
        await db.nodes().update_one({"node_id": node_id}, {"$set": {"customer_prefix": prefix}})
        await db.counters().update_one({"_id": f"{node_id}:customer"}, {"$max": {"seq": top}}, upsert=True)
        node["customer_prefix"] = prefix
        # customers keyed in before the import and not matched to Cin7 get refs after the Cin7 ones
        # (a ref they were given before the import could clash with a Cin7 number)
        async for c in db.customers().find({"node_id": node_id, "cin7_ids": {"$exists": False}}):
            m = re.fullmatch(rf"{prefix}-(\d+)", c.get("ref") or "")
            if m and int(m.group(1)) > top:
                continue
            ref = f"{prefix}-{await db.next_number(node_id, 'customer'):04d}"
            await db.customers().update_one({"_id": c["_id"]}, {"$set": {"ref": ref}})
        await audit.log(user, node_id, "import", "customers", None,
                        after={k: v for k, v in stats.items() if k != "merged_with_existing"})
        customer_search.invalidate(node_id)
    stats["applied"] = apply
    stats["existing_before"] = len(existing)
    stats["refs_continue_after"] = f"{prefix}-{top:04d}"
    return stats


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 2:
        raise SystemExit(__doc__)
    out = asyncio.run(run(args[0], args[1], "--apply" in sys.argv))
    for k, v in out.items():
        print(f"{k}: {v}")
