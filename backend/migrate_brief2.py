"""Brief 2 migration and Makulu onboarding. Idempotent: safe on every startup.

1. Existing delivery notes become the commercial chain:
   - distinct client names per node become customers (basis terms: GoGreen's deliveries were
     all delivered before payment), client_details copied into one site's address;
   - each active DN gets one legacy order (fully delivered) and one legacy invoice carrying
     its lines, totals, split and payment status. Legacy invoices take no number, are never
     posted to Xero and have no PDF; they display the DN number;
   - each matched payment becomes an allocation to that legacy invoice.
2. Makulu Tanks (slug makx) is created with its config, price lists and brand assets.
"""
from __future__ import annotations

import base64
import os
from datetime import datetime
from uuid import uuid4

import db
import pricing
import recon
from corrections import ACTIVE

ASSETS = os.path.join(os.path.dirname(__file__), "assets", "brands")
SYSTEM = {"email": "system:migration", "role": "system"}


async def migrate_delivery_notes() -> dict:
    stats = {"customers": 0, "orders": 0, "invoices": 0, "allocations": 0}
    async for dn in db.delivery_notes().find({"order_id": {"$exists": False}}):
        node_id = dn["node_id"]
        cfg = await db.node_config().find_one({"node_id": node_id}) or {}
        cust = await db.customers().find_one({"node_id": node_id, "name": dn["client_name"]})
        if not cust:
            cust = {"_id": uuid4().hex, "node_id": node_id, "name": dn["client_name"],
                    "contact_person": "", "email": "", "phone": "", "billing_address": "",
                    "vat_number": "", "payment_basis": "terms",
                    "payment_terms_days": cfg.get("payment_terms_days", 30), "price_tier": "retail",
                    "xero_contact_id": None, "active": True,
                    "sites": [{"site_id": uuid4().hex[:10], "label": "Main", "address": dn.get("client_details", ""),
                               "town": "", "maps_link": "", "lat": None, "lng": None, "pinned": False,
                               "contact_name": "", "contact_phone": "", "directions": ""}],
                    "date": dn["date"], "created_at": datetime.utcnow(), "created_by": SYSTEM["email"],
                    "migrated": True}
            await db.customers().insert_one(cust)
            stats["customers"] += 1
        order_lines = []
        dn_lines = []
        for i, ln in enumerate(dn["lines"]):
            lid = f"L{i + 1}"
            ol = pricing.price_line({"line_id": lid, "kind": "tank", "tank_type": ln["tank_type"],
                                     "colour": ln.get("colour", ""), "grade": ln.get("grade", "A"),
                                     "qty": ln["quantity"], "unit_price": ln.get("unit_price", 0.0),
                                     "price_includes_vat": False, "qty_cancelled": 0}, cfg or {"vat_rate": 15})
            order_lines.append(ol)
            dn_lines.append({**ln, "order_line_id": lid})
        order = {"_id": uuid4().hex, "node_id": node_id, "order_number": None, "date": dn["date"],
                 "customer_id": cust["_id"], "customer_name": dn["client_name"],
                 "site": (cust.get("sites") or [None])[0], "quote_id": None, "payment_basis": "terms",
                 "pay_on_delivery": False, "cod_method": None, "price_tier": "retail",
                 "price_list_version_id": None, "lines": order_lines,
                 "subtotal": dn.get("subtotal", 0.0), "vat": dn.get("vat", 0.0), "total": dn.get("total", 0.0),
                 "vat_rate": dn.get("vat_rate", 15.0), "requested_date": None, "notes": "",
                 "status": "cancelled" if dn.get("void") else "open", "legacy": True,
                 "display_number": dn["dn_number"], "created_by": SYSTEM["email"],
                 "created_at": dn.get("created_at") or datetime.utcnow()}
        await db.orders().insert_one(order)
        stats["orders"] += 1
        inv = {"_id": uuid4().hex, "node_id": node_id, "invoice_number": None,
               "display_number": dn["dn_number"], "order_id": order["_id"], "order_number": None,
               "dn_id": dn["_id"], "dn_number": dn["dn_number"], "customer_id": cust["_id"],
               "customer_name": dn["client_name"], "date": dn["date"],
               "due_date": _add(dn["date"], cfg.get("payment_terms_days", 30)),
               "lines": order_lines, "subtotal": dn.get("subtotal", 0.0), "vat": dn.get("vat", 0.0),
               "total": dn.get("total", 0.0), "vat_rate": dn.get("vat_rate", 15.0),
               "amount_paid": dn.get("amount_paid", 0.0),
               "balance": round(dn.get("total", 0.0) - dn.get("amount_paid", 0.0), 2),
               "status": dn.get("status", "unpaid"), "legacy": True, "xero": {"status": "never"},
               "fenix_value": dn.get("fenix_exworks_value"),
               "created_by": SYSTEM["email"], "created_at": dn.get("created_at") or datetime.utcnow()}
        if dn.get("void"):
            inv["void"] = dn["void"]
            inv["status"] = "void"
        await db.invoices().insert_one(inv)
        stats["invoices"] += 1
        await db.delivery_notes().update_one({"_id": dn["_id"]}, {"$set": {
            "order_id": order["_id"], "customer_id": cust["_id"], "invoice_id": inv["_id"],
            "lines": dn_lines, "legacy": True, "delivery_status": "delivered",
            "collect_amount": None, "collection": None}})
        async for p in db.payments().find({"matched_delivery_id": dn["_id"], "allocations": {"$exists": False}}):
            await db.payments().update_one({"_id": p["_id"]}, {"$set": {
                "allocations": [{"invoice_id": inv["_id"], "amount": p["amount"]}], "unallocated": 0.0,
                "customer_id": cust["_id"], "method": p.get("method", "eft"), "deposit_id": None,
                "reference": p.get("bank_reference", "")}})
            stats["allocations"] += 1
    # unmatched Brief 1 payments: no allocation yet
    await _unmatched_payments()
    return stats


