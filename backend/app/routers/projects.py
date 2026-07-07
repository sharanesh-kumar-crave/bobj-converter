import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request

from app.auth.users import get_current_user, require_role
from app.db.hana import execute_dml, execute_query, get_db
from app.models.schemas import Project, ProjectCreate

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("", response_model=list[Project])
async def list_projects(user: dict = Depends(get_current_user)):
    # Shared team tool: any authenticated user sees all projects (incl. legacy
    # anonymous-owned ones). Role gating on writes happens on the mutating routes.
    async with get_db() as conn:
        rows = execute_query(
            conn,
            """
            SELECT P.*,
                   (SELECT COUNT(*) FROM BOBJ_CONVERSION_JOBS J
                    WHERE J.PROJECT_ID = P.ID) AS JOB_COUNT
            FROM BOBJ_PROJECTS P
            ORDER BY P.UPDATED_AT DESC
            """,
            (),
        )
    return [_row_to_project(r) for r in rows]


@router.get("/{project_id}/conversions")
async def list_project_conversions(project_id: str, user: dict = Depends(get_current_user)):
    """Conversions recorded for a project (from HANA, so it matches job_count)."""
    async with get_db() as conn:
        rows = execute_query(
            conn,
            """
            SELECT ID, PROJECT_ID, ARTIFACT_NAME, INPUT_TYPE, STATUS,
                   TOTAL_OBJECTS, CONVERTED_COUNT, CREATED_AT, COMPLETED_AT
            FROM BOBJ_CONVERSION_JOBS
            WHERE PROJECT_ID = ?
            ORDER BY CREATED_AT DESC
            """,
            (project_id,),
        )
    return rows


@router.post("", response_model=Project, status_code=201)
async def create_project(body: ProjectCreate, user: dict = Depends(require_role("editor"))):
    user_id = user["id"]
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
                str(project_id),
                body.name,
                body.description,
                body.bobj_system_name,
                body.datasphere_space_id,
                body.sac_tenant_url,
                user_id,
            ),
        )
    return Project(id=project_id, owner_user_id=user_id, **body.model_dump())


@router.delete("/{project_id}", status_code=204)
async def delete_project(project_id: str, user: dict = Depends(require_role("editor"))):
    async with get_db() as conn:
        rows = execute_query(
            conn, "SELECT ID FROM BOBJ_PROJECTS WHERE ID = ?", (project_id,)
        )
        if not rows:
            raise HTTPException(status_code=404, detail="Project not found")
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
