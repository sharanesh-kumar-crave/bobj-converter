from fastapi import APIRouter, Request

from app.db.hana import execute_query, get_db
from app.models.schemas import InputType, JobStatus, JobSummary

router = APIRouter()


@router.get("", response_model=list[JobSummary])
async def list_jobs(
    request: Request,
    project_id: str | None = None,
    status: JobStatus | None = None,
    limit: int = 50,
):
    user_id = request.state.user.get("sub", "unknown")
    filters = ["OWNER_USER_ID = ?"]
    params: list = [user_id]
    if project_id:
        filters.append("PROJECT_ID = ?")
        params.append(project_id)
    if status:
        filters.append("STATUS = ?")
        params.append(status.value)
    where = " AND ".join(filters)
    params.append(limit)

    async with get_db() as conn:
        rows = execute_query(
            conn,
            f"""
            SELECT ID, PROJECT_ID, ARTIFACT_NAME, INPUT_TYPE,
                   STATUS, TOTAL_OBJECTS, CONVERTED_COUNT,
                   CREATED_AT, COMPLETED_AT
            FROM BOBJ_CONVERSION_JOBS
            WHERE {where}
            ORDER BY CREATED_AT DESC
            LIMIT ?
            """,
            tuple(params),
        )
    return [
        JobSummary(
            id=r["id"],
            project_id=r.get("project_id"),
            artifact_name=r["artifact_name"],
            input_type=InputType(r["input_type"]),
            status=JobStatus(r["status"]),
            total_objects=r.get("total_objects"),
            converted=r.get("converted_count"),
            created_at=r["created_at"],
            completed_at=r.get("completed_at"),
        )
        for r in rows
    ]