async def _unmatched_payments():
    async for p in db.payments().find({"allocations": {"$exists": False}}):
        await db.payments().update_one({"_id": p["_id"]}, {"$set": {
            "allocations": [], "unallocated": p["amount"], "method": "eft", "deposit_id": None,
            "reference": p.get("bank_reference", "")}})


def _add(d: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(d) + timedelta(days=n)).isoformat()


# ============================== Makulu ============================== #

MAKULU_NODE = {
    "node_id": "makulu", "name": "Makulu Tanks", "location": "Bellville South, Cape Town",
    "status": "active", "slug": "makx", "prefix": "MX", "partner_app_live": False,
    "branding": {
        "display_name": "Makulu Tanks",
        "colors": {"primary": "#00B499", "accent": "#9CC938", "dark": "#0A1D23",
                   "gradient": ["#00B499", "#9CC938", "#D2E07E"]},
        "font": {"family": "Acumin Variable Concept", "fallback": "Inter, ui-sans-serif, system-ui, sans-serif",
                 "adobe_kit": None},
        "assets": {},
    },
    "company": {
        "legal_name": "MAKULU TANKS (PTY) LTD", "reg_no": "2018/060808/07", "registered": "2018-05-03",
        "vat_number": "4880281797", "address": "4 Fabriek Street, Bellville South, Cape Town",
        "whatsapp": "+27 67 637 8054", "phone": "+27 21 931 0193", "email": "info@makulutanks.co.za",
        "website": "makulutanks.co.za", "hours": "Monday to Friday, 7am to 5pm",
        "bank": {"bank": "FNB", "account_name": "Makulu Tanks", "account_number": "62748592096",
                 "branch_code": "202709", "swift": "FIRNZAJJ"},
    },
}

