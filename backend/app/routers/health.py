from fastapi import APIRouter
from app.db.hana import get_db, execute_query

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
