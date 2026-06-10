"""Audit log: every write records who, when, what, before/after."""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

import db


async def log(user: dict, node_id: str, action: str, collection: str,
              doc_id: str | None = None, before: dict | None = None,
              after: dict | None = None):
    await db.audit_log().insert_one({
        "_id": uuid4().hex,
        "node_id": node_id,
        "at": datetime.utcnow(),
        "by": user["email"],
        "role": user["role"],
        "action": action,           # create | update | delete | resolve | match
        "collection": collection,
        "doc_id": doc_id,
        "before": _scrub(before),
        "after": _scrub(after),
    })


def _scrub(doc: dict | None) -> dict | None:
    if doc is None:
        return None
    out = {k: v for k, v in doc.items() if k not in ("password_hash", "content_b64", "photo_b64")}
    return out
