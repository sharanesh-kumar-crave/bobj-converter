import asyncio
import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

try:
    from hdbcli import dbapi
except ImportError:
    dbapi = None

logger = logging.getLogger(__name__)

_pool: list | None = None
_pool_lock = asyncio.Lock()
_hana_config: dict = {}

POOL_SIZE = int(os.getenv("HANA_POOL_SIZE", "5"))


def _parse_hana_credentials(vcap: dict) -> dict:
    """Build HANA Cloud connection params from VCAP_SERVICES, with env-var overrides.

    The 'hana-cloud' (hana-td) binding provides host/port but no SQL user/password,
    so credentials fall back to the HANA_USER / HANA_PASSWORD env vars.
    """
    for svc_name in ("hana", "hanatrial", "hana-cloud"):
        entries = vcap.get(svc_name, [])
        if not entries:
            continue
        creds = entries[0].get("credentials", {})
        cfg = {
            "address": creds.get("host") or os.getenv("HANA_HOST", "localhost"),
            "port": int(creds.get("port") or os.getenv("HANA_PORT", "443")),
            "user": creds.get("user") or os.getenv("HANA_USER", ""),
            "password": creds.get("password") or os.getenv("HANA_PASSWORD", ""),
            "encrypt": True,
            "sslValidateCertificate": os.getenv("HANA_SSL_VALIDATE", "true").lower() == "true",
        }
        # The 'hana / schema' (and hdi-shared) binding provides a dedicated schema
        # and a TLS certificate — pass both through so tables land in the right schema.
        if creds.get("schema"):
            cfg["currentSchema"] = creds["schema"]
        if creds.get("certificate"):
            cfg["sslTrustStore"] = creds["certificate"]
        return cfg
    # Fallback to env vars (local development / no binding)
    return {
        "address": os.getenv("HANA_HOST", "localhost"),
        "port": int(os.getenv("HANA_PORT", "39017")),
        "user": os.getenv("HANA_USER", "SYSTEM"),
        "password": os.getenv("HANA_PASSWORD", ""),
        "encrypt": os.getenv("HANA_ENCRYPT", "false").lower() == "true",
    }


# Minimal DDL for the tables the app uses — embedded so bootstrap works without
# the repo's db/schema.sql being present in the deployment.
_SCHEMA_DDL = {
    "BOBJ_PROJECTS": """
        CREATE TABLE BOBJ_PROJECTS (
            ID                  NVARCHAR(36)   NOT NULL PRIMARY KEY,
            NAME                NVARCHAR(255)  NOT NULL,
            DESCRIPTION         NCLOB,
            BOBJ_SYSTEM_NAME    NVARCHAR(255),
            DATASPHERE_SPACE_ID NVARCHAR(255),
            SAC_TENANT_URL      NVARCHAR(512),
            OWNER_USER_ID       NVARCHAR(255)  NOT NULL,
            CREATED_AT          TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UPDATED_AT          TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """,
    "BOBJ_CONVERSION_JOBS": """
        CREATE TABLE BOBJ_CONVERSION_JOBS (
            ID              NVARCHAR(36)   NOT NULL PRIMARY KEY,
            PROJECT_ID      NVARCHAR(36),
            ARTIFACT_NAME   NVARCHAR(255)  NOT NULL,
            INPUT_TYPE      NVARCHAR(50)   NOT NULL,
            RAW_CONTENT     NCLOB          NOT NULL,
            STATUS          NVARCHAR(20)   NOT NULL DEFAULT 'pending',
            RESULT_JSON     NCLOB,
            TOTAL_OBJECTS   INTEGER,
            CONVERTED_COUNT INTEGER,
            ERROR_MESSAGE   NVARCHAR(2000),
            OWNER_USER_ID   NVARCHAR(255)  NOT NULL,
            CREATED_AT      TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
            COMPLETED_AT    TIMESTAMP
        )
    """,
}


def _bootstrap_schema(conn) -> None:
    """Create the app's tables if they don't already exist (idempotent)."""
    cursor = conn.cursor()
    for table, ddl in _SCHEMA_DDL.items():
        cursor.execute(
            "SELECT COUNT(*) FROM SYS.TABLES "
            "WHERE SCHEMA_NAME = CURRENT_SCHEMA AND TABLE_NAME = ?",
            (table,),
        )
        if not cursor.fetchone()[0]:
            logger.info("Creating missing table %s", table)
            cursor.execute(ddl)
    conn.commit()


async def init_db(vcap: dict):
    global _pool, _hana_config
    _hana_config = _parse_hana_credentials(vcap)
    logger.info("Connecting to HANA Cloud at %s:%s", _hana_config["address"], _hana_config["port"])
    _pool = []
    if os.getenv("ENVIRONMENT", "local") == "local":
        return
    if dbapi is None:
        raise RuntimeError("hdbcli driver unavailable but ENVIRONMENT is not 'local'")
    for _ in range(POOL_SIZE):
        conn = dbapi.connect(**_hana_config)
        conn.setautocommit(False)
        _pool.append(conn)
    _bootstrap_schema(_pool[0])
    logger.info("HANA Cloud pool ready (%d connections)", POOL_SIZE)


async def close_db():
    global _pool
    if _pool:
        for conn in _pool:
            try:
                conn.close()
            except Exception:
                pass
        _pool = []


@asynccontextmanager
async def get_db() -> AsyncGenerator[any, None]:
    """Acquire a HANA connection — returns mock in local env."""
    if os.getenv("ENVIRONMENT", "local") == "local":
        from unittest.mock import MagicMock
        mock = MagicMock()
        mock.cursor.return_value.description = [("result",)]
        mock.cursor.return_value.fetchall.return_value = []
        mock.cursor.return_value.rowcount = 1
        yield mock
        return
    global _pool
    async with _pool_lock:
        conn = _pool.pop(0)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        async with _pool_lock:
            _pool.append(conn)

def execute_query(conn: any, sql: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT and return rows as dicts."""
    cursor = conn.cursor()
    cursor.execute(sql, params)
    columns = [desc[0].lower() for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def execute_dml(conn: any, sql: str, params: tuple = ()) -> int:
    """Execute INSERT/UPDATE/DELETE and return affected row count."""
    cursor = conn.cursor()
    cursor.execute(sql, params)
    return cursor.rowcount
