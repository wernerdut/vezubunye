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
                {"code": "2500L", "name": "2500L Tank", "ex_works_price": 1620.0, "weight_kg": 36.0},
                {"code": "5000L", "name": "5000L Tank", "ex_works_price": 3450.0, "weight_kg": 75.0},
            ],
            "material_cost_per_kg": 20.0,           # admin-only visibility
            "b_grade_exworks_pct": 100.0,           # OPEN ITEM: Werner to confirm the B-grade rule
            "vat_rate": 15.0,
            "payment_terms_days": 30,
            "updated_at": datetime.utcnow(),
        })
        print("config: gogreen created")
    else:
        print("config: gogreen exists")

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
