"""
Admin monitoring API endpoints.
Exposes metrics, DLQ management, and health details.
Protected by admin scope.
"""
import logging
from fastapi import APIRouter, Request, Depends, HTTPException
from app.auth.xsuaa import require_scope
from app.monitoring.metrics import metrics
from app.db.hana import get_db, execute_query, execute_dml

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/metrics")
async def get_metrics(_=Depends(require_scope("read"))):
    """Return current application metrics snapshot."""
    return await metrics.snapshot()


@router.get("/metrics/history")
async def get_metrics_history(
    hours: int = 24,
    _=Depends(require_scope("read")),
):
    """Return historical metrics from HANA for trend charts."""
    async with get_db() as conn:
        rows = execute_query(
            conn,
            """
            SELECT
                HOUR(CREATED_AT)        AS hour,
                COUNT(*)                AS total_jobs,
                SUM(CASE WHEN STATUS = 'completed' THEN 1 ELSE 0 END) AS completed,
                SUM(CASE WHEN STATUS = 'failed'    THEN 1 ELSE 0 END) AS failed,
                AVG(TOTAL_OBJECTS)      AS avg_objects,
                AVG(CONVERTED_COUNT)    AS avg_converted
            FROM BOBJ_CONVERSION_JOBS
            WHERE CREATED_AT >= ADD_SECONDS(NOW(), ?)
            GROUP BY HOUR(CREATED_AT)
            ORDER BY HOUR(CREATED_AT)
            """,
            (-hours * 3600,),
        )
    return {"hours": hours, "data": rows}


@router.get("/metrics/jobs/breakdown")
async def get_job_breakdown(_=Depends(require_scope("read"))):
    """Return job breakdown by status and input type."""
    async with get_db() as conn:
        by_status = execute_query(
            conn,
            """
            SELECT STATUS, COUNT(*) AS count
            FROM BOBJ_CONVERSION_JOBS
            GROUP BY STATUS
            """,
        )
        by_type = execute_query(
            conn,
            """
            SELECT INPUT_TYPE, COUNT(*) AS count,
                   AVG(TOTAL_OBJECTS) AS avg_objects
            FROM BOBJ_CONVERSION_JOBS
            GROUP BY INPUT_TYPE
            """,
        )
        recent_failures = execute_query(
            conn,
            """
            SELECT ID, ARTIFACT_NAME, ERROR_MESSAGE, CREATED_AT, RETRY_COUNT
            FROM BOBJ_CONVERSION_JOBS
            WHERE STATUS = 'failed'
            ORDER BY CREATED_AT DESC
            LIMIT 10
            """,
        )
    return {
        "by_status":       by_status,
        "by_input_type":   by_type,
        "recent_failures": recent_failures,
    }


@router.get("/dlq")
async def list_dlq(
    limit: int = 50,
    _=Depends(require_scope("admin")),
):
    """List jobs in the Dead Letter Queue."""
    async with get_db() as conn:
        rows = execute_query(
            conn,
            """
            SELECT D.ID, D.JOB_ID, D.ARTIFACT_NAME, D.INPUT_TYPE,
                   D.ERROR_MESSAGE, D.ATTEMPTS, D.CREATED_AT,
                   D.RESOLVED, D.RESOLVED_AT
            FROM CONVERSION_DLQ D
            ORDER BY D.CREATED_AT DESC
            LIMIT ?
            """,
            (limit,),
        )
    return {"count": len(rows), "items": rows}


@router.post("/dlq/{dlq_id}/requeue")
async def requeue_dlq_job(
    dlq_id: str,
    request: Request,
    _=Depends(require_scope("admin")),
):
    """Requeue a DLQ job for another conversion attempt."""
    from fastapi import BackgroundTasks
    from app.monitoring.retry import run_with_retry
    import uuid

    async with get_db() as conn:
        rows = execute_query(
            conn,
            """
            SELECT D.*, J.RAW_CONTENT
            FROM CONVERSION_DLQ D
            JOIN BOBJ_CONVERSION_JOBS J ON J.ID = D.JOB_ID
            WHERE D.ID = ? AND D.RESOLVED = FALSE
            """,
            (dlq_id,),
        )

    if not rows:
        raise HTTPException(status_code=404, detail="DLQ item not found or already resolved")

    item = rows[0]
    new_job_id = uuid.uuid4()

    async with get_db() as conn:
        # Mark DLQ item as resolved
        execute_dml(
            conn,
            "UPDATE CONVERSION_DLQ SET RESOLVED = TRUE, RESOLVED_AT = CURRENT_TIMESTAMP WHERE ID = ?",
            (dlq_id,),
        )
        # Create new job
        execute_dml(
            conn,
            """
            INSERT INTO BOBJ_CONVERSION_JOBS
              (ID, ARTIFACT_NAME, INPUT_TYPE, RAW_CONTENT, STATUS, OWNER_USER_ID, CREATED_AT)
            VALUES (?, ?, ?, ?, 'pending', ?, CURRENT_TIMESTAMP)
            """,
            (
                str(new_job_id),
                item["artifact_name"],
                item["input_type"],
                item["raw_content"],
                request.state.user.get("sub", "admin"),
            ),
        )

    logger.info("DLQ job requeued", extra={"dlq_id": dlq_id, "new_job_id": str(new_job_id)})
    return {"message": "Job requeued", "new_job_id": str(new_job_id)}


@router.delete("/dlq/{dlq_id}")
async def dismiss_dlq_job(
    dlq_id: str,
    _=Depends(require_scope("admin")),
):
    """Dismiss (permanently discard) a DLQ job."""
    async with get_db() as conn:
        execute_dml(
            conn,
            "UPDATE CONVERSION_DLQ SET RESOLVED = TRUE, RESOLVED_AT = CURRENT_TIMESTAMP WHERE ID = ?",
            (dlq_id,),
        )
    return {"message": "DLQ item dismissed"}


@router.get("/health/detailed")
async def detailed_health():
    """Extended health check with component status."""
    results = {"status": "ok", "components": {}}

    # HANA
    try:
        async with get_db() as conn:
            execute_query(conn, "SELECT 1 FROM DUMMY")
        results["components"]["hana"] = {"status": "ok"}
    except Exception as e:
        results["components"]["hana"] = {"status": "error", "detail": str(e)[:100]}
        results["status"] = "degraded"

    # Metrics snapshot
    results["metrics"] = await metrics.snapshot()
    return results
