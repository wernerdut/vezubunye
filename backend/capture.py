"""Daily production capture, shared by the internal app and the partner app.

One capture per node per day. Keying the sheet writes the powder, fittings and paraffin
movements and the tanks moulded; A and B tanks go straight into finished goods (booked),
rejects to scrap. Re-capturing a captured day is a correction: reason required, the prior
derived rows are voided (never deleted) and logged in full.

Colour: a production line names the tank colour (the node's colour code) and/or the outer
powder grade. Either is derived from the other through the node's colours; booked finished
goods carry the tank colour so stock is known per colour.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException

import audit
import corrections
import db
from corrections import ACTIVE

SAST = timezone(timedelta(hours=2))


def _now():
    return datetime.utcnow()


def same_sa_day(first_captured: datetime) -> bool:
    as_utc = first_captured.replace(tzinfo=timezone.utc)
    return as_utc.astimezone(SAST).date() == datetime.now(SAST).date()


async def open_capture(node_id: str, date: str, user: dict) -> dict:
    existing = await db.daily_captures().find_one({"node_id": node_id, "date": date, **ACTIVE})
    if existing:
        return {k: v for k, v in existing.items() if k not in ("photo_b64", "photo")}
    doc = {"_id": uuid4().hex, "node_id": node_id, "date": date, "photo_url": None,
           "captured_by": user["email"], "status": "pending", "created_at": _now()}
    await db.daily_captures().insert_one(doc)
    await audit.log(user, node_id, "create", "daily_captures", doc["_id"], after=doc)
    return doc


def resolve_colour(cfg: dict, line) -> tuple[str, str]:
    """(outer powder code, tank colour code) for a production line."""
    colours = {c["code"]: c for c in cfg.get("colours", []) or []}
    powder = line.colour or ""
    tank_colour = getattr(line, "tank_colour", "") or ""
    if tank_colour:
        c = colours.get(tank_colour)
        if not c:
            raise HTTPException(400, f"Unknown colour {tank_colour}")
        if not c.get("powder_product"):
            raise HTTPException(400, f"{c['name']} has no powder product yet, so it cannot be captured")
        powder = powder or c["powder_product"]
    elif powder:
        for c in colours.values():
            if c.get("powder_product") == powder:
                tank_colour = c["code"]
                break
    return powder, tank_colour


async def key_entries(cap: dict, payload, user: dict, cfg: dict, can_correct_any_day: bool) -> dict:
    """Write the day's movements. `can_correct_any_day` is the admin correction right."""
    if cap.get("void"):
        raise HTTPException(400, "Capture is void")
    node_id, date, capture_id = cap["node_id"], cap["date"], cap["_id"]
    recapture = cap.get("status") == "captured"
    reason = None
    if recapture:
        if not can_correct_any_day and not same_sa_day(cap["created_at"]):
            raise HTTPException(403, "Only same-day fixes are open here. "
                                     "Correcting an earlier capture is an admin correction")
        reason = corrections.require_reason(payload.reason)
    tanks = {t["code"]: t for t in cfg["tank_types"]}
    resolved = []
    for line in payload.production:
        if line.tank_type not in tanks:
            raise HTTPException(400, f"Unknown tank type {line.tank_type}")
        resolved.append(resolve_colour(cfg, line))
    entries = payload.model_dump(exclude={"reason"})

    async with corrections.txn() as s:
        async def ins(coll, doc):
            await coll.insert_one({"_id": uuid4().hex, "node_id": node_id, "date": date,
                                   **doc, "created_at": _now()}, session=s)

        prior: dict = {}
        if recapture:
            prior = await corrections.void_derived(
                corrections.capture_cascade(capture_id), corrections.stamp(user, reason), s)

        for line in payload.powder:
            for mtype, kg in (("received", line.received_kg), ("issued", line.issued_kg)):
                if kg:
                    await ins(db.powder_ledger(), {
                        "powder_type": line.powder_type, "type": mtype, "kg": kg,
                        "source_capture_id": capture_id, "notes": payload.notes or ""})
        if payload.paraffin_received:
            await ins(db.paraffin_ledger(), {
                "type": "received", "litres": payload.paraffin_received,
                "source_capture_id": capture_id, "notes": payload.notes or ""})
        for line in payload.fittings:
            for mtype, qty in (("received", line.received_qty), ("issued", line.issued_qty)):
                if qty:
                    await ins(db.fittings_ledger(), {
                        "fitting_type": line.fitting_type, "type": mtype, "quantity": qty,
                        "source_capture_id": capture_id})
        for line, (powder, tank_colour) in zip(payload.production, resolved):
            if line.quantity_a == line.quantity_b == line.quantity_reject == 0:
                continue
            await ins(db.production_runs(), {
                "tank_type": line.tank_type, "colour": powder, "tank_colour": tank_colour,
                "quantity_a": line.quantity_a, "quantity_b": line.quantity_b,
                "quantity_reject": line.quantity_reject, "source_capture_id": capture_id})
            for grade, qty in (("A", line.quantity_a), ("B", line.quantity_b)):
                if qty > 0:
                    await ins(db.finished_goods(), {
                        "tank_type": line.tank_type, "colour": tank_colour, "grade": grade,
                        "type": "booked", "quantity": qty, "reference": capture_id})
            if line.quantity_reject > 0:
                t = tanks[line.tank_type]
                kg_lost = line.quantity_reject * (t["weight_kg"] + t.get("lid_weight_kg", 0.0))
                await ins(db.scrap_log(), {
                    "tank_type": line.tank_type, "quantity": line.quantity_reject,
                    "kg_lost": kg_lost,
                    "material_cost_lost": round(kg_lost * cfg.get("material_cost_per_kg", 0.0), 2),
                    "source_capture_id": capture_id, "notes": payload.notes or ""})

        await db.daily_captures().update_one(
            {"_id": capture_id},
            {"$set": {"status": "captured", "entries": entries,
                      "captured_by": user["email"], "captured_at": _now()}}, session=s)
        if recapture:
            await audit.log(user, node_id, "edit", "daily_captures", capture_id,
                            before={"entries": cap.get("entries"), "derived": prior, "reason": reason},
                            after={"entries": entries, "status": "captured"}, session=s)
        else:
            await audit.log(user, node_id, "update", "daily_captures", capture_id,
                            after={"entries": entries, "status": "captured"}, session=s)

    out = {"capture_id": capture_id, "status": "captured", "flags_raised": []}
    if recapture:
        out.update(await corrections.after_correction(node_id, "daily_captures", capture_id, "re-capture", date))
    return out
