"""Bearer API-key auth for /v1."""
from __future__ import annotations
import hashlib, hmac, secrets
from fastapi import Depends, Header, HTTPException, Request
from app.core import config
from app.v1 import db

def gen_key() -> str:
    return "sk_" + secrets.token_urlsafe(32)

def hash_key(key: str) -> str:
    return hashlib.sha256((config.API_KEY_SALT + key).encode()).hexdigest()

def mint_key(conn, user_name: str, is_admin: bool = False):
    uid = db.create_user(conn, user_name, is_admin=is_admin)
    key = gen_key(); prefix = key[:10]
    kid = db.create_api_key(conn, uid, key_hash=hash_key(key), key_prefix=prefix)
    return key, prefix, kid, uid

def _bearer(value: str | None) -> str | None:
    if not value or not value.startswith("Bearer "):
        return None
    return value[len("Bearer "):].strip()

def _resolve_user(conn, authorization: str | None) -> str:
    token = _bearer(authorization)
    if not token:
        raise HTTPException(status_code=401, detail="missing bearer token")
    row = db.get_key_by_hash(conn, hash_key(token))
    if row is None:
        raise HTTPException(status_code=401, detail="invalid api key")
    return row["user_id"]

def _is_admin(authorization: str | None) -> bool:
    token = _bearer(authorization)
    admin = config.ADMIN_API_KEY
    return bool(admin) and token is not None and hmac.compare_digest(token, admin)

# FastAPI deps (conn provided by app state via dependency in routes module)
def require_user(request: Request, authorization: str | None = Header(default=None)) -> str:
    return _resolve_user(request.app.state.db, authorization)

def require_admin(authorization: str | None = Header(default=None)) -> bool:
    if not _is_admin(authorization):
        raise HTTPException(status_code=403, detail="admin only")
    return True
