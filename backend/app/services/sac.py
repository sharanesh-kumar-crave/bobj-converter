import os
import logging
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

# SAC REST API:
# https://api.sap.com/package/SAPAnalyticsCloudForPlanningAndAnalysis/rest


def _get_sac_config() -> dict:
    return {
        "tenant_url":    os.getenv("SAC_TENANT_URL", ""),     # e.g. https://<tenant>.eu20.sapanalytics.cloud
        "token_url":     os.getenv("SAC_TOKEN_URL", ""),
        "client_id":     os.getenv("SAC_CLIENT_ID", ""),
        "client_secret": os.getenv("SAC_CLIENT_SECRET", ""),
    }


_sac_token: Optional[str] = None


async def _get_sac_token(config: dict) -> str:
    global _sac_token
    if _sac_token:
        return _sac_token
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
        _sac_token = resp.json()["access_token"]
        return _sac_token


async def push_model(model_config: dict) -> dict:
    """
    Create an SAC model from the generated configuration.
    Returns result summary.
    """
    config = _get_sac_config()
    if not config["tenant_url"]:
        logger.warning("SAC config missing — skipping push")
        return {"skipped": True, "reason": "SAC not configured"}

    token = await _get_sac_token(config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type":  "application/json",
    }
    base = config["tenant_url"].rstrip("/")
    payload = _to_sac_payload(model_config)

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(
                f"{base}/api/v1/models",
                headers=headers,
                json=payload,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                return {
                    "model_id": data.get("modelId") or data.get("id"),
                    "model_name": model_config.get("modelName"),
                    "status": "created",
                }
            else:
                return {
                    "status": "failed",
                    "error": resp.text[:300],
                    "model_name": model_config.get("modelName"),
                }
        except httpx.RequestError as e:
            return {"status": "failed", "error": str(e)}


def _to_sac_payload(model: dict) -> dict:
    """Map our internal SAC schema to the SAC API payload format."""
    return {
        "modelName":   model.get("modelName"),
        "modelType":   model.get("modelType", "Analytical"),
        "description": model.get("description", ""),
        "dimensions": [
            {
                "id":          dim.get("id"),
                "name":        dim.get("name"),
                "dimensionType": dim.get("type", "Generic"),
            }
            for dim in model.get("dimensions", [])
        ],
        "measures": [
            {
                "id":          m.get("id"),
                "name":        m.get("name"),
                "aggregationType": m.get("aggregation", "SUM"),
            }
            for m in model.get("measures", [])
        ],
        "dataConnections": model.get("dataConnections", []),
    }
