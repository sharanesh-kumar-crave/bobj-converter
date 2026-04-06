"""
Job retry engine with exponential backoff and dead letter queue (DLQ).
Retries failed conversions up to MAX_ATTEMPTS with jitter.
DLQ stores permanently failed jobs for manual review.
"""
import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Optional

from app.monitoring.metrics import metrics

logger = logging.getLogger(__name__)

MAX_ATTEMPTS    = 3
BASE_DELAY_S    = 5.0    # initial retry delay in seconds
MAX_DELAY_S     = 60.0   # cap on retry delay
JITTER_FACTOR   = 0.3    # ±30% jitter to avoid thundering herd


def _backoff_delay(attempt: int) -> float:
    """Exponential backoff with jitter: delay = min(base * 2^attempt, max) ± jitter."""
    delay = min(BASE_DELAY_S * (2 ** (attempt - 1)), MAX_DELAY_S)
    jitter = delay * JITTER_FACTOR * (random.random() * 2 - 1)
    return max(1.0, delay + jitter)


async def run_with_retry(
    job_id: uuid.UUID,
    input_type: str,
    raw_content: str,
    artifact_name: str,
    project_id: Optional[uuid.UUID] = None,
) -> dict:
    """
    Execute the full conversion pipeline with retry logic.
    On permanent failure, writes to DLQ and raises.
    """
    from app.services.ai_core import run_conversion
    from app.services.datasphere import push_entities
    from app.services.sac import push_model
    from app.db.hana import get_db, execute_dml
    from app.models.schemas import JobStatus

    last_error: Optional[Exception] = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            logger.info(
                "Starting conversion attempt",
                extra={"job_id": str(job_id), "attempt": attempt, "max_attempts": MAX_ATTEMPTS},
            )

            start = asyncio.get_event_loop().time()

            # ── Run AI conversion ─────────────────────────────────────────────
            ai_start = asyncio.get_event_loop().time()
            result = await run_conversion(input_type, raw_content)
            await metrics.record_ai_core_latency(asyncio.get_event_loop().time() - ai_start)

            # ── Push to Datasphere ────────────────────────────────────────────
            entities = result.get("datasphereEntities", [])
            ds_result = await push_entities(entities)
            ds_ok = not ds_result.get("failed")
            await metrics.record_ds_push(ds_ok, len(entities))

            # ── Push to SAC ───────────────────────────────────────────────────
            sac_result = await push_model(result.get("sacModelConfig", {}))
            sac_ok = sac_result.get("status") == "created"
            await metrics.record_sac_push(sac_ok)

            duration = asyncio.get_event_loop().time() - start
            summary = result.get("summary", {})

            await metrics.record_job_completed(
                str(job_id),
                duration,
                summary.get("totalObjects", 0),
                summary.get("converted", 0),
            )

            # ── Persist result ────────────────────────────────────────────────
            import json
            async with get_db() as conn:
                execute_dml(
                    conn,
                    """
                    UPDATE BOBJ_CONVERSION_JOBS SET
                      STATUS = ?, RESULT_JSON = ?,
                      TOTAL_OBJECTS = ?, CONVERTED_COUNT = ?,
                      COMPLETED_AT = CURRENT_TIMESTAMP,
                      RETRY_COUNT = ?
                    WHERE ID = ?
                    """,
                    (
                        JobStatus.completed.value,
                        json.dumps(result),
                        summary.get("totalObjects", 0),
                        summary.get("converted", 0),
                        attempt - 1,
                        str(job_id),
                    ),
                )

            logger.info(
                "Conversion succeeded",
                extra={"job_id": str(job_id), "attempt": attempt, "duration_s": round(duration, 2)},
            )
            return result

        except Exception as e:
            last_error = e
            await metrics.record_job_failed(str(job_id), str(e), attempt)

            if attempt < MAX_ATTEMPTS:
                delay = _backoff_delay(attempt)
                await metrics.record_job_retried(str(job_id), attempt + 1)
                logger.warning(
                    "Conversion failed — retrying",
                    extra={
                        "job_id":      str(job_id),
                        "attempt":     attempt,
                        "next_attempt": attempt + 1,
                        "delay_s":     round(delay, 1),
                        "error":       str(e)[:200],
                    },
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "Conversion permanently failed — sending to DLQ",
                    extra={"job_id": str(job_id), "attempts": MAX_ATTEMPTS, "error": str(e)[:300]},
                )

    # ── Dead letter queue ─────────────────────────────────────────────────────
    await _write_dlq(job_id, input_type, artifact_name, str(last_error), MAX_ATTEMPTS)
    raise last_error


async def _write_dlq(
    job_id: uuid.UUID,
    input_type: str,
    artifact_name: str,
    error: str,
    attempts: int,
):
    """Write permanently failed job to the DLQ table for manual review."""
    try:
        from app.db.hana import get_db, execute_dml
        from app.models.schemas import JobStatus

        async with get_db() as conn:
            # Update job status to failed
            execute_dml(
                conn,
                """
                UPDATE BOBJ_CONVERSION_JOBS SET
                  STATUS = ?, ERROR_MESSAGE = ?,
                  RETRY_COUNT = ?, COMPLETED_AT = CURRENT_TIMESTAMP
                WHERE ID = ?
                """,
                (JobStatus.failed.value, error[:1000], attempts, str(job_id)),
            )

            # Write to DLQ
            execute_dml(
                conn,
                """
                INSERT INTO CONVERSION_DLQ
                  (ID, JOB_ID, INPUT_TYPE, ARTIFACT_NAME,
                   ERROR_MESSAGE, ATTEMPTS, CREATED_AT)
                VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (str(uuid.uuid4()), str(job_id), input_type,
                 artifact_name, error[:1000], attempts),
            )

        logger.info("Job written to DLQ", extra={"job_id": str(job_id)})

        # Send alert
        await _send_alert(job_id, artifact_name, error, attempts)

    except Exception as dlq_error:
        logger.error("Failed to write DLQ entry", extra={"error": str(dlq_error)})


async def _send_alert(
    job_id: uuid.UUID,
    artifact_name: str,
    error: str,
    attempts: int,
):
    """Send email/Slack alert for permanently failed jobs."""
    import os
    import httpx

    webhook_url = os.getenv("SLACK_WEBHOOK_URL", "")
    if not webhook_url:
        return

    message = {
        "text": f"*BOBJ Converter — Job Failed Permanently*",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*Job failed after {attempts} attempts*\n"
                        f"*Job ID:* `{job_id}`\n"
                        f"*Artifact:* `{artifact_name}`\n"
                        f"*Error:* `{error[:200]}`\n"
                        f"*Action:* Check DLQ table `CONVERSION_DLQ` in HANA"
                    ),
                },
            }
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook_url, json=message)
    except Exception as e:
        logger.warning("Failed to send Slack alert", extra={"error": str(e)})
