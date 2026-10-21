"""Uploaded files: bill scans, proof-of-delivery photos, deposit slips, capture photos,
delivery documents.

Stored in the private Vercel Blob store (`vezubunye-files`) when BLOB_READ_WRITE_TOKEN is set,
else inline in the record (base64), so local dev and tests need no store. Files are only ever
served through authenticated routes; a private store has no public URLs.

Photos are shrunk on upload to at most SHRINK_MAX_PX on the long side as JPEG: a phone photo
drops from ~3 MB to ~0.4 MB and still reads. PDFs are stored as uploaded. The sha256 of the
bytes as uploaded is kept as evidence.

`store` returns the fields to save on the owning record; `load` reads them back, and also
reads records written before this module (`content_b64` / `photo_b64`).
"""
from __future__ import annotations

import base64
import hashlib
import io
import os
from uuid import uuid4

SHRINK_MAX_PX = 2000
SHRINK_QUALITY = 85
SHRINK_KEEP_BELOW = 600 * 1024     # a small JPEG/PNG within SHRINK_MAX_PX is kept as is
IMAGE_MIMES = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")


def blob_enabled() -> bool:
    return bool(os.getenv("BLOB_READ_WRITE_TOKEN"))


async def _blob_put(pathname: str, data: bytes, content_type: str) -> None:
    from vercel.blob import put_blob
    await put_blob(pathname, data, access="private", content_type=content_type, allow_overwrite=True)


async def _blob_get(pathname: str) -> bytes:
    from vercel.blob import get_blob
    return (await get_blob(pathname, access="private")).body


def shrink_image(content: bytes, mime: str) -> tuple[bytes, str, bool]:
    """(bytes, mime, shrunk). Re-encoded only when larger than SHRINK_MAX_PX or SHRINK_KEEP_BELOW,
    and only kept when it actually saves space."""
    from PIL import Image, ImageOps
    im = Image.open(io.BytesIO(content))
    if max(im.size) <= SHRINK_MAX_PX and len(content) <= SHRINK_KEEP_BELOW and mime in ("image/jpeg", "image/png"):
        return content, mime, False
    im = ImageOps.exif_transpose(im)
    if im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    if max(im.size) > SHRINK_MAX_PX:
        im.thumbnail((SHRINK_MAX_PX, SHRINK_MAX_PX))
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=SHRINK_QUALITY, optimize=True)
    jpeg = out.getvalue()
    if len(jpeg) >= len(content):
        return content, mime, False
    return jpeg, "image/jpeg", True


async def store(prefix: str, content: bytes, filename: str, mime: str, shrink: bool = True) -> dict:
    """Store one file. `prefix` groups files in the store, e.g. "pod/<node_id>"."""
    rec: dict = {"filename": filename or "file", "sha256": hashlib.sha256(content).hexdigest()}
    if shrink and mime in IMAGE_MIMES:
        try:
            stored, new_mime, shrunk = shrink_image(content, mime)
        except Exception as e:  # noqa: BLE001  (a photo Pillow cannot open is kept as uploaded)
            stored, new_mime, shrunk = content, mime, False
            rec["shrink_error"] = str(e)[:200]
        if shrunk:
            rec["original_size"] = len(content)
            rec["original_mime"] = mime
            if new_mime != mime:
                rec["filename"] = rec["filename"].rsplit(".", 1)[0] + ".jpg"
            content, mime = stored, new_mime
    rec.update(content_type=mime, size=len(content))
    if blob_enabled():
        safe = rec["filename"].replace("/", "_")
        pathname = f"{prefix}/{uuid4().hex}/{safe}"
        await _blob_put(pathname, content, mime)
        rec.update(storage="blob", blob_pathname=pathname)
    else:
        rec.update(storage="inline", content_b64=base64.b64encode(content).decode())
    return rec


async def load(rec: dict, legacy_field: str = "content_b64") -> bytes:
    if rec.get("storage") == "blob" and rec.get("blob_pathname"):
        return await _blob_get(rec["blob_pathname"])
    raw = rec.get("content_b64") or rec.get(legacy_field)
    if not raw:
        raise FileNotFoundError("No stored bytes")
    return base64.b64decode(raw)
