import uuid
import logging
from fastapi import APIRouter, Request, HTTPException
from app.models.schemas import Project, ProjectCreate, JobSummary, JobStatus, InputType
from app.db.hana import get_db, execute_dml, execute_query
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[Project])
async def list_projects(request: Request):
    user_id = request.state.user.get("sub", "unknown")
    async with get_db() as conn:
        rows = execute_query(
            conn,
            """
            SELECT P.*, COUNT(J.ID) AS JOB_COUNT
            FROM BOBJ_PROJECTS P
            LEFT JOIN BOBJ_CONVERSION_JOBS J ON J.PROJECT_ID = P.ID
            WHERE P.OWNER_USER_ID = ?
            GROUP BY P.ID, P.NAME, P.DESCRIPTION, P.BOBJ_SYSTEM_NAME,
                     P.DATASPHERE_SPACE_ID, P.SAC_TENANT_URL,
                     P.OWNER_USER_ID, P.CREATED_AT, P.UPDATED_AT
            ORDER BY P.UPDATED_AT DESC
            """,
            (user_id,),
        )
    return [_row_to_project(r) for r in rows]


@router.post("", response_model=Project, status_code=201)
async def create_project(request: Request, body: ProjectCreate):
    user_id = request.state.user.get("sub", "unknown")
    project_id = uuid.uuid4()
    async with get_db() as conn:
        execute_dml(
            conn,
            """
            INSERT INTO BOBJ_PROJECTS
              (ID, NAME, DESCRIPTION, BOBJ_SYSTEM_NAME,
               DATASPHERE_SPACE_ID, SAC_TENANT_URL, OWNER_USER_ID,
               CREATED_AT, UPDATED_AT)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                str(project_id), body.name, body.description,
                body.bobj_system_name, body.datasphere_space_id,
                body.sac_tenant_url, user_id,
            ),
        )
    return Project(id=project_id, owner_user_id=user_id, **body.model_dump())


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, request: Request):
    user_id = request.state.user.get("sub", "unknown")
    async with get_db() as conn:
        rows = execute_query(
            conn, "SELECT OWNER_USER_ID FROM BOBJ_PROJECTS WHERE ID = ?", (project_id,)
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Project not found")
        if rows[0]["owner_user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not your project")
        execute_dml(conn, "DELETE FROM BOBJ_PROJECTS WHERE ID = ?", (project_id,))


def _row_to_project(row: dict) -> Project:
    return Project(
        id=row["id"],
        name=row["name"],
        description=row.get("description"),
        bobj_system_name=row.get("bobj_system_name"),
        datasphere_space_id=row.get("datasphere_space_id"),
        sac_tenant_url=row.get("sac_tenant_url"),
        owner_user_id=row["owner_user_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        job_count=row.get("job_count", 0),
    )
