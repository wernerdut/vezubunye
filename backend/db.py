"""MongoDB client + collection accessors for Vezubunye."""
from __future__ import annotations

import os
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = os.getenv("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.getenv("DB_NAME", "vezubunye")

_client: AsyncIOMotorClient | None = None


def client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URL)
    return _client


def db():
    return client()[DB_NAME]


def nodes():
    return db()["nodes"]


def node_config():
    return db()["node_config"]


def powder_ledger():
    return db()["powder_ledger"]


def production_runs():
    return db()["production_runs"]


def finished_goods():
    return db()["finished_goods_ledger"]


def scrap_log():
    return db()["scrap_log"]


def daily_captures():
    return db()["daily_captures"]


def delivery_notes():
    return db()["delivery_notes"]


def invoices():
    return db()["invoices"]


def payments():
    return db()["payments"]


def flags():
    return db()["flags"]


def physical_counts():
    return db()["physical_counts"]


def users():
    return db()["users"]


def audit_log():
    return db()["audit_log"]


def counters():
    return db()["counters"]


async def next_number(node_id: str, kind: str) -> int:
    """Atomic per-node sequential counter (kind: 'dn' | 'inv')."""
    doc = await counters().find_one_and_update(
        {"_id": f"{node_id}:{kind}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    return doc["seq"]


async def ensure_indexes():
    """node_id is indexed on every collection without exception."""
    for coll in (
        powder_ledger(), production_runs(), finished_goods(), scrap_log(),
        daily_captures(), delivery_notes(), invoices(), payments(), flags(),
        physical_counts(), audit_log(),
    ):
        await coll.create_index("node_id")
        await coll.create_index([("node_id", 1), ("date", 1)])
    await node_config().create_index("node_id", unique=True)
    await nodes().create_index("node_id", unique=True)
    await users().create_index("email", unique=True)
    await delivery_notes().create_index([("node_id", 1), ("dn_number", 1)], unique=True)
    await invoices().create_index([("node_id", 1), ("invoice_number", 1)], unique=True)
