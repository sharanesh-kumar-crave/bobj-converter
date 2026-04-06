from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum

from pydantic import UUID4, BaseModel, Field

# ─── Enums ────────────────────────────────────────────────────────────────────


class InputType(str, Enum):
    universe_xml = "universe_xml"
    report_rpt = "report_rpt"
    manual = "manual"


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class ConversionStatus(str, Enum):
    converted = "Converted"
    manual_review = "Manual Review Required"
    not_supported = "Not Supported"


# ─── BOBJ Input ───────────────────────────────────────────────────────────────


class ConversionRequest(BaseModel):
    project_id: UUID4 | None = None
    input_type: InputType
    artifact_name: str = Field(..., min_length=1, max_length=255)
    raw_content: str = Field(..., min_length=10, description="Raw BOBJ artifact text")


# ─── Datasphere Output ────────────────────────────────────────────────────────


class ColumnDef(BaseModel):
    name: str
    data_type: str
    description: str | None = None
    key_column: bool = False


class JoinDef(BaseModel):
    left_table: str
    right_table: str
    join_type: str = "INNER"
    condition: str


class DatasphereEntity(BaseModel):
    entity_name: str
    entity_type: str  # View | Entity | Dimension | Fact | Analytical Dataset
    description: str | None = None
    columns: list[ColumnDef] = []
    joins: list[JoinDef] = []
    sql_expression: str | None = None


# ─── SAC Output ───────────────────────────────────────────────────────────────


class SACDimension(BaseModel):
    id: str
    name: str
    type: str  # Account | Date | Generic | Organization
    hierarchies: list[str] = []


class SACMeasure(BaseModel):
    id: str
    name: str
    aggregation: str = "SUM"
    format: str | None = None
    currency: str | None = None


class SACDataConnection(BaseModel):
    name: str
    type: str  # Datasphere | Live
    entity_name: str


class SACModelConfig(BaseModel):
    model_name: str
    model_type: str  # Analytical | Planning
    description: str | None = None
    dimensions: list[SACDimension] = []
    measures: list[SACMeasure] = []
    data_connections: list[SACDataConnection] = []


# ─── Mapping Report ───────────────────────────────────────────────────────────


class FieldMapping(BaseModel):
    source_field: str
    target_field: str
    transformation: str | None = None


class MappingEntry(BaseModel):
    source_object: str
    source_type: str
    target_object: str
    target_type: str
    status: ConversionStatus
    notes: str | None = None
    field_mappings: list[FieldMapping] = []


# ─── Summary ──────────────────────────────────────────────────────────────────


class ConversionSummary(BaseModel):
    total_objects: int
    converted: int
    manual_review: int
    not_supported: int
    recommendations: list[str] = []


# ─── Full Conversion Result ───────────────────────────────────────────────────


class ConversionResult(BaseModel):
    job_id: UUID4
    project_id: UUID4 | None
    status: JobStatus
    datasphere_entities: list[DatasphereEntity] = []
    sac_model_config: SACModelConfig | None = None
    conversion_mapping: list[MappingEntry] = []
    summary: ConversionSummary | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    completed_at: datetime | None = None
    error: str | None = None


# ─── Project ──────────────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    bobj_system_name: str | None = None
    datasphere_space_id: str | None = None
    sac_tenant_url: str | None = None


class Project(ProjectCreate):
    id: UUID4 = Field(default_factory=uuid.uuid4)
    owner_user_id: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    job_count: int = 0

    class Config:
        from_attributes = True


# ─── Job (lightweight listing) ────────────────────────────────────────────────


class JobSummary(BaseModel):
    id: UUID4
    project_id: UUID4 | None
    artifact_name: str
    input_type: InputType
    status: JobStatus
    total_objects: int | None
    converted: int | None
    created_at: datetime
    completed_at: datetime | None
