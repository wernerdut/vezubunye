"""Email/password auth with JWT role claims.

Passwords: PBKDF2-HMAC-SHA256 (stdlib, no extra deps).
Tokens: HS256 JWT carrying email, role, node_access.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

import db

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-prod")
TOKEN_HOURS = int(os.getenv("TOKEN_HOURS", "72"))
_ITERATIONS = 200_000

_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_b64, dk_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(dk_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


def make_token(user: dict) -> str:
    payload = {
        "sub": user["email"],
        "role": user["role"],
        "node_access": user.get("node_access", "all"),
        "name": user.get("name", ""),
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_HOURS),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if creds is None:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    user = await db.users().find_one({"email": payload["sub"]})
    if not user:
        raise HTTPException(401, "User not found")
    return {
        "email": user["email"],
        "role": user["role"],
        "node_access": user.get("node_access", "all"),
        "name": user.get("name", ""),
    }


def require_role(*roles: str):
    async def dep(user: dict = Depends(current_user)) -> dict:
        if user["role"] not in roles:
            raise HTTPException(403, f"Requires role: {', '.join(roles)}")
        return user
    return dep


def check_node_access(user: dict, node_id: str):
    access = user.get("node_access", [])
    if access == "all" or user["role"] == "admin":
        return
    if node_id not in access:
        raise HTTPException(403, f"No access to node {node_id}")
