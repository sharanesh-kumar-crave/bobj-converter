from fastapi import APIRouter

from app.db.hana import execute_query, get_db

router = APIRouter()


@router.get("")
async def health():
    try:
        async with get_db() as conn:
            execute_query(conn, "SELECT 1 FROM DUMMY")
        db_status = "ok"
    except Exception as e:
        db_status = f"error: {str(e)[:100]}"
    return {"status": "ok", "db": db_status}
