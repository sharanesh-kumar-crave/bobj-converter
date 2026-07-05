import logging
import uuid
import json
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.models.schemas import ConversionRequest, JobStatus
from app.services.ai_core import run_conversion
from app.services.datasphere import push_entities
from app.services.sac import push_model

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory stores
_jobs: Dict[str, Dict[str, Any]] = {}
_projects: Dict[str, Dict[str, Any]] = {}
_bobj_connections: Dict[str, Dict[str, Any]] = {}


# ─── Project Models ───────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = ""
    bobj_system: Optional[str] = ""

class BOBJConnect(BaseModel):
    host: str
    port: int = 6400
    username: str
    password: str
    auth_type: str = "secEnterprise"
    system_name: Optional[str] = ""


# ─── Projects ─────────────────────────────────────────────────────
@router.get("/projects")
async def list_projects():
    return JSONResponse(content=list(_projects.values()))

@router.post("/projects")
async def create_project(payload: ProjectCreate):
    project_id = str(uuid.uuid4())
    project = {
        "project_id": project_id,
        "name": payload.name,
        "description": payload.description,
        "bobj_system": payload.bobj_system,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "conversion_count": 0,
        "last_activity": None,
    }
    _projects[project_id] = project
    logger.info("Project created: %s", project_id)
    return JSONResponse(content=project, status_code=201)

@router.get("/projects/{project_id}")
async def get_project(project_id: str):
    p = _projects.get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    # Attach jobs
    p["jobs"] = [j for j in _jobs.values() if j.get("project_id") == project_id]
    return JSONResponse(content=p)

@router.delete("/projects/{project_id}")
async def delete_project(project_id: str):
    if project_id not in _projects:
        raise HTTPException(status_code=404, detail="Project not found")
    del _projects[project_id]
    return JSONResponse(content={"deleted": True})


# ─── BOBJ Connection ──────────────────────────────────────────────
@router.post("/bobj/connect")
async def connect_bobj(payload: BOBJConnect):
    """Test BOBJ connection and simulate object discovery."""
    conn_id = str(uuid.uuid4())
    # In production this would use the BOBJ SDK/REST API
    # For now we simulate a successful connection
    if not payload.host or not payload.username:
        return JSONResponse(content={"status": "failed", "error": "Host and username required"}, status_code=400)

    connection = {
        "connection_id": conn_id,
        "host": payload.host,
        "port": payload.port,
        "username": payload.username,
        "system_name": payload.system_name or payload.host,
        "auth_type": payload.auth_type,
        "status": "connected",
        "connected_at": datetime.now(timezone.utc).isoformat(),
        "objects": _simulate_bobj_objects(),
    }
    _bobj_connections[conn_id] = connection
    return JSONResponse(content=connection)

@router.get("/bobj/connections")
async def list_connections():
    return JSONResponse(content=list(_bobj_connections.values()))

@router.get("/bobj/{connection_id}/objects")
async def get_bobj_objects(connection_id: str):
    conn = _bobj_connections.get(connection_id)
    if not conn:
        raise HTTPException(status_code=404, detail="Connection not found")
    return JSONResponse(content=conn["objects"])

def _simulate_bobj_objects():
    return [
        {"id": "u001", "name": "Sales Universe", "type": "Universe", "folder": "/Universes/Sales", "last_modified": "2024-01-15", "size_kb": 248},
        {"id": "u002", "name": "Finance Universe", "type": "Universe", "folder": "/Universes/Finance", "last_modified": "2024-02-20", "size_kb": 312},
        {"id": "u003", "name": "Procurement Universe", "type": "Universe", "folder": "/Universes/SCM", "last_modified": "2024-03-10", "size_kb": 189},
        {"id": "r001", "name": "Monthly Sales Report", "type": "WebI Report", "folder": "/Reports/Sales", "last_modified": "2024-03-01", "size_kb": 45},
        {"id": "r002", "name": "P&L Dashboard", "type": "WebI Report", "folder": "/Reports/Finance", "last_modified": "2024-02-28", "size_kb": 67},
        {"id": "r003", "name": "Vendor Analysis", "type": "WebI Report", "folder": "/Reports/SCM", "last_modified": "2024-03-05", "size_kb": 38},
        {"id": "r004", "name": "Headcount Report", "type": "Crystal Report", "folder": "/Reports/HR", "last_modified": "2024-01-20", "size_kb": 92},
        {"id": "u004", "name": "HR Universe", "type": "Universe", "folder": "/Universes/HR", "last_modified": "2023-12-10", "size_kb": 156},
        {"id": "r005", "name": "Customer 360", "type": "WebI Report", "folder": "/Reports/CRM", "last_modified": "2024-03-12", "size_kb": 83},
        {"id": "u005", "name": "CRM Universe", "type": "Universe", "folder": "/Universes/CRM", "last_modified": "2024-01-08", "size_kb": 201},
    ]


