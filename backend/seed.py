"""Seed the launch state: GoGreen node, config, three users.

Run: python seed.py
Idempotent — skips anything that already exists.
Passwords come from env (WERNER_PASSWORD etc.) or fall back to changeme-* defaults
that must be rotated before anyone real logs in.
"""
import asyncio
import os
from datetime import datetime
from uuid import uuid4

from dotenv import load_dotenv
load_dotenv()

import auth
import db


async def migrate_config() -> list[str]:
    """Idempotently bring the existing gogreen config up to the current shape.

    Adds any missing fields/products without overwriting admin-edited values.
    Safe to run on every app startup. Returns the list of fields touched.
    """
    cfg = await db.node_config().find_one({"node_id": "gogreen"})
    if not cfg:
        return []
    add: dict = {}
    if not any("lid_weight_kg" in t for t in cfg.get("tank_types", [])):
        add["tank_types"] = [dict(t, lid_weight_kg=t.get("lid_weight_kg", 1.0))
                             for t in cfg.get("tank_types", [])]
    # one-time add of the 1000L transport tank — match any 1000L-ish tank (code or name)
    # so an admin who renamed it (e.g. T1000L) doesn't get a duplicate re-added on restart
    has_1000 = any("1000" in (t.get("code", "") + t.get("name", ""))
                   for t in cfg.get("tank_types", []))
    if not has_1000:
        tts = add.get("tank_types") or list(cfg.get("tank_types", []))
        tts.append({"code": "1000L", "name": "1000L Horizontal Transport Tank",
                    "ex_works_price": 2600.0, "weight_kg": 40.0, "lid_weight_kg": 1.0})
        add["tank_types"] = tts
    if "powder_products" not in cfg:
        add["powder_products"] = [
            {"code": "BLACK", "colour": "Black", "description": "Black powder (body + lids)",
             "is_black": True}]
    if "fitting_types" not in cfg:
        add["fitting_types"] = []
    if "fittings_per_tank" not in cfg:
        add["fittings_per_tank"] = {}
    if "paraffin_litres_per_tank" not in cfg:
        add["paraffin_litres_per_tank"] = 11.0
    if "tolerances" not in cfg:
        add["tolerances"] = {"powder_kg": 0.0, "tank_qty": 0, "fittings_qty": 0}
    if add:
        await db.node_config().update_one({"node_id": "gogreen"}, {"$set": add})
    return list(add)


async def seed():
    # --- node ---
    if not await db.nodes().find_one({"node_id": "gogreen"}):
        await db.nodes().insert_one({
            "_id": uuid4().hex,
            "node_id": "gogreen",
            "name": "GoGreen Roto Moulding",
            "location": "Queenstown",
            "prefix": "GG",
            "status": "active",
            "created_at": datetime.utcnow(),
        })
        print("node: gogreen created")
    else:
        print("node: gogreen exists")

    # --- config (admin-editable, never hard-coded elsewhere) ---
    if not await db.node_config().find_one({"node_id": "gogreen"}):
        await db.node_config().insert_one({
            "_id": uuid4().hex,
            "node_id": "gogreen",
            "tank_types": [
                {"code": "1000L", "name": "1000L Horizontal Transport Tank", "ex_works_price": 2600.0,
                 "weight_kg": 40.0, "lid_weight_kg": 1.0},
                {"code": "2500L", "name": "2500L Tank", "ex_works_price": 1620.0,
                 "weight_kg": 36.0, "lid_weight_kg": 1.0},
                {"code": "5000L", "name": "5000L Tank", "ex_works_price": 3450.0,
                 "weight_kg": 76.0, "lid_weight_kg": 1.0},
            ],
            "material_cost_per_kg": 20.0,           # admin-only visibility
            "b_grade_exworks_pct": 100.0,           # OPEN ITEM: Werner to confirm the B-grade rule
            "vat_rate": 15.0,
            "payment_terms_days": 30,
            "powder_products": [
                {"code": "BLACK", "colour": "Black", "description": "Black powder (body + lids)",
                 "is_black": True},
            ],
            "fitting_types": [],
            "fittings_per_tank": {},
            "paraffin_litres_per_tank": 11.0,
            "tolerances": {"powder_kg": 0.0, "tank_qty": 0, "fittings_qty": 0},
            "updated_at": datetime.utcnow(),
        })
        print("config: gogreen created")
    else:
        changed = await migrate_config()
        if changed:
            print(f"config: gogreen migrated ({', '.join(changed)})")
        else:
            print("config: gogreen up to date")

    # --- users ---
    users = [
        ("werner@fenixrising.co.za", "Werner", "admin", "all", os.getenv("WERNER_PASSWORD", "changeme-werner")),
        ("pierre@fenixrising.co.za", "Pierre", "audit", ["gogreen"], os.getenv("PIERRE_PASSWORD", "changeme-pierre")),
        ("steven@fenixrising.co.za", "Steven", "operations", ["gogreen"], os.getenv("STEVEN_PASSWORD", "changeme-steven")),
    ]
    for email, name, role, access, password in users:
        if await db.users().find_one({"email": email}):
            print(f"user: {email} exists")
            continue
        await db.users().insert_one({
            "_id": uuid4().hex,
            "email": email,
            "name": name,
            "password_hash": auth.hash_password(password),
            "role": role,
            "node_access": access,
            "created_at": datetime.utcnow(),
        })
        print(f"user: {email} created ({role})")

    await db.ensure_indexes()
    print("indexes ensured")


if __name__ == "__main__":
    asyncio.run(seed())
