"""Nightly jobs (02:00 SAST): recon sweeps for every node (R3, R4, unallocated payments, cash
not banked, quote expiry), the Yoco catch-up for missed webhooks, and the Xero pull, queue and
keep-alive. In-process, like RIPS's scheduler; the service runs one replica."""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone

import db

SAST = timezone(timedelta(hours=2))
RUN_AT_HOUR = int(os.getenv("NIGHTLY_HOUR_SAST", "2"))
_task: asyncio.Task | None = None


async def run_once() -> dict:
    import recon
    import xero_mt
    import yoco
    out = {}
    async for node in db.nodes().find({"status": "active"}):
        nid = node["node_id"]
        if not await db.node_config().find_one({"node_id": nid}):
            continue
        res = {}
        for name, fn in (("yoco", yoco.sweep_links), ("xero", xero_mt.nightly), ("sweeps", recon.run_sweeps)):
            try:
                res[name] = await fn(nid)
            except Exception as e:  # noqa: BLE001  (one node or one integration never stops the rest)
                res[name] = {"error": str(e)[:200]}
        out[nid] = res
    try:
        await xero_mt.keep_alive()
    except Exception as e:  # noqa: BLE001
        out["keep_alive"] = str(e)[:200]
    return out


def _seconds_to_next() -> float:
    now = datetime.now(SAST)
    nxt = now.replace(hour=RUN_AT_HOUR, minute=0, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


async def _loop():
    while True:
        await asyncio.sleep(_seconds_to_next())
        try:
            res = await run_once()
            print(f"[nightly] {res}")
        except Exception as e:  # noqa: BLE001
            print(f"[nightly] failed: {e}")


def start():
    global _task
    if os.getenv("DISABLE_SCHEDULER") or _task is not None:
        return
    try:
        _task = asyncio.get_event_loop().create_task(_loop())
    except RuntimeError:
        pass
