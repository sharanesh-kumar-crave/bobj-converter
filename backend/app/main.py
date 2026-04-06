import os
import json
import logging
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.db.hana import init_db, close_db
from app.auth.xsuaa import verify_token
from app.monitoring.logging import setup_logging, RequestLoggingMiddleware
from app.routers import conversion, projects, jobs, health
from app.routers import admin

setup_logging()
logger = logging.getLogger(__name__)


def load_vcap_services() -> dict:
    vcap = os.getenv("VCAP_SERVICES", "{}")
    try:
        return json.loads(vcap)
    except json.JSONDecodeError:
        logger.warning("Could not parse VCAP_SERVICES")
        return {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    vcap = load_vcap_services()
    app.state.vcap = vcap
    logger.info("Initializing HANA Cloud connection pool")
    await init_db(vcap)
    logger.info("Application startup complete", extra={
        "environment": os.getenv("ENVIRONMENT", "local"),
        "build_sha":   os.getenv("BUILD_SHA", "unknown"),
    })
    yield
    logger.info("Shutting down")
    await close_db()


app = FastAPI(
    title="BOBJ to Datasphere and SAC Converter API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS",
    "https://*.hana.ondemand.com,https://*.cfapps.eu10.hana.ondemand.com"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "x-csrf-token", "x-correlation-id"],
)

app.add_middleware(RequestLoggingMiddleware)

app.include_router(health.router,   prefix="/api/health",        tags=["health"])
app.include_router(conversion.router, prefix="/api/v1/conversions", tags=["conversion"],
    dependencies=[Depends(verify_token)])
app.include_router(projects.router, prefix="/api/v1/projects",   tags=["projects"],
    dependencies=[Depends(verify_token)])
app.include_router(jobs.router,     prefix="/api/v1/jobs",       tags=["jobs"],
    dependencies=[Depends(verify_token)])
app.include_router(admin.router,    prefix="/api/v1/admin",      tags=["admin"],
    dependencies=[Depends(verify_token)])
