"""
Shared pytest fixtures for unit and integration tests.
"""
import json
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient


# ── Tell the app we are in test mode ──────────────────────────────────────────
import os
os.environ.setdefault("TESTING", "true")
os.environ.setdefault("HANA_HOST", "mock")
os.environ.setdefault("HANA_USER", "mock")
os.environ.setdefault("HANA_PASSWORD", "mock")
os.environ.setdefault("ANTHROPIC_API_KEY", "mock-key")


# ── Fake XSUAA token ──────────────────────────────────────────────────────────
import base64, time

def _make_jwt(sub="test-user-123", scopes="bobj-converter.convert bobj-converter.read bobj-converter.push"):
    header  = base64.urlsafe_b64encode(b'{"alg":"none","typ":"JWT"}').rstrip(b"=")
    payload = base64.urlsafe_b64encode(json.dumps({
        "sub":        sub,
        "email":      "tester@example.com",
        "given_name": "Test",
        "scope":      scopes,
        "exp":        int(time.time()) + 3600,
        "iss":        "https://test.authentication.eu20.hana.ondemand.com",
        "clientid":   "test-client-id",
    }).encode()).rstrip(b"=")
    sig = base64.urlsafe_b64encode(b"fakesig").rstrip(b"=")
    return f"{header.decode()}.{payload.decode()}.{sig.decode()}"

TEST_JWT = _make_jwt()
TEST_HEADERS = {"Authorization": f"Bearer {TEST_JWT}"}


# ── Mock DB connection ─────────────────────────────────────────────────────────
class MockCursor:
    def __init__(self, rows=None, description=None):
        self._rows = rows or []
        self.description = description or [("id",), ("name",)]
        self.rowcount = len(self._rows)

    def execute(self, sql, params=None): pass
    def fetchall(self): return self._rows
    def fetchone(self): return self._rows[0] if self._rows else None


class MockConn:
    def __init__(self, rows=None):
        self._rows = rows or []
    def cursor(self): return MockCursor(self._rows)
    def commit(self): pass
    def rollback(self): pass


# ── App fixture (with all external deps mocked) ───────────────────────────────
@pytest.fixture(scope="session")
def mock_db_conn():
    return MockConn()


@pytest.fixture(scope="session")
def app(mock_db_conn):
    """Create FastAPI test app with HANA and XSUAA mocked out."""
    # Patch DB init so it doesn't try to connect to real HANA
    with patch("app.db.hana.init_db", new_callable=AsyncMock), \
         patch("app.db.hana.close_db", new_callable=AsyncMock), \
         patch("app.db.hana._pool", [mock_db_conn]):

        from app.main import app as _app
        yield _app


@pytest.fixture
def client(app):
    """HTTP test client with XSUAA token verification bypassed."""
    from app.auth.xsuaa import verify_token

    async def _fake_verify(request, credentials=None):
        import base64, json as _json
        parts = TEST_JWT.split(".")
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = _json.loads(base64.urlsafe_b64decode(padded))
        request.state.user = payload
        return payload

    app.dependency_overrides[verify_token] = _fake_verify
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    return TEST_HEADERS


# ── Mock AI Core response ──────────────────────────────────────────────────────
MOCK_CONVERSION_RESULT = {
    "datasphereEntities": [
        {
            "entityName": "V_FACT_SALES",
            "entityType": "View",
            "description": "Sales fact view",
            "columns": [
                {"name": "SALE_ID",   "dataType": "Integer", "keyColumn": True},
                {"name": "REVENUE",   "dataType": "Decimal", "keyColumn": False},
                {"name": "DATE_KEY",  "dataType": "Integer", "keyColumn": False},
            ],
            "joins": [],
            "sqlExpression": "SELECT * FROM FACT_SALES",
        }
    ],
    "sacModelConfig": {
        "modelName":   "Sales_Analytical_Model",
        "modelType":   "Analytical",
        "description": "Sales analytics model",
        "dimensions": [
            {"id": "d_customer", "name": "Customer", "type": "Generic", "hierarchies": []},
            {"id": "d_time",     "name": "Time",     "type": "Date",    "hierarchies": ["Year-Quarter-Month"]},
        ],
        "measures": [
            {"id": "m_revenue", "name": "Total Revenue", "aggregation": "SUM", "format": "#,##0.00"},
            {"id": "m_qty",     "name": "Quantity",      "aggregation": "SUM"},
        ],
        "dataConnections": [{"name": "DS_SALES", "type": "Datasphere", "entityName": "V_FACT_SALES"}],
    },
    "conversionMapping": [
        {
            "sourceObject": "FACT_SALES",    "sourceType": "Table",
            "targetObject": "V_FACT_SALES",  "targetType": "View",
            "status": "Converted",           "notes": "Direct table-to-view mapping",
            "fieldMappings": [],
        },
        {
            "sourceObject": "Custom_Calc",   "sourceType": "Measure",
            "targetObject": None,            "targetType": None,
            "status": "Manual Review Required",
            "notes": "Complex calculated formula requires manual review",
            "fieldMappings": [],
        },
    ],
    "summary": {
        "totalObjects": 5,
        "converted":    4,
        "manualReview": 1,
        "notSupported": 0,
        "recommendations": [
            "Review calculated measure 'Custom_Calc' — it uses a non-standard BOBJ function.",
            "Enable HANA Live connection in Datasphere for optimal performance.",
        ],
    },
}


@pytest.fixture
def mock_ai_core():
    """Patch AI Core so tests don't call real LLM APIs."""
    with patch("app.services.ai_core.run_conversion", new_callable=AsyncMock) as m:
        m.return_value = MOCK_CONVERSION_RESULT
        yield m


@pytest.fixture
def mock_datasphere():
    with patch("app.services.datasphere.push_entities", new_callable=AsyncMock) as m:
        m.return_value = {"created": ["V_FACT_SALES"], "failed": []}
        yield m


@pytest.fixture
def mock_sac():
    with patch("app.services.sac.push_model", new_callable=AsyncMock) as m:
        m.return_value = {"status": "created", "model_name": "Sales_Analytical_Model", "model_id": "sac-123"}
        yield m


@pytest.fixture
def mock_db():
    """Patch HANA execute_query and execute_dml for unit tests."""
    with patch("app.db.hana.execute_query") as mock_query, \
         patch("app.db.hana.execute_dml")   as mock_dml:
        mock_query.return_value = []
        mock_dml.return_value   = 1
        yield mock_query, mock_dml
