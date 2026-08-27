"""
Integration tests — full conversion pipeline flow.
Uses FastAPI TestClient + mocked HANA, AI Core, Datasphere, SAC.
"""
import json
import uuid
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from contextlib import asynccontextmanager
from tests.conftest import MOCK_CONVERSION_RESULT


@pytest.fixture
def db_mock_with_job():
    """Mock DB that returns a completed job row."""
    job_id = str(uuid.uuid4())
    job_row = {
        "id":             job_id,
        "project_id":     None,
        "artifact_name":  "Test_Universe",
        "input_type":     "universe_xml",
        "status":         "completed",
        "result_json":    json.dumps(MOCK_CONVERSION_RESULT),
        "total_objects":  MOCK_CONVERSION_RESULT["summary"]["totalObjects"],
        "converted_count": MOCK_CONVERSION_RESULT["summary"]["converted"],
        "error_message":  None,
        "owner_user_id":  "test-user-123",
        "created_at":     "2025-01-01T00:00:00",
        "completed_at":   "2025-01-01T00:01:00",
    }
    return job_id, job_row


class TestConversionPipeline:
    def test_full_conversion_flow(self, client, mock_ai_core, mock_datasphere, mock_sac):
        """Submit a conversion (JSON endpoint) and verify it is accepted."""
        with patch("app.routers.conversion._run_conversion_pipeline", new_callable=AsyncMock):
            resp = client.post("/api/v1/conversions/json", json={
                "input_type":    "universe_xml",
                "artifact_name": "Sales_Universe",
                "raw_content":   "<Universe name='Sales'><DataFoundation/></Universe>",
            })
        assert resp.status_code == 202
        assert resp.json()["job_id"] is not None

    def test_result_contains_all_outputs(self, client, db_mock_with_job):
        from app.routers.conversion import _jobs
        job_id, _ = db_mock_with_job
        _jobs[job_id] = {
            "job_id": job_id, "project_id": None, "artifact_name": "Test_Universe",
            "input_type": "universe_xml", "status": "completed",
            "created_at": "2025-01-01T00:00:00", "completed_at": "2025-01-01T00:01:00",
            "error": None, "result": MOCK_CONVERSION_RESULT,
        }
        resp = client.get(f"/api/v1/conversions/{job_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert len(body["datasphere_entities"]) == 1
        assert body["sac_model_config"]["modelName"] == "Sales_Analytical_Model"
        assert len(body["conversion_mapping"]) == 2
        assert body["summary"]["totalObjects"] == 5

    def test_mapping_statuses_present(self, client, db_mock_with_job):
        from app.routers.conversion import _jobs
        job_id, _ = db_mock_with_job
        _jobs[job_id] = {
            "job_id": job_id, "project_id": None, "artifact_name": "T",
            "input_type": "universe_xml", "status": "completed",
            "created_at": None, "completed_at": None, "error": None,
            "result": MOCK_CONVERSION_RESULT,
        }
        resp = client.get(f"/api/v1/conversions/{job_id}")
        mapping = resp.json()["conversion_mapping"]
        statuses = {m["status"] for m in mapping}
        assert "Converted" in statuses
        assert "Manual Review Required" in statuses

    @pytest.mark.asyncio
    async def test_datasphere_push_called(self):
        """The pipeline invokes AI conversion + Datasphere + SAC push."""
        from app.routers import conversion as conv
        job_id = str(uuid.uuid4())
        conv._jobs[job_id] = {
            "job_id": job_id, "project_id": None, "artifact_name": "Test",
            "input_type": "universe_xml", "status": "pending",
            "created_at": None, "completed_at": None, "error": None, "result": None,
        }
        with patch.object(conv, "run_conversion", new_callable=AsyncMock) as m_ai,              patch.object(conv, "push_entities", new_callable=AsyncMock) as m_ds,              patch.object(conv, "push_model", new_callable=AsyncMock) as m_sac:
            m_ai.return_value = MOCK_CONVERSION_RESULT
            m_ds.return_value = {"created": [], "failed": []}
            m_sac.return_value = {"status": "created"}
            await conv._run_conversion_pipeline(job_id, "universe_xml", "<Universe/>")
        m_ai.assert_called_once()
        m_ds.assert_called_once()
        m_sac.assert_called_once()


class TestProjectsIntegration:
    def test_create_and_list_project(self, client):
        """Create a project then verify it appears in list."""
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        created_id = str(uuid.uuid4())
        project_row = {
            "id":                 created_id,
            "name":               "Integration Test Project",
            "description":        None,
            "bobj_system_name":   "BOBJ-TEST",
            "datasphere_space_id": None,
            "sac_tenant_url":     None,
            "owner_user_id":      "test-user-123",
            "created_at":         "2025-01-01T00:00:00",
            "updated_at":         "2025-01-01T00:00:00",
            "job_count":          0,
        }

        with patch("app.routers.projects.get_db", fake_get_db), \
             patch("app.routers.projects.execute_dml", return_value=1):
            create_resp = client.post("/api/v1/projects", json={
                "name": "Integration Test Project",
                "bobj_system_name": "BOBJ-TEST",
            })
        assert create_resp.status_code == 201

        with patch("app.routers.projects.get_db", fake_get_db), \
             patch("app.routers.projects.execute_query", return_value=[project_row]):
            list_resp = client.get("/api/v1/projects")
        assert list_resp.status_code == 200
        assert any(p["name"] == "Integration Test Project" for p in list_resp.json())


class TestJobsIntegration:
    def test_list_jobs_empty(self, client):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        with patch("app.routers.jobs.get_db", fake_get_db), \
             patch("app.routers.jobs.execute_query", return_value=[]):
            resp = client.get("/api/v1/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_jobs_with_filter(self, client):
        @asynccontextmanager
        async def fake_get_db():
            yield MagicMock()

        rows = [{
            "id":             str(uuid.uuid4()),
            "project_id":     None,
            "artifact_name":  "Test_Universe",
            "input_type":     "universe_xml",
            "status":         "completed",
            "total_objects":  5,
            "converted_count": 4,
            "created_at":     "2025-01-01T00:00:00",
            "completed_at":   "2025-01-01T00:01:00",
        }]

        with patch("app.routers.jobs.get_db", fake_get_db), \
             patch("app.routers.jobs.execute_query", return_value=rows):
            resp = client.get("/api/v1/jobs?status=completed")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["status"] == "completed"
