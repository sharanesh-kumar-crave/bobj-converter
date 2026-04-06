"""
Metrics collection for the BOBJ Converter.
Tracks: job counts, durations, error rates, AI Core latency, push success rates.
Stores snapshots in HANA for historical trending.
"""
import time
import logging
import asyncio
from collections import defaultdict, deque
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    In-memory metrics store with periodic flush to HANA.
    Thread-safe via asyncio locks.
    """
    def __init__(self):
        self._lock = asyncio.Lock()

        # Counters
        self.jobs_submitted  = 0
        self.jobs_completed  = 0
        self.jobs_failed     = 0
        self.jobs_retried    = 0
        self.ds_pushes_ok    = 0
        self.ds_pushes_fail  = 0
        self.sac_pushes_ok   = 0
        self.sac_pushes_fail = 0

        # Duration tracking (last 100 jobs)
        self._conversion_durations: deque = deque(maxlen=100)
        self._ai_core_durations:    deque = deque(maxlen=100)

        # Per-status counters
        self.status_counts: dict = defaultdict(int)

        # Input type breakdown
        self.input_type_counts: dict = defaultdict(int)

        # Error tracking (last 50 errors)
        self._recent_errors: deque = deque(maxlen=50)

        # Start time
        self._started_at = datetime.now(timezone.utc)

    # ── Recording methods ─────────────────────────────────────────────────────

    async def record_job_submitted(self, input_type: str):
        async with self._lock:
            self.jobs_submitted += 1
            self.input_type_counts[input_type] += 1
        logger.info("Job submitted", extra={"event": "job_submitted", "input_type": input_type})

    async def record_job_completed(self, job_id: str, duration_s: float,
                                    total_objects: int, converted: int):
        async with self._lock:
            self.jobs_completed += 1
            self._conversion_durations.append(duration_s)
            self.status_counts["completed"] += 1
        logger.info(
            "Job completed",
            extra={
                "event":         "job_completed",
                "job_id":        job_id,
                "duration_s":    round(duration_s, 2),
                "total_objects": total_objects,
                "converted":     converted,
                "conversion_rate": round(converted / total_objects * 100, 1) if total_objects else 0,
            },
        )

    async def record_job_failed(self, job_id: str, error: str, attempt: int = 1):
        async with self._lock:
            self.jobs_failed += 1
            self.status_counts["failed"] += 1
            self._recent_errors.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "job_id":    job_id,
                "error":     error[:300],
                "attempt":   attempt,
            })
        logger.error(
            "Job failed",
            extra={"event": "job_failed", "job_id": job_id, "error": error[:200], "attempt": attempt},
        )

    async def record_job_retried(self, job_id: str, attempt: int):
        async with self._lock:
            self.jobs_retried += 1
        logger.warning(
            "Job retried",
            extra={"event": "job_retried", "job_id": job_id, "attempt": attempt},
        )

    async def record_ai_core_latency(self, duration_s: float):
        async with self._lock:
            self._ai_core_durations.append(duration_s)

    async def record_ds_push(self, success: bool, entity_count: int = 0):
        async with self._lock:
            if success:
                self.ds_pushes_ok += 1
            else:
                self.ds_pushes_fail += 1
        logger.info(
            "Datasphere push",
            extra={"event": "ds_push", "success": success, "entity_count": entity_count},
        )

    async def record_sac_push(self, success: bool):
        async with self._lock:
            if success:
                self.sac_pushes_ok += 1
            else:
                self.sac_pushes_fail += 1
        logger.info(
            "SAC push",
            extra={"event": "sac_push", "success": success},
        )

    # ── Computed metrics ──────────────────────────────────────────────────────

    def _avg(self, values) -> float:
        lst = list(values)
        return round(sum(lst) / len(lst), 2) if lst else 0.0

    def _p95(self, values) -> float:
        lst = sorted(values)
        if not lst:
            return 0.0
        idx = int(len(lst) * 0.95)
        return round(lst[min(idx, len(lst) - 1)], 2)

    async def snapshot(self) -> dict:
        """Return current metrics as a dict for the dashboard API."""
        async with self._lock:
            total = self.jobs_submitted or 1
            return {
                "uptime_seconds":        round((datetime.now(timezone.utc) - self._started_at).total_seconds()),
                "jobs": {
                    "submitted":         self.jobs_submitted,
                    "completed":         self.jobs_completed,
                    "failed":            self.jobs_failed,
                    "retried":           self.jobs_retried,
                    "success_rate_pct":  round(self.jobs_completed / total * 100, 1),
                    "failure_rate_pct":  round(self.jobs_failed    / total * 100, 1),
                },
                "conversion": {
                    "avg_duration_s":    self._avg(self._conversion_durations),
                    "p95_duration_s":    self._p95(self._conversion_durations),
                    "input_type_breakdown": dict(self.input_type_counts),
                },
                "ai_core": {
                    "avg_latency_s":     self._avg(self._ai_core_durations),
                    "p95_latency_s":     self._p95(self._ai_core_durations),
                },
                "datasphere": {
                    "pushes_ok":         self.ds_pushes_ok,
                    "pushes_failed":     self.ds_pushes_fail,
                    "success_rate_pct":  round(
                        self.ds_pushes_ok / (self.ds_pushes_ok + self.ds_pushes_fail + 0.001) * 100, 1
                    ),
                },
                "sac": {
                    "pushes_ok":         self.sac_pushes_ok,
                    "pushes_failed":     self.sac_pushes_fail,
                },
                "recent_errors":         list(self._recent_errors)[-10:],
            }


# ── Global singleton ──────────────────────────────────────────────────────────
metrics = MetricsCollector()
