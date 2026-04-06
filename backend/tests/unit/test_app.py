"""
Unit tests — health endpoint, Pydantic schemas, conversion submission.
All external deps (HANA, AI Core, Datasphere, SAC) are mocked via conftest.
"""
import uuid
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from contextlib import asynccontextmanager


# ── Health endpoint ────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client, mock_db):
        mock_query, _ = mock_db
        mock_query.return_value = [{"1": 1}]

        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.health.get_db", fake_get_db), \
             patch("app.routers.health.execute_query", return_value=[{"1": 1}]):
            resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_health_returns_db_status(self, client):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.health.get_db", fake_get_db), \
             patch("app.routers.health.execute_query", return_value=[]):
            resp = client.get("/api/health")
        assert "db" in resp.json()


# ── Pydantic schema validation ─────────────────────────────────────────────────

class TestSchemas:
    def test_conversion_request_valid(self):
        from app.models.schemas import ConversionRequest, InputType
        req = ConversionRequest(
            input_type=InputType.universe_xml,
            artifact_name="Test_Universe",
            raw_content="<Universe name='Test'><DataFoundation/></Universe>",
        )
        assert req.artifact_name == "Test_Universe"
        assert req.input_type == InputType.universe_xml

    def test_conversion_request_requires_content(self):
        from app.models.schemas import ConversionRequest, InputType
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ConversionRequest(
                input_type=InputType.universe_xml,
                artifact_name="Test",
                raw_content="x",   # min_length=10 should fail
            )

    def test_conversion_request_requires_name(self):
        from app.models.schemas import ConversionRequest, InputType
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ConversionRequest(
                input_type=InputType.universe_xml,
                artifact_name="",
                raw_content="<Universe name='Test'><DataFoundation/></Universe>",
            )

    def test_datasphere_entity_defaults(self):
        from app.models.schemas import DatasphereEntity
        e = DatasphereEntity(entity_name="V_TEST", entity_type="View")
        assert e.columns == []
        assert e.joins == []

    def test_job_status_enum(self):
        from app.models.schemas import JobStatus
        assert JobStatus.pending.value == "pending"
        assert JobStatus.completed.value == "completed"

    def test_conversion_summary(self):
        from app.models.schemas import ConversionSummary
        s = ConversionSummary(
            total_objects=10, converted=8, manual_review=2, not_supported=0,
            recommendations=["Test recommendation"],
        )
        assert s.total_objects == 10
        assert len(s.recommendations) == 1


# ── Conversion router — submission ────────────────────────────────────────────

class TestConversionSubmit:
    def test_submit_returns_202(self, client, mock_ai_core, mock_datasphere, mock_sac):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.conversion.get_db", fake_get_db), \
             patch("app.routers.conversion.execute_dml", return_value=1):
            resp = client.post("/api/v1/conversions", json={
                "input_type":    "universe_xml",
                "artifact_name": "My_Universe",
                "raw_content":   "<Universe name='Test'><DataFoundation/></Universe>",
            })
        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        assert body["status"] == "pending"

    def test_submit_requires_auth(self, app):
        from fastapi.testclient import TestClient
        with TestClient(app) as c:
            # No auth header
            resp = c.post("/api/v1/conversions", json={
                "input_type": "universe_xml",
                "artifact_name": "Test",
                "raw_content": "<Universe/>" * 5,
            })
        assert resp.status_code in (401, 403)

    def test_get_job_not_found(self, client):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.conversion.get_db", fake_get_db), \
             patch("app.routers.conversion.execute_query", return_value=[]):
            resp = client.get(f"/api/v1/conversions/{uuid.uuid4()}")
        assert resp.status_code == 404


# ── Projects router ───────────────────────────────────────────────────────────

class TestProjects:
    def test_list_projects_empty(self, client):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.projects.get_db", fake_get_db), \
             patch("app.routers.projects.execute_query", return_value=[]):
            resp = client.get("/api/v1/projects")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_project(self, client):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.projects.get_db", fake_get_db), \
             patch("app.routers.projects.execute_dml", return_value=1):
            resp = client.post("/api/v1/projects", json={
                "name": "Q3 Migration",
                "bobj_system_name": "BOBJ-PROD",
            })
        assert resp.status_code == 201
        body = resp.json()
        assert body["name"] == "Q3 Migration"
        assert "id" in body

    def test_create_project_requires_name(self, client):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.projects.get_db", fake_get_db):
            resp = client.post("/api/v1/projects", json={"name": ""})
        assert resp.status_code == 422


# ── AI Core service unit tests ────────────────────────────────────────────────

class TestAICore:
    def test_system_prompt_contains_required_keys(self):
        from app.services.ai_core import SYSTEM_PROMPT
        for key in ["datasphereEntities", "sacModelConfig", "conversionMapping", "summary"]:
            assert key in SYSTEM_PROMPT, f"SYSTEM_PROMPT missing key: {key}"

    def test_system_prompt_demands_json_only(self):
        from app.services.ai_core import SYSTEM_PROMPT
        assert "ONLY valid JSON" in SYSTEM_PROMPT or "ONLY the JSON" in SYSTEM_PROMPT

    @pytest.mark.asyncio
    async def test_run_conversion_parses_response(self):
        import json
        from app.services.ai_core import run_conversion
        mock_result = json.dumps({
            "datasphereEntities": [],
            "sacModelConfig": {"modelName": "Test", "modelType": "Analytical",
                               "dimensions": [], "measures": [], "dataConnections": []},
            "conversionMapping": [],
            "summary": {"totalObjects": 0, "converted": 0, "manualReview": 0,
                        "notSupported": 0, "recommendations": []},
        })
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "content": [{"text": mock_result}]
            }
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_client_class.return_value = mock_client

            result = await run_conversion("universe_xml", "<Universe/>")
        assert "datasphereEntities" in result
        assert "sacModelConfig" in result
