import os
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

# Datasphere REST API docs:
# https://api.sap.com/package/SAPDatasphere/rest


def _get_datasphere_config() -> dict:
    return {
        "base_url":   os.getenv("DATASPHERE_BASE_URL", ""),   # e.g. https://<tenant>.eu20.hana.ondemand.com
        "space_id":   os.getenv("DATASPHERE_SPACE_ID", ""),
        "token_url":  os.getenv("DATASPHERE_TOKEN_URL", ""),
        "client_id":  os.getenv("DATASPHERE_CLIENT_ID", ""),
        "client_secret": os.getenv("DATASPHERE_CLIENT_SECRET", ""),
    }


_ds_token: Optional[str] = None


async def _get_ds_token(config: dict) -> str:
    global _ds_token
    if _ds_token:
        return _ds_token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            config["token_url"],
            data={
                "grant_type":    "client_credentials",
                "client_id":     config["client_id"],
                "client_secret": config["client_secret"],
            },
        )
        resp.raise_for_status()
        _ds_token = resp.json()["access_token"]
        return _ds_token


async def push_entities(entities: list[dict]) -> dict:
    """
    Push Datasphere entity definitions via the Datasphere REST API.
    Returns a summary of what was created / failed.
    """
    config = _get_datasphere_config()
    if not config["base_url"] or not config["space_id"]:
        logger.warning("Datasphere config missing — skipping push")
        return {"skipped": True, "reason": "Datasphere not configured"}

    token = await _get_ds_token(config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    base = config["base_url"].rstrip("/")
    space = config["space_id"]

    results = {"created": [], "failed": []}
    async with httpx.AsyncClient(timeout=30) as client:
        for entity in entities:
            entity_name = entity.get("entityName", "unknown")
            # Convert to Datasphere entity payload format
            ds_payload = _to_datasphere_payload(entity, space)
            try:
                resp = await client.post(
                    f"{base}/api/v1/spaces/{space}/entities",
                    headers=headers,
                    json=ds_payload,
                )
                if resp.status_code in (200, 201):
                    results["created"].append(entity_name)
                else:
                    results["failed"].append({
                        "entity": entity_name,
                        "error": resp.text[:200],
                    })
            except httpx.RequestError as e:
                results["failed"].append({"entity": entity_name, "error": str(e)})

    return results


def _to_datasphere_payload(entity: dict, space_id: str) -> dict:
    """Map our internal entity schema to Datasphere API payload."""
    return {
        "entityName":  entity.get("entityName"),
        "entityType":  entity.get("entityType", "View"),
        "space":       space_id,
        "description": entity.get("description", ""),
        "columns": [
            {
                "name":     col.get("name"),
                "dataType": col.get("dataType", "String"),
                "isKey":    col.get("keyColumn", False),
            }
            for col in entity.get("columns", [])
        ],
        "sqlExpression": entity.get("sqlExpression"),
    }
