import logging

from fastapi import APIRouter, Depends, HTTPException

from app.auth.users import create_token, get_current_user, get_user_by_username, verify_password
from app.models.schemas import LoginRequest, LoginResponse

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    row = await get_user_by_username(body.username)
    if not row or not bool(row.get("is_active", 1)) or not verify_password(body.password, row["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    user = {"id": row["id"], "username": row["username"], "role": row.get("role", "viewer")}
    token = create_token(user)
    logger.info("User logged in: %s (%s)", row["username"], user["role"])
    return LoginResponse(
        access_token=token,
        username=row["username"],
        role=user["role"],
        email=row.get("email"),
    )


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    return user
