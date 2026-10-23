"""Customer search: find the right customer among a few thousand in one box, and stop duplicates.

Phones are stored as E.164 (+27...). A query that looks like a phone number matches on the
number first. Otherwise, ranked: exact phone or ref > name starts with > a word in the name
starts with > name contains > partial phone, contact, email, suburb/address > close spelling
(trigram similarity, so "Gringoe" finds "Geringoe"). Ties go to the most recent order.

The search runs in memory over a per-node index (a few thousand small rows), rebuilt when a
customer is written here and at most every CACHE_SECONDS otherwise.
"""
from __future__ import annotations

import re
import time
import unicodedata
from datetime import date, timedelta

import db

CACHE_SECONDS = 120
LIMIT = 20
_cache: dict[str, tuple[float, list[dict]]] = {}


# ------------------------------------------------------------ normalising

def norm(s: str | None) -> str:
    """Lowercase, accents and punctuation removed: "A.R. Búx" -> "a r bux"."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def phone_e164(s: str | None) -> str:
    """'082 123-4567' / '27821234567' / '+27 82 123 4567' -> '+27821234567'. Anything that is
    not a phone number comes back as typed, trimmed."""
    raw = (s or "").strip()
    d = re.sub(r"[\s\-().]", "", raw)
    if d.startswith("00"):
        d = "+" + d[2:]
    if re.fullmatch(r"0\d{9}", d):
        return "+27" + d[1:]
    if re.fullmatch(r"27\d{9}", d):
        return "+" + d
    if re.fullmatch(r"\+\d{8,15}", d):
        return d
    return raw


def looks_like_phone(q: str) -> bool:
    d = re.sub(r"[\s\-().+]", "", q or "")
    return len(d) >= 3 and d.isdigit()


def _trigrams(s: str) -> set[str]:
    s = f"  {s} "
    return {s[i:i + 3] for i in range(len(s) - 2)}


def similarity(a: str, b: str) -> float:
    ta, tb = _trigrams(a), _trigrams(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


# ------------------------------------------------------------ index

def suburb(c: dict) -> str:
    """Town of the first site, else the billing city, else the last part of an address
    ("..., Hout Bay")."""
    for s in c.get("sites") or []:
        if s.get("town"):
            return s["town"]
    if c.get("billing_city"):
        return c["billing_city"]
    for a in [s.get("address", "") for s in c.get("sites") or []] + [c.get("billing_address", "")]:
        tail = a.rsplit(",", 1)[-1].strip() if "," in a else ""
        if tail and not any(ch.isdigit() for ch in tail) and len(tail.split()) <= 3:
            return tail
    return ""


def _entry(c: dict) -> dict:
    phones = [p for p in (c.get("phone"), c.get("other_phone")) if p]
    places = [c.get("billing_address", ""), c.get("billing_city", "")]
    for s in c.get("sites") or []:
        places += [s.get("address", ""), s.get("town", "")]
    digits = [re.sub(r"\D", "", p) for p in phones]
    digits += ["0" + d[2:] for d in digits if d.startswith("27")]      # local form: 082...
    name_n = norm(c["name"])
    return {"_id": c["_id"], "name": c["name"], "name_n": name_n, "name_c": name_n.replace(" ", ""),
            "words": name_n.split(), "contact_n": norm(c.get("contact_person")),
            "phones": digits, "e164": [phone_e164(p) for p in phones],
            "email": (c.get("email") or "").strip().lower(), "ref": norm(c.get("ref")).replace(" ", ""),
            "places_n": norm(" ".join(p for p in places if p)),
            "last_order": c.get("last_order_date") or "", "last_activity": c.get("last_activity") or "",
            "row": row(c)}


def row(c: dict) -> dict:
    """What a result shows: enough to tell two people with the same name apart."""
    p = c.get("phone") or ""
    return {"_id": c["_id"], "ref": c.get("ref"), "name": c["name"], "suburb": suburb(c),
            "last_order_date": c.get("last_order_date"), "phone_last4": re.sub(r"\D", "", p)[-4:] if p else "",
            "phone": p, "price_tier": c.get("price_tier", "retail"), "payment_basis": c.get("payment_basis"),
            "active": c.get("active", True)}


def invalidate(node_id: str) -> None:
    _cache.pop(node_id, None)


async def index(node_id: str) -> list[dict]:
    hit = _cache.get(node_id)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    proj = {"name": 1, "contact_person": 1, "phone": 1, "other_phone": 1, "email": 1, "ref": 1,
            "billing_address": 1, "billing_city": 1, "sites.address": 1, "sites.town": 1, "price_tier": 1,
            "payment_basis": 1, "active": 1, "last_order_date": 1, "last_activity": 1}
    rows = [_entry(c) async for c in db.customers().find({"node_id": node_id}, proj)]
    _cache[node_id] = (time.monotonic(), rows)
    return rows


# ------------------------------------------------------------ search

def _rank(e: dict, qn: str, qc: str, qd: str, q164: str, phone_q: bool) -> tuple[int, float] | None:
    if q164.startswith("+") and q164 in e["e164"]:
        return 0, 1.0
    if qc and qc == e["ref"]:
        return 0, 1.0
    if phone_q:     # digits only: phone or ref digits, nothing else
        if any(qd in p for p in e["phones"]):
            return 4, 0.0
        if qd in e["ref"]:
            return 4, 0.0
        return None
    if e["name_n"].startswith(qn) or e["name_c"].startswith(qc):
        return 1, 0.0
    qw = qn.split()
    if qw and all(any(w.startswith(t) for w in e["words"]) for t in qw):
        return 2, 0.0
    if qc in e["name_c"]:
        return 3, 0.0
    if (qn in e["contact_n"] or qn in e["places_n"] or (qn and qn in norm(e["email"]))
            or (len(qd) >= 3 and any(qd in p for p in e["phones"])) or (len(qc) >= 3 and qc in e["ref"])):
        return 4, 0.0
    if len(qc) >= 4:
        best = max([similarity(qn, e["name_n"])] + [similarity(t, w) for t in qw if len(t) >= 4
                                                     for w in e["words"] if len(w) >= 3])
        if best >= 0.4:
            return 5, best
    return None


async def search(node_id: str, q: str, limit: int = LIMIT, include_inactive: bool = False) -> list[dict]:
    rows = await index(node_id)
    if not include_inactive:
        rows = [e for e in rows if e["row"]["active"] is not False]
    q = (q or "").strip()
    if not q:
        return recent(rows, limit)
    qn = norm(q)
    qc = qn.replace(" ", "")
    qd = re.sub(r"\D", "", q)
    phone_q = looks_like_phone(q)
    hits = []
    for e in rows:
        r = _rank(e, qn, qc, qd, phone_e164(q), phone_q)
        if r:
            hits.append((r[0], -r[1], e))
    # most recent order first within a rank: sort by date descending, then stable by rank
    hits.sort(key=lambda h: h[2]["last_order"] or "", reverse=True)
    hits.sort(key=lambda h: (h[0], h[1]))
    return [h[2]["row"] for h in hits[:limit]]


def recent(rows: list[dict], limit: int) -> list[dict]:
    """Empty box: customers with an order or other activity in the past 30 days, newest first."""
    since = (date.today() - timedelta(days=30)).isoformat()
    act = [e for e in rows if max(e["last_activity"], e["last_order"]) >= since]
    act.sort(key=lambda e: max(e["last_activity"], e["last_order"]), reverse=True)
    return [e["row"] for e in act[:limit]]


async def by_phone(node_id: str, phone: str) -> dict | None:
    """The customer a WhatsApp number belongs to (website orders attach to it)."""
    p = phone_e164(phone)
    if not p.startswith("+"):
        return None
    for e in await index(node_id):
        if p in e["e164"] and e["row"]["active"] is not False:
            return e["row"]
    return None


async def duplicates(node_id: str, phone: str | None, email: str | None, exclude_id: str | None = None) -> list[dict]:
    """Existing customers with the same phone or email."""
    p = phone_e164(phone)
    em = (email or "").strip().lower()
    out = []
    for e in await index(node_id):
        if e["_id"] == exclude_id:
            continue
        by = "phone" if p.startswith("+") and p in e["e164"] else "email" if em and em == e["email"] else None
        if by:
            out.append({**e["row"], "match": by})
    return out
