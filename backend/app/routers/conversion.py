import uuid
import logging
from datetime import datetime
from fastapi import APIRouter, Request, Depends, BackgroundTasks, HTTPException

from app.models.schemas import ConversionRequest, ConversionResult, JobStatus
from app.services.ai_core import run_conversion
from app.services.datasphere import push_entities
from app.services.sac import push_model
from app.db.hana import get_db, execute_dml, execute_query

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("", response_model=ConversionResult, status_code=202)
async def start_conversion(
    request: Request,
    body: ConversionRequest,
    background_tasks: BackgroundTasks,
):
    """
    Submit a BOBJ artifact for conversion.
    Returns immediately with a job_id; conversion runs in the background.
    Poll GET /api/v1/jobs/{job_id} for status.
    """
    job_id = uuid.uuid4()
    user_id = request.state.user.get("sub", "unknown")

    async with get_db() as conn:
        execute_dml(
            conn,
            """
            INSERT INTO BOBJ_CONVERSION_JOBS
              (ID, PROJECT_ID, ARTIFACT_NAME, INPUT_TYPE, RAW_CONTENT,
               STATUS, OWNER_USER_ID, CREATED_AT)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                str(job_id),
                str(body.project_id) if body.project_id else None,
                body.artifact_name,
                body.input_type.value,
                body.raw_content,
                JobStatus.pending.value,
                user_id,
            ),
        )

    background_tasks.add_task(
        _run_conversion_pipeline, job_id, body
    )

    return ConversionResult(
        job_id=job_id,
        project_id=body.project_id,
        status=JobStatus.pending,
    )


@router.get("/{job_id}", response_model=ConversionResult)
async def get_conversion_result(job_id: str):
    """Fetch the full result for a completed conversion job."""
    async with get_db() as conn:
        rows = execute_query(
            conn,
            "SELECT * FROM BOBJ_CONVERSION_JOBS WHERE ID = ?",
            (job_id,),
        )
    if not rows:
        raise HTTPException(status_code=404, detail="Job not found")

    row = rows[0]
    import json
    result_json = json.loads(row.get("result_json") or "{}")

    return ConversionResult(
        job_id=row["id"],
        project_id=row.get("project_id"),
        status=JobStatus(row["status"]),
        datasphere_entities=result_json.get("datasphereEntities", []),
        sac_model_config=result_json.get("sacModelConfig"),
        conversion_mapping=result_json.get("conversionMapping", []),
        summary=result_json.get("summary"),
        created_at=row["created_at"],
        completed_at=row.get("completed_at"),
        error=row.get("error_message"),
    )


async def _run_conversion_pipeline(job_id: uuid.UUID, body: ConversionRequest):
    """Background task: run AI conversion, persist result, push to Datasphere + SAC."""
    try:
        # 1 — Update status to running
        async with get_db() as conn:
            execute_dml(
                conn,
                "UPDATE BOBJ_CONVERSION_JOBS SET STATUS = ? WHERE ID = ?",
                (JobStatus.running.value, str(job_id)),
            )

        # 2 — Call SAP AI Core
        result = await run_conversion(body.input_type.value, body.raw_content)

        # 3 — Push to Datasphere
        ds_result = await push_entities(result.get("datasphereEntities", []))
        logger.info("Datasphere push: %s", ds_result)

        # 4 — Push to SAC
        sac_result = await push_model(result.get("sacModelConfig", {}))
        logger.info("SAC push: %s", sac_result)

        # 5 — Persist full result to HANA
        import json
        summary = result.get("summary", {})
        async with get_db() as conn:
            execute_dml(
                conn,
                """
                UPDATE BOBJ_CONVERSION_JOBS SET
                  STATUS = ?, RESULT_JSON = ?,
                  TOTAL_OBJECTS = ?, CONVERTED_COUNT = ?,
                  COMPLETED_AT = CURRENT_TIMESTAMP
                WHERE ID = ?
                """,
                (
                    JobStatus.completed.value,
                    json.dumps(result),
                    summary.get("totalObjects", 0),
                    summary.get("converted", 0),
                    str(job_id),
                ),
            )

    except Exception as e:
        logger.exception("Conversion pipeline failed for job %s", job_id)
        async with get_db() as conn:
            execute_dml(
                conn,
                """
                UPDATE BOBJ_CONVERSION_JOBS SET
                  STATUS = ?, ERROR_MESSAGE = ?, COMPLETED_AT = CURRENT_TIMESTAMP
                WHERE ID = ?
                """,
                (JobStatus.failed.value, str(e)[:1000], str(job_id)),
            )
