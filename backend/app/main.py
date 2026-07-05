import os
import json
import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from app.db.hana import init_db, close_db
from app.routers import conversion, projects, jobs, health
from app.routers import admin

logging.basicConfig(level=logging.INFO)
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
    logger.info("Initializing HANA Cloud connection pool...")
    await init_db(vcap)
    logger.info("Application startup complete.")
    yield
    logger.info("Shutting down — closing DB pool...")
    await close_db()


app = FastAPI(
    title="BOBJ → Datasphere & SAC Converter API",
    version="1.0.0",
    description="SAP BTP-hosted API for converting BOBJ artifacts to Datasphere entities and SAC models.",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

ALLOWED_ORIGINS = [
    "https://crave-bobj-sac-convertor.cfapps.eu10-004.hana.ondemand.com",
    "https://bobj-converter-ui.cfapps.eu10-004.hana.ondemand.com",
    "https://crave-bw-bobj-datasphere-convertor.cfapps.eu10-004.hana.ondemand.com",
    "http://localhost:3000",
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "x-csrf-token", "x-correlation-id"],
)

# No auth required for now — add verify_token back when XSUAA is configured
app.include_router(health.router,     prefix="/api/health",         tags=["health"])
app.include_router(conversion.router, prefix="/api/v1/conversions", tags=["conversion"])
app.include_router(projects.router,   prefix="/api/v1/projects",    tags=["projects"])
app.include_router(jobs.router,       prefix="/api/v1/jobs",        tags=["jobs"])
app.include_router(admin.router,      prefix="/api/v1/admin",       tags=["admin"])