# ─── Conversions ──────────────────────────────────────────────────
@router.post("")
async def start_conversion(
    request: Request,
    background_tasks: BackgroundTasks,
    artifact_name: str = Form(...),
    input_type: str = Form(...),
    project_id: Optional[str] = Form(None),
    raw_content: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    content = raw_content or ""
    if file:
        file_bytes = await file.read()
        content = file_bytes.decode("utf-8", errors="replace")
        if not artifact_name or artifact_name == "undefined":
            artifact_name = file.filename or "uploaded_file"

    if not content.strip():
        raise HTTPException(status_code=422, detail="Content is required")

    job_id = str(uuid.uuid4())
    user_id = getattr(request.state, "user", {}).get("sub", "anonymous")

    _jobs[job_id] = {
        "job_id": job_id,
        "project_id": project_id,
        "artifact_name": artifact_name,
        "input_type": input_type,
        "status": JobStatus.pending.value,
        "owner_user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "result": None,
        "error": None,
    }

    if project_id and project_id in _projects:
        _projects[project_id]["conversion_count"] = _projects[project_id].get("conversion_count", 0) + 1
        _projects[project_id]["last_activity"] = datetime.now(timezone.utc).isoformat()

    background_tasks.add_task(_run_conversion_pipeline, job_id, input_type, content)
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": JobStatus.pending.value})


# Keep JSON body POST as well for backwards compat
@router.post("/json")
async def start_conversion_json(request: Request, payload: ConversionRequest, background_tasks: BackgroundTasks):
    job_id = str(uuid.uuid4())
    user_id = getattr(request.state, "user", {}).get("sub", "anonymous")
    _jobs[job_id] = {
        "job_id": job_id,
        "project_id": str(payload.project_id) if payload.project_id else None,
        "artifact_name": payload.artifact_name,
        "input_type": payload.input_type.value,
        "status": JobStatus.pending.value,
        "owner_user_id": user_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "result": None,
        "error": None,
    }
    if payload.project_id and str(payload.project_id) in _projects:
        _projects[str(payload.project_id)]["conversion_count"] = _projects[str(payload.project_id)].get("conversion_count", 0) + 1
        _projects[str(payload.project_id)]["last_activity"] = datetime.now(timezone.utc).isoformat()
    background_tasks.add_task(_run_conversion_pipeline, job_id, payload.input_type.value, payload.raw_content)
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": JobStatus.pending.value})


@router.get("/{job_id}")
async def get_conversion_result(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    response = {
        "job_id": job["job_id"],
        "project_id": job.get("project_id"),
        "artifact_name": job.get("artifact_name"),
        "input_type": job.get("input_type"),
        "status": job["status"],
        "created_at": job.get("created_at"),
        "completed_at": job.get("completed_at"),
        "error": job.get("error"),
    }
    if job.get("result"):
        r = job["result"]
        response["datasphere_entities"] = r.get("datasphereEntities", [])
        response["sac_model_config"] = r.get("sacModelConfig", {})
        response["conversion_mapping"] = r.get("conversionMapping", [])
        response["summary"] = r.get("summary", {})
    return JSONResponse(content=response)


@router.get("")
async def list_jobs(project_id: Optional[str] = None):
    jobs = list(_jobs.values())
    if project_id:
        jobs = [j for j in jobs if j.get("project_id") == project_id]
    return JSONResponse(content=jobs)


@router.post("/{job_id}/push-datasphere")
async def push_to_datasphere(job_id: str):
    job = _jobs.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job not found or not completed")
    result = await push_entities(job["result"].get("datasphereEntities", []))
    return JSONResponse(content=result)


@router.post("/{job_id}/push-sac")
async def push_to_sac(job_id: str):
    job = _jobs.get(job_id)
    if not job or not job.get("result"):
        raise HTTPException(status_code=404, detail="Job not found or not completed")
    result = await push_model(job["result"].get("sacModelConfig", {}))
    return JSONResponse(content=result)


async def _run_conversion_pipeline(job_id: str, input_type: str, content: str):
    _jobs[job_id]["status"] = JobStatus.running.value
    try:
        result = await run_conversion(input_type, content)
        ds_result = await push_entities(result.get("datasphereEntities", []))
        logger.info("Datasphere push: %s", ds_result)
        sac_result = await push_model(result.get("sacModelConfig", {}))
        logger.info("SAC push: %s", sac_result)
        _jobs[job_id].update({
            "status": JobStatus.completed.value,
            "result": result,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.exception("Conversion pipeline failed for job %s", job_id)
        _jobs[job_id].update({
            "status": JobStatus.failed.value,
            "error": str(e)[:1000],
            "completed_at": datetime.now(timezone.utc).isoformat(),
        })
