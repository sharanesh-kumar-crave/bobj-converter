import json
import logging
import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.xsuaa import verify_token
from app.db.hana import close_db, init_db
from app.routers import conversion, health, jobs, projects

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def load_vcap_services() -> dict:
    """Parse CF VCAP_SERVICES env var to extract bound service credentials."""
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

# CORS — restrict to your BTP subaccount domain in production
ALLOWED_ORIGINS = os.getenv(
    "ALLOWED_ORIGINS", "https://*.hana.ondemand.com,https://*.cfapps.eu20.hana.ondemand.com"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "x-csrf-token"],
)

# Routers
app.include_router(health.router, prefix="/api/health", tags=["health"])
app.include_router(
    conversion.router,
    prefix="/api/v1/conversions",
    tags=["conversion"],
    dependencies=[Depends(verify_token)],
)
app.include_router(
    projects.router,
    prefix="/api/v1/projects",
    tags=["projects"],
    dependencies=[Depends(verify_token)],
)
app.include_router(
    jobs.router,
    prefix="/api/v1/jobs",
    tags=["jobs"],
    dependencies=[Depends(verify_token)],
)