_BLACK = "5840 Black UV RotoPowder"
MAKULU_COLOURS = [
    ("EMERALD", "Emerald Green", "#1E8C4E", "standard", "5840 Emerald Green UV RotoPowder"),
    ("GREY", "Battleship Grey", "#7C8285", "standard", "5840 BattleShip Grey UV RotoPowder"),
    ("SAHARA", "Sahara Beige", "#D8C7A2", "standard", "3840 Makulu Beige UV RotoPowder"),
    ("PURPLE", "Jacaranda Purple", "#7B5BA6", "on_request", "3840 Purple UV RotoPowder"),
    ("ISLAND", "Island Blue", "#2A8C99", "on_request", "3840 Teal Blue UV RotoPowder"),
    ("ORANGE", "Burnt Orange", "#C8602A", "on_request", ""),
    ("COFFEE", "Coffee Brown", "#5C4033", "on_request", "5840 Coffee Brown UV RotoPowder"),
    ("BLACK", "Black", "#1C1C1C", "on_request", _BLACK),
    ("BRONZE", "Bronze Olive", "#6F6A3A", "on_request", ""),
    ("JADE", "Jade Green", "#3FA37A", "on_request", "7840 Jade Green UV RotoPowder"),
]


def makulu_config() -> dict:
    colours = [{"code": c, "name": n, "swatch_hex": h, "availability": a, "powder_product": p}
               for c, n, h, a, p in MAKULU_COLOURS]
    blacks = ["5840 Black UV RotoPowder", "6840 Black UV RotoPowder", "2840 Black UV RotoPowder"]
    outer = sorted({p for *_, p in MAKULU_COLOURS if p and p not in blacks})
    all_codes = [c["code"] for c in colours]
    return {
        "node_id": "makulu",
        "tank_types": [
            {"code": "W1000", "name": "1,000 L water", "kind": "water", "capacity_l": 1000,
             "diameter_mm": 780, "height_mm": 2300, "weight_kg": 22.0, "lid_weight_kg": 1.0,
             "ex_works_price": 0.0, "allowed_colours": all_codes, "active": True},
            {"code": "W2300", "name": "2,300 L water", "kind": "water", "capacity_l": 2300,
             "diameter_mm": 1250, "height_mm": 1950, "weight_kg": 36.0, "lid_weight_kg": 1.0,
             "ex_works_price": 0.0, "allowed_colours": all_codes, "active": True},
            {"code": "W5000", "name": "5,000 L water", "kind": "water", "capacity_l": 5000,
             "diameter_mm": 1850, "height_mm": 2150, "weight_kg": 72.0, "lid_weight_kg": 1.0,
             "ex_works_price": 0.0, "allowed_colours": all_codes, "active": True},
            {"code": "S2000", "name": "2,000 L septic", "kind": "septic", "capacity_l": 2000,
             "diameter_mm": 1400, "height_mm": 1600, "weight_kg": 80.0, "lid_weight_kg": 1.0,
             "ex_works_price": 0.0, "allowed_colours": ["BLACK"], "active": True},
        ],
        "material_cost_per_kg": 0.0, "b_grade_exworks_pct": 100.0, "vat_rate": 15.0,
        "payment_terms_days": 30,
        "powder_products": [{"code": b, "colour": "Black", "description": b, "is_black": True} for b in blacks]
                           + [{"code": p, "colour": p.split(" ", 1)[1].replace(" UV RotoPowder", ""),
                               "description": p, "is_black": False} for p in outer],
        "fitting_types": [], "fittings_per_tank": {}, "paraffin_litres_per_tank": 0.0,
        "tolerances": {"powder_kg": 0.0, "tank_qty": 0, "fittings_qty": 0},
        "colours": colours, "track_colour_stock": True, "commercial_model": "powder_sale",
        "price_tiers": [{"code": "retail", "name": "Retail", "prices_include_vat": True},
                        {"code": "trade", "name": "Trade", "prices_include_vat": True}],
        "charges": [
            {"code": "DEL50", "name": "Delivery within 50 km", "amount": 300.0, "includes_vat": True, "xero_account": ""},
            {"code": "DELQ", "name": "Delivery beyond 50 km", "amount": None, "includes_vat": True, "xero_account": ""},
        ],
        "quote_validity_days": 14, "cash_banking_days": 2,
        "drivers": [{"name": "Jimmy", "phone": "+27 67 046 3659", "vehicle_reg": ""},
                    {"name": "Tapiwa L", "phone": "+27 68 473 7805", "vehicle_reg": ""}],
        "updated_at": datetime.utcnow(),
    }


