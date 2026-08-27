import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.users import (
    _ROLES,
    create_user,
    get_user_by_id,
    hash_password,
    require_role,
)
from app.db.hana import execute_dml, execute_query, get_db
from app.models.schemas import PasswordReset, UserCreate, UserOut, UserUpdate

logger = logging.getLogger(__name__)
router = APIRouter()


def _row_to_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "username": row["username"],
        "email": row.get("email"),
        "role": row.get("role", "viewer"),
        "is_active": bool(row.get("is_active", 1)),
        "created_at": row.get("created_at"),
    }


async def _active_admin_count() -> int:
    async with get_db() as conn:
        rows = execute_query(
            conn,
            "SELECT COUNT(*) AS N FROM BOBJ_USERS WHERE ROLE = 'admin' AND IS_ACTIVE = 1",
            (),
        )
    return rows[0].get("n", 0) if rows else 0


@router.get("", response_model=list[UserOut])
async def list_users(_: dict = Depends(require_role("admin"))):
    async with get_db() as conn:
        rows = execute_query(conn, "SELECT * FROM BOBJ_USERS ORDER BY CREATED_AT DESC", ())
    return [_row_to_out(r) for r in rows]


@router.post("", response_model=UserOut, status_code=201)
async def add_user(body: UserCreate, _: dict = Depends(require_role("admin"))):
    user = await create_user(body.username, body.password, role=body.role, email=body.email)
    return _row_to_out({**user, "is_active": 1})


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: str, body: UserUpdate, admin: dict = Depends(require_role("admin"))):
    row = await get_user_by_id(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")

    new_role = body.role if body.role is not None else row.get("role")
    if new_role not in _ROLES:
        raise HTTPException(status_code=422, detail=f"Invalid role: {new_role}")
    new_active = int(body.is_active) if body.is_active is not None else int(row.get("is_active", 1))
    new_email = body.email if body.email is not None else row.get("email")

    # Guard: don't strip the last active admin (by demotion or deactivation).
    was_active_admin = row.get("role") == "admin" and int(row.get("is_active", 1)) == 1
    still_active_admin = new_role == "admin" and new_active == 1
    if was_active_admin and not still_active_admin and await _active_admin_count() <= 1:
        raise HTTPException(
            status_code=409, detail="Cannot demote/deactivate the last active admin"
        )

    async with get_db() as conn:
        execute_dml(
            conn,
            "UPDATE BOBJ_USERS SET ROLE = ?, IS_ACTIVE = ?, EMAIL = ?, UPDATED_AT = CURRENT_TIMESTAMP WHERE ID = ?",
            (new_role, new_active, new_email, user_id),
        )
    updated = await get_user_by_id(user_id)
    if not updated:
        raise HTTPException(status_code=404, detail="User not found")
    return _row_to_out(updated)


@router.post("/{user_id}/password", status_code=204)
async def reset_password(
    user_id: str, body: PasswordReset, _: dict = Depends(require_role("admin"))
):
    row = await get_user_by_id(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    async with get_db() as conn:
        execute_dml(
            conn,
            "UPDATE BOBJ_USERS SET PASSWORD_HASH = ?, UPDATED_AT = CURRENT_TIMESTAMP WHERE ID = ?",
            (hash_password(body.password), user_id),
        )


@router.delete("/{user_id}", status_code=204)
async def delete_user(user_id: str, admin: dict = Depends(require_role("admin"))):
    row = await get_user_by_id(user_id)
    if not row:
        raise HTTPException(status_code=404, detail="User not found")
    if user_id == admin["id"]:
        raise HTTPException(status_code=409, detail="You cannot delete your own account")
    if (
        row.get("role") == "admin"
        and int(row.get("is_active", 1)) == 1
        and await _active_admin_count() <= 1
    ):
        raise HTTPException(status_code=409, detail="Cannot delete the last active admin")
    async with get_db() as conn:
        execute_dml(conn, "DELETE FROM BOBJ_USERS WHERE ID = ?", (user_id,))
