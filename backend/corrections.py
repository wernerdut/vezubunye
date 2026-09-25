"""Corrections layer: correct, never erase.

Operational records are voided, not deleted. A void stamps `void: {by, at, reason}` on the
record and on every ledger row derived from it; voided rows drop out of every derivation
(recon, reports, dashboards) through the ACTIVE filter but stay in the database as evidence.

Every correction:
  - requires a non-blank reason,
  - writes a full before/after audit entry, including the derived rows it touched,
  - runs the node's recon sweep,
  - raises `post_count_correction` when a stock-moving record dated on or before the node's
    latest physical count is corrected (protects R7 without period locking).

Writes that touch more than one collection run in a MongoDB transaction when the server
supports it (Atlas replica set). A standalone mongod or the test mock runs them unwrapped.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import HTTPException

import audit
import db
import recon

# Every read that derives a balance, a report or a list must include this.
ACTIVE = {"void": {"$exists": False}}


def active(filt: dict | None = None) -> dict:
    return {**(filt or {}), **ACTIVE}


def require_reason(reason: str | None) -> str:
    if not reason or not reason.strip():
        raise HTTPException(400, "Reason required")
    return reason.strip()


def stamp(user: dict, reason: str) -> dict:
    return {"by": user["email"], "at": datetime.utcnow(), "reason": reason}


def coll(name: str):
    return db.db()[name]


def capture_cascade(capture_id: str) -> list[tuple[str, dict]]:
    """Every ledger row a daily capture writes."""
    by_src = {"source_capture_id": capture_id}
    return [
        ("powder_ledger", by_src), ("fittings_ledger", by_src), ("paraffin_ledger", by_src),
        ("production_runs", by_src), ("scrap_log", by_src),
        ("finished_goods_ledger", {"reference": capture_id}),
    ]


def delivery_cascade(dn_id: str) -> list[tuple[str, dict]]:
    """The stock-out rows a delivery note writes. Voiding them returns the tanks to stock."""
    return [("finished_goods_ledger", {"reference": dn_id, "type": "dispatched"})]


# ---------- transactions ---------- #

_txn_support: dict = {}


async def _supports_txn() -> bool:
    client = db.client()
    key = id(client)
    if key not in _txn_support:
        try:
            hello = await client.admin.command("hello")
            _txn_support[key] = bool(hello.get("setName") or hello.get("msg") == "isdbgrid")
        except Exception:  # noqa: BLE001  (mock client, no hello)
            _txn_support[key] = False
    return _txn_support[key]


@asynccontextmanager
async def txn():
    """Yield a session inside a transaction, or None where transactions are unavailable.
    Pass it as `session=` to every write that must commit together."""
    if not await _supports_txn():
        yield None
        return
    async with await db.client().start_session() as s:
        async with s.start_transaction():
            yield s


# ---------- void ---------- #

async def void_derived(cascade: list[tuple[str, dict]], st: dict, session=None) -> dict:
    """Void every active row matching the cascade. Returns {collection: [rows as they were]}."""
    before: dict = {}
    for cname, filt in cascade:
        rows = [r async for r in coll(cname).find(active(filt), session=session)]
        if rows:
            await coll(cname).update_many({"_id": {"$in": [r["_id"] for r in rows]}},
                                          {"$set": {"void": st}}, session=session)
            before.setdefault(cname, []).extend(rows)
    return before


async def mark_void(collection: str, doc_id: str, user: dict, reason: str,
                    cascade: list[tuple[str, dict]] | None = None, session=None,
                    extra: dict | None = None) -> dict:
    """Void one record and its derived rows, audit-logged, inside the caller's session."""
    rec = await coll(collection).find_one({"_id": doc_id}, session=session)
    if not rec:
        raise HTTPException(404, "Record not found")
    if rec.get("void"):
        raise HTTPException(400, "Record is already void")
    st = stamp(user, reason)
    derived = await void_derived(cascade or [], st, session)
    await coll(collection).update_one({"_id": doc_id}, {"$set": {"void": st, **(extra or {})}},
                                      session=session)
    await audit.log(
        user, rec["node_id"], "void", collection, doc_id,
        before={"record": rec, "derived": derived},
        after={"record": {**rec, **(extra or {}), "void": st},
               "derived": {c: [{**r, "void": st} for r in rows] for c, rows in derived.items()}},
        session=session)
    return {**rec, **(extra or {}), "void": st, "_derived": derived}


async def latest_count(node_id: str, exclude_id: str | None = None) -> dict | None:
    filt = active({"node_id": node_id})
    if exclude_id:
        filt["_id"] = {"$ne": exclude_id}
    async for c in db.physical_counts().find(filt).sort("date", -1).limit(1):
        return c
    return None


async def after_correction(node_id: str, collection: str, doc_id: str, action: str,
                           record_date: str | None, moves_stock: bool = True) -> dict:
    """Rule 7 (post-count flag, stock-moving corrections only) + recon sweep."""
    flag_id = None
    if moves_stock and record_date:
        cnt = await latest_count(node_id, exclude_id=doc_id if collection == "physical_counts" else None)
        if cnt and record_date <= cnt["date"]:
            flag_id = await recon.raise_flag(
                node_id, "post_count_correction",
                f"{action.capitalize()} of a {collection} record dated {record_date}, on or before "
                f"the {cnt['date']} count. That count's variances were struck against the ledger "
                "as it stood before this correction.",
                {"collection": collection, "doc_id": doc_id, "count_id": cnt["_id"], "action": action},
                record_date)
    sweep = await recon.run_sweeps(node_id)
    return {"post_count_flag": flag_id, "sweep": sweep}


async def void_record(collection: str, doc_id: str, user: dict, reason: str,
                      cascade: list[tuple[str, dict]] | None = None,
                      moves_stock: bool = True) -> dict:
    """Void a record and its derived rows in one transaction, then flag and sweep."""
    reason = require_reason(reason)
    async with txn() as s:
        rec = await mark_void(collection, doc_id, user, reason, cascade, s)
    post = await after_correction(rec["node_id"], collection, doc_id, "void",
                                  rec.get("date"), moves_stock)
    derived = rec.pop("_derived")
    rec.pop("content_b64", None)
    rec.pop("photo_b64", None)
    return {**rec, "voided_rows": {c: len(r) for c, r in derived.items()}, **post}
