"""Partner auth realm: a separate login for each node's partner app.

A partner token is signed with PARTNER_JWT_SECRET (never JWT_SECRET) and carries
`aud: "partner"`. The internal API decodes with JWT_SECRET and no audience, so a partner
token fails there twice over; this module decodes with the partner secret and requires the
audience, so an internal token fails here. Every partner route takes its node_id from the
token only. Hashing is auth.py's PBKDF2, reused, not copied.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import auth
import db

PARTNER_JWT_SECRET = os.getenv("PARTNER_JWT_SECRET", "dev-partner-secret-change-in-prod")
AUDIENCE = "partner"
TOKEN_HOURS = int(os.getenv("PARTNER_TOKEN_HOURS", os.getenv("TOKEN_HOURS", "72")))

RESERVED_SLUGS = {"www", "api", "app", "admin"}

_bearer = HTTPBearer(auto_error=False)

hash_password = auth.hash_password
verify_password = auth.verify_password


def make_token(user: dict) -> str:
    payload = {
        "sub": user["email"],
        "node_id": user["node_id"],
        "role": user["role"],
        "employer": user.get("employer", "partner"),
        "aud": AUDIENCE,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, PARTNER_JWT_SECRET, algorithm="HS256")


async def current_partner(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, PARTNER_JWT_SECRET, algorithms=["HS256"],
                             audience=AUDIENCE)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    user = await db.partner_users().find_one({"email": payload["sub"], "node_id": payload["node_id"]})
    if not user or not user.get("active", True):
        raise HTTPException(401, "User not found")
    # The audit log keys on email + role; `employer` says which side the capturer works for.
    return {
        "email": user["email"], "name": user.get("name", ""), "node_id": user["node_id"],
        "role": user["role"], "employer": user.get("employer", "partner"),
    }


async def partner_admin(user: dict = Depends(current_partner)) -> dict:
    if user["role"] != "partner_admin":
        raise HTTPException(403, "Requires partner admin")
    return user


def public_user(u: dict) -> dict:
    return {k: v for k, v in u.items() if k != "password_hash"}
