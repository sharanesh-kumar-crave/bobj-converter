import os
import json
import logging
from typing import Optional, AsyncGenerator
import asyncio
from contextlib import asynccontextmanager
from hdbcli import dbapi

logger = logging.getLogger(__name__)

_pool: Optional[list] = None
_pool_lock = asyncio.Lock()
_hana_config: dict = {}

POOL_SIZE = int(os.getenv("HANA_POOL_SIZE", "5"))


def _parse_hana_credentials(vcap: dict) -> dict:
    """Extract HANA Cloud credentials from VCAP_SERVICES."""
    # Try 'hana' service (HANA Cloud via service binding)
    for svc_name in ("hana", "hanatrial"):
        entries = vcap.get(svc_name, [])
        if entries:
            creds = entries[0]["credentials"]
            return {
                "host": creds.get("host") or creds.get("url"),
                "port": int(creds.get("port", 443)),
                "user": creds.get("user"),
                "password": creds.get("password"),
                "encrypt": True,
                "sslValidateCertificate": True,
            }
    # Fallback to env vars (local development)
    return {
        "host": os.getenv("HANA_HOST", "localhost"),
        "port": int(os.getenv("HANA_PORT", "39017")),
        "user": os.getenv("HANA_USER", "SYSTEM"),
        "password": os.getenv("HANA_PASSWORD", ""),
        "encrypt": os.getenv("HANA_ENCRYPT", "false").lower() == "true",
    }


async def init_db(vcap: dict):
    global _pool, _hana_config
    _hana_config = _parse_hana_credentials(vcap)
    logger.info("Connecting to HANA Cloud at %s:%s", _hana_config["host"], _hana_config["port"])
    _pool = []
    for _ in range(POOL_SIZE):
        conn = dbapi.connect(**_hana_config)
        conn.setautocommit(False)
        _pool.append(conn)
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
async def get_db() -> AsyncGenerator[dbapi.Connection, None]:
    """Acquire a HANA connection from the pool (simple round-robin)."""
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


def execute_query(conn: dbapi.Connection, sql: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT and return rows as dicts."""
    cursor = conn.cursor()
    cursor.execute(sql, params)
    columns = [desc[0].lower() for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def execute_dml(conn: dbapi.Connection, sql: str, params: tuple = ()) -> int:
    """Execute INSERT/UPDATE/DELETE and return affected row count."""
    cursor = conn.cursor()
    cursor.execute(sql, params)
    return cursor.rowcount
