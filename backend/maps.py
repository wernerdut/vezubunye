"""Delivery-site locations, Google Maps links and WhatsApp share links. No API keys needed.

A pasted Maps link is always stored as given. Short links (maps.app.goo.gl, goo.gl/maps) are
resolved by following the redirect server-side; coordinates are parsed from the final URL
(`!3d..!4d..` first, then `q=`/`ll=`/`query=`, then `@lat,lng`) and must fall inside South
Africa. When that fails the link is kept and the site shows "location not pinned", so a
location is never silently wrong.
"""
from __future__ import annotations

import re
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

_NUM = r"(-?\d{1,3}(?:\.\d+)?)"
_AT = re.compile(r"@" + _NUM + "," + _NUM)
_D34 = re.compile(r"!3d" + _NUM + r"!4d" + _NUM)
_PAIR = re.compile(r"^\s*" + _NUM + r"\s*,\s*" + _NUM + r"\s*$")
SHORT_HOSTS = ("maps.app.goo.gl", "goo.gl")


def _valid(lat: float, lng: float) -> bool:
    return -90 <= lat <= 90 and -180 <= lng <= 180 and not (lat == 0 and lng == 0)


def parse_coords(url: str) -> tuple[float, float] | None:
    """Coordinates from a full Google Maps URL, or None. `!3d!4d` (the pin itself) wins over
    `@lat,lng` (the camera centre) when both are present."""
    if not url:
        return None
    text = unquote(url)
    m = _D34.search(text)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if _valid(lat, lng):
            return lat, lng
    qs = parse_qs(urlparse(text).query)
    for key in ("q", "query", "destination", "ll", "daddr"):
        for v in qs.get(key, []):
            pm = _PAIR.match(v)
            if pm:
                lat, lng = float(pm.group(1)), float(pm.group(2))
                if _valid(lat, lng):
                    return lat, lng
    m = _AT.search(text)
    if m:
        lat, lng = float(m.group(1)), float(m.group(2))
        if _valid(lat, lng):
            return lat, lng
    return None


# South Africa (with a margin): a pin outside it is a wrong link, not a delivery site
SA_LAT = (-35.5, -21.5)
SA_LNG = (16.0, 33.5)
NOT_READ = "Couldn't read a location from that link, drop the pin on the map instead."


def in_sa(lat: float, lng: float) -> bool:
    return SA_LAT[0] <= lat <= SA_LAT[1] and SA_LNG[0] <= lng <= SA_LNG[1]


def is_short(url: str) -> bool:
    host = urlparse(url.strip()).netloc.lower()
    path = urlparse(url.strip()).path
    return host == "maps.app.goo.gl" or (host == "goo.gl" and path.startswith("/maps"))


async def resolve(url: str, client: httpx.AsyncClient | None = None) -> str:
    """Follow a short link's redirects and return the final URL (the input on any failure)."""
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, timeout=10)
    try:
        r = await client.get(url.strip())
        return str(r.url)
    except Exception:  # noqa: BLE001
        return url
    finally:
        if own:
            await client.aclose()


async def locate(url: str, client: httpx.AsyncClient | None = None) -> dict:
    """{maps_link, resolved_link, lat, lng, pinned} for a pasted link."""
    url = (url or "").strip()
    final = await resolve(url, client) if is_short(url) else url
    coords = parse_coords(final) or parse_coords(url)
    if coords and not in_sa(*coords):
        coords = None
    return {"maps_link": url, "resolved_link": final if final != url else None,
            "lat": coords[0] if coords else None, "lng": coords[1] if coords else None,
            "pinned": bool(coords), "message": None if coords else NOT_READ}


def nav_link(site: dict | None) -> str:
    """Google Maps navigation for any phone: coordinates first, then the stored link, then
    the address text."""
    site = site or {}
    if site.get("lat") is not None and site.get("lng") is not None:
        return f"https://www.google.com/maps/dir/?api=1&destination={site['lat']},{site['lng']}"
    if site.get("maps_link"):
        return site["maps_link"]
    addr = ", ".join(x for x in (site.get("address"), site.get("town")) if x)
    if addr:
        return f"https://www.google.com/maps/dir/?api=1&destination={quote(addr)}"
    return ""


def intl_phone(phone: str, country: str = "27") -> str:
    """Digits only, international format (0821234567 -> 27821234567)."""
    digits = re.sub(r"\D", "", phone or "")
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = country + digits[1:]
    return digits


def wa_link(phone: str, text: str) -> str:
    num = intl_phone(phone)
    base = f"https://wa.me/{num}" if num else "https://wa.me/"
    return f"{base}?text={quote(text)}"
