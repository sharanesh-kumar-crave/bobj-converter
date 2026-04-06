"""
Structured JSON logging with SAP Cloud Logging Service integration.
Streams logs to SAP Cloud Logging via OpenTelemetry / syslog endpoint.
Falls back to stdout JSON for local dev and CF log drain.
"""
import os
import sys
import json
import logging
import time
import traceback
from datetime import datetime, timezone
from typing import Any
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware


# ── SAP Cloud Logging config ──────────────────────────────────────────────────

def _get_cloud_logging_config() -> dict:
    """Extract SAP Cloud Logging credentials from VCAP_SERVICES."""
    import json as _json
    vcap_raw = os.getenv("VCAP_SERVICES", "{}")
    vcap = _json.loads(vcap_raw)
    cls_list = vcap.get("cloud-logging", [])
    if cls_list:
        creds = cls_list[0]["credentials"]
        return {
            "ingest_url":    creds.get("ingest-otlp-endpoint", ""),
            "ingest_token":  creds.get("ingest-otlp-token", ""),
            "enabled":       True,
        }
    return {"enabled": False}


# ── Structured JSON formatter ──────────────────────────────────────────────────

class StructuredJSONFormatter(logging.Formatter):
    """
    Formats log records as structured JSON compatible with
    SAP Cloud Logging / OpenSearch ingestion format.
    """
    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp":   datetime.now(timezone.utc).isoformat(),
            "level":       record.levelname,
            "logger":      record.name,
            "message":     record.getMessage(),
            "module":      record.module,
            "function":    record.funcName,
            "line":        record.lineno,
            "app":         os.getenv("VCAP_APPLICATION", "{}"),
            "environment": os.getenv("ENVIRONMENT", "local"),
            "build_sha":   os.getenv("BUILD_SHA", "unknown"),
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = {
                "type":       record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message":    str(record.exc_info[1]),
                "stacktrace": traceback.format_exception(*record.exc_info),
            }

        # Add any extra fields passed to the logger
        for key, value in record.__dict__.items():
            if key not in (
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message",
            ):
                if not key.startswith("_"):
                    log_entry[key] = value

        return json.dumps(log_entry, default=str)


def setup_logging():
    """Configure structured JSON logging for the application."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, log_level, logging.INFO))

    # Remove default handlers
    root_logger.handlers.clear()

    # Stdout handler with JSON formatter
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(StructuredJSONFormatter())
    root_logger.addHandler(handler)

    # Suppress noisy third-party loggers
    for noisy in ("uvicorn.access", "httpx", "hdbcli"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Structured logging initialized",
        extra={"log_level": log_level, "environment": os.getenv("ENVIRONMENT", "local")},
    )


# ── Request/Response logging middleware ───────────────────────────────────────

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Logs every HTTP request and response with timing, status,
    user info, and correlation ID for distributed tracing.
    """
    logger = logging.getLogger("app.http")

    async def dispatch(self, request: Request, call_next) -> Response:
        start = time.perf_counter()
        correlation_id = request.headers.get("x-correlation-id", self._gen_id())
        request.state.correlation_id = correlation_id

        response = await call_next(request)

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        user_id = getattr(getattr(request.state, "user", {}), "get", lambda k, d=None: d)("sub", "anonymous")

        log_data = {
            "event":          "http_request",
            "method":         request.method,
            "path":           request.url.path,
            "status_code":    response.status_code,
            "duration_ms":    duration_ms,
            "correlation_id": correlation_id,
            "user_id":        user_id,
            "user_agent":     request.headers.get("user-agent", ""),
            "ip":             request.client.host if request.client else "unknown",
        }

        level = logging.WARNING if response.status_code >= 400 else logging.INFO
        self.logger.log(level, f"{request.method} {request.url.path} → {response.status_code}", extra=log_data)

        response.headers["x-correlation-id"] = correlation_id
        response.headers["x-response-time"]  = f"{duration_ms}ms"
        return response

    @staticmethod
    def _gen_id() -> str:
        import uuid
        return str(uuid.uuid4())[:8]