MAKULU_PRICES = {
    "retail": {"W1000": {"A": 1800}, "W2300": {"A": 2250}, "W5000": {"A": 4400}, "S2000": {"A": 7200}},
    "trade": {"W1000": {"A": 1250}, "W2300": {"A": 1850}, "W5000": {"A": 3550}, "S2000": {"A": 5550}},
}

ASSET_FILES = {"logo_light": "logo_light.png", "logo_dark": "logo_dark.svg", "favicon": "favicon.png",
               "icon": "icon.png", "app_icon": "app_icon.png", "logo_light_png": "logo_light.png",
               "logo_dark_png": "logo_dark.png", "icon_png": "icon.png", "gradient": "gradient.jpg"}


async def load_brand_assets(node_id: str, slug: str) -> list[str]:
    """Seed a node's brand files from assets/brands/<slug>/ unless the node already has them."""
    loaded = []
    folder = os.path.join(ASSETS, slug)
    if not os.path.isdir(folder):
        return loaded
    for kind, fname in ASSET_FILES.items():
        path = os.path.join(folder, fname)
        if not os.path.exists(path) or await db.brand_assets().find_one({"node_id": node_id, "kind": kind}):
            continue
        ctype = {"svg": "image/svg+xml", "jpg": "image/jpeg"}.get(fname.rsplit(".", 1)[1], "image/png")
        await db.brand_assets().insert_one({"_id": uuid4().hex, "node_id": node_id, "kind": kind,
                                            "content_type": ctype, "filename": fname,
                                            "content_b64": base64.b64encode(open(path, "rb").read()).decode(),
                                            "created_at": datetime.utcnow()})
        loaded.append(kind)
    return loaded


async def seed_makulu() -> list[str]:
    done = []
    if not await db.nodes().find_one({"node_id": "makulu"}):
        await db.nodes().insert_one({"_id": uuid4().hex, **MAKULU_NODE, "created_at": datetime.utcnow()})
        done.append("node")
    if not await db.node_config().find_one({"node_id": "makulu"}):
        await db.node_config().insert_one({"_id": uuid4().hex, **makulu_config()})
        done.append("config")
    for tier, prices in MAKULU_PRICES.items():
        if not await db.price_lists().find_one({"node_id": "makulu", "tier": tier}):
            await pricing.new_version("makulu", tier, "2026-10-01", prices, SYSTEM)
            done.append(f"prices:{tier}")
    done += await load_brand_assets("makulu", "makx")
    return done


# Collections the migration edits in place (new fields on existing documents) plus the config
# around them. Atlas free tier has no snapshots, so the first run copies them, untouched, into
# backup_brief2_<name> in the same database. Restoring is a rename back.
BACKUP_COLLECTIONS = ("delivery_notes", "payments", "invoices", "nodes", "node_config", "counters")
BACKUP_MARKER = "backup:brief2"


async def backup_once() -> dict:
    """Copy the collections before the first migration. Runs once (marker in counters); if the
    copy does not match the source count, the migration does not run."""
    if await db.counters().find_one({"_id": BACKUP_MARKER}):
        return {"skipped": "already backed up"}
    counts = {}
    for name in BACKUP_COLLECTIONS:
        src, dst = db.db()[name], db.db()[f"backup_brief2_{name}"]
        await dst.delete_many({})
        docs = [d async for d in src.find({})]
        if docs:
            await dst.insert_many(docs)
        n_src, n_dst = await src.count_documents({}), await dst.count_documents({})
        if n_src != n_dst:
            raise RuntimeError(f"backup of {name} incomplete ({n_dst} of {n_src}); migration not run")
        counts[name] = n_dst
    await db.counters().insert_one({"_id": BACKUP_MARKER, "at": datetime.utcnow(), "counts": counts})
    return counts


async def run() -> dict:
    backup = await backup_once()
    out = {"backup": backup, "delivery_notes": await migrate_delivery_notes(), "makulu": await seed_makulu()}
    return out
