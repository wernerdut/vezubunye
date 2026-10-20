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


def fittings_ledger():
    return db()["fittings_ledger"]


def paraffin_ledger():
    return db()["paraffin_ledger"]


def finished_goods():
    return db()["finished_goods_ledger"]


def scrap_log():
    return db()["scrap_log"]


def daily_captures():
    return db()["daily_captures"]


def delivery_notes():
    return db()["delivery_notes"]


def delivery_documents():
    return db()["delivery_documents"]


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


# ---------- partner app (Brief 2) ---------- #

def partner_users():
    return db()["partner_users"]


def brand_assets():
    return db()["brand_assets"]


def customers():
    return db()["customers"]


def price_lists():
    return db()["price_lists"]


def quotes():
    return db()["quotes"]


def orders():
    return db()["orders"]


def cash_deposits():
    return db()["cash_deposits"]


def pay_links():
    return db()["pay_links"]


def dashboard_views():
    return db()["dashboard_views"]


def pod_files():
    return db()["pod_files"]


def secrets():
    """Per-node encrypted integration secrets (Yoco key, webhook secret). Never serialised."""
    return db()["node_secrets"]


def xero_connections():
    return db()["xero_connections"]


def xero_calls():
    return db()["xero_call_log"]


def bills():
    return db()["bills"]


def suppliers():
    return db()["suppliers"]


def bill_documents():
    return db()["bill_documents"]


async def next_number(node_id: str, kind: str, session=None) -> int:
    """Atomic per-node sequential counter (kind: 'dn' | 'inv' | 'quo' | 'ord'). Never reused."""
    doc = await counters().find_one_and_update(
        {"_id": f"{node_id}:{kind}"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
        session=session,
    )
    return doc["seq"]


async def ensure_indexes():
    """node_id is indexed on every collection without exception."""
    for coll in (
        powder_ledger(), production_runs(), finished_goods(), scrap_log(),
        fittings_ledger(), paraffin_ledger(), daily_captures(), delivery_notes(),
        invoices(), payments(), flags(), physical_counts(), audit_log(),
        customers(), quotes(), orders(), cash_deposits(), pay_links(), bills(),
        suppliers(), price_lists(),
    ):
        await coll.create_index("node_id")
        await coll.create_index([("node_id", 1), ("date", 1)])
    await delivery_documents().create_index("delivery_id")
    await delivery_documents().create_index("node_id")
    await node_config().create_index("node_id", unique=True)
    await nodes().create_index("node_id", unique=True)
    await users().create_index("email", unique=True)
    await delivery_notes().create_index([("node_id", 1), ("dn_number", 1)], unique=True)
    # legacy invoices carry no number of their own, so uniqueness applies to numbered ones only
    try:
        await invoices().create_index([("node_id", 1), ("invoice_number", 1)], unique=True,
                                      partialFilterExpression={"invoice_number": {"$type": "string"}})
    except Exception:  # noqa: BLE001  (Brief 1 built it without the partial filter)
        await invoices().drop_index("node_id_1_invoice_number_1")
        await invoices().create_index([("node_id", 1), ("invoice_number", 1)], unique=True,
                                      partialFilterExpression={"invoice_number": {"$type": "string"}})
    await invoices().create_index("order_id")
    await invoices().create_index("dn_id")
    await delivery_notes().create_index("order_id")
    await payments().create_index("allocations.invoice_id")
    await payments().create_index([("node_id", 1), ("xero_payment_id", 1)])
    await nodes().create_index("slug", unique=True,
                               partialFilterExpression={"slug": {"$type": "string"}})
    await partner_users().create_index([("node_id", 1), ("email", 1)], unique=True)
    await quotes().create_index([("node_id", 1), ("quote_number", 1)])
    await orders().create_index([("node_id", 1), ("order_number", 1)])
    await orders().create_index("customer_id")
    await customers().create_index([("node_id", 1), ("name", 1)])
    await dashboard_views().create_index([("node_id", 1), ("user", 1)])
    await xero_connections().create_index("node_id", unique=True)
