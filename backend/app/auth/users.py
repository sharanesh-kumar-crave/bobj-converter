"""Self-contained user auth: PBKDF2 password hashing (stdlib), PyJWT tokens,
and FastAPI dependencies for role-based access control.

Deliberately avoids compiled password-hashing wheels (bcrypt/argon2) — the CF
python buildpack has been fragile with native deps, so we use hashlib.pbkdf2_hmac.
"""

import base64
import hashlib
import hmac
import logging
import os
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from fastapi import Depends, HTTPException, Request, status

from app.db.hana import execute_dml, execute_query, get_db

logger = logging.getLogger(__name__)

_PBKDF2_ITERATIONS = 200_000
_TOKEN_TTL_HOURS = 12
_ROLES = ("admin", "editor", "viewer")

# Stable JWT secret. Set AUTH_SECRET in the CF env so tokens survive restarts;
# the fallback keeps local/dev working but logs a warning.
_SECRET: str = os.getenv("AUTH_SECRET") or "datahub-dev-secret-change-me"
if not os.getenv("AUTH_SECRET"):
    logger.warning("AUTH_SECRET not set — using an insecure dev fallback. Set it in the CF env.")


# ─── Password hashing ─────────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS)
    return (
        f"pbkdf2_sha256${_PBKDF2_ITERATIONS}$"
        f"{base64.b64encode(salt).decode()}${base64.b64encode(dk).decode()}"
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iters, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iters))
        return hmac.compare_digest(dk, expected)
    except Exception:
        return False


# ─── Tokens ───────────────────────────────────────────────────────────────────
def create_token(user: dict) -> str:
    payload = {
        "sub": user["id"],
        "username": user["username"],
        "role": user["role"],
        "exp": datetime.now(UTC) + timedelta(hours=_TOKEN_TTL_HOURS),
    }
    return jwt.encode(payload, _SECRET, algorithm="HS256")


def _decode_token(token: str) -> dict:
    return jwt.decode(token, _SECRET, algorithms=["HS256"])


# ─── User data access ─────────────────────────────────────────────────────────
def _row_to_user(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row.get("email"),
        "role": row.get("role", "viewer"),
        "is_active": bool(row.get("is_active", 1)),
        "created_at": row.get("created_at"),
    }


async def get_user_by_username(username: str) -> dict | None:
    async with get_db() as conn:
        rows = execute_query(
            conn,
            "SELECT * FROM BOBJ_USERS WHERE LOWER(USERNAME) = LOWER(?)",
            (username,),
        )
    return rows[0] if rows else None


async def get_user_by_id(user_id: str) -> dict | None:
    async with get_db() as conn:
        rows = execute_query(conn, "SELECT * FROM BOBJ_USERS WHERE ID = ?", (user_id,))
    return rows[0] if rows else None


async def create_user(
    username: str, password: str, role: str = "viewer", email: str | None = None
) -> dict:
    if role not in _ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {role}")
    existing = await get_user_by_username(username)
    if existing:
        raise HTTPException(status_code=409, detail="Username already exists")
    user_id = str(uuid.uuid4())
    async with get_db() as conn:
        execute_dml(
            conn,
            """
            INSERT INTO BOBJ_USERS (ID, USERNAME, EMAIL, PASSWORD_HASH, ROLE, IS_ACTIVE)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (user_id, username, email, hash_password(password), role),
        )
    return {"id": user_id, "username": username, "email": email, "role": role, "is_active": True}


# ─── FastAPI dependencies ─────────────────────────────────────────────────────
async def get_current_user(request: Request) -> dict:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = auth[7:]
    try:
        payload = _decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired"
        ) from None
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token"
        ) from None
    # Load fresh from DB so role changes / deactivation take effect immediately.
    row = await get_user_by_id(payload.get("sub", ""))
    if not row or not bool(row.get("is_active", 1)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account inactive")
    return _row_to_user(row)


def require_role(*roles: str):
    """Dependency factory — admin is always allowed; otherwise role must match."""

    async def _check(user: dict = Depends(get_current_user)) -> dict:
        if user["role"] != "admin" and user["role"] not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient role for this action",
            )
        return user

    return _check


# ─── Startup seed ─────────────────────────────────────────────────────────────
async def seed_default_admin() -> None:
    """Create a default admin if the users table is empty, so the app is usable."""
    try:
        async with get_db() as conn:
            rows = execute_query(conn, "SELECT COUNT(*) AS N FROM BOBJ_USERS", ())
        count = rows[0].get("n", 0) if rows else 0
        if count:
            return
        password = os.getenv("ADMIN_DEFAULT_PASSWORD", "admin123")
        await create_user("admin", password, role="admin", email="admin@craveinfotech.com")
        logger.warning(
            "Seeded default admin user 'admin'. CHANGE THE PASSWORD via User Management immediately."
        )
    except Exception:
        logger.exception("Failed to seed default admin user")
