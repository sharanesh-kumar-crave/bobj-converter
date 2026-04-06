import json
import logging
import os

import httpx

logger = logging.getLogger(__name__)

# SAP AI Core uses OAuth2 client-credentials to obtain a token,
# then calls the AI Core inference endpoint.

_access_token: str | None = None


def _get_aicore_config() -> dict:
    """Extract AI Core credentials from VCAP_SERVICES or environment."""
    import json as _json

    vcap_raw = os.getenv("VCAP_SERVICES", "{}")
    vcap = _json.loads(vcap_raw)
    aicore_list = vcap.get("aicore", [])
    if aicore_list:
        creds = aicore_list[0]["credentials"]
        return {
            "auth_url": creds["serviceurls"]["AI_API_URL"].rstrip("/") + "/oauth/token",
            "api_base": creds["serviceurls"]["AI_API_URL"].rstrip("/"),
            "client_id": creds["clientid"],
            "client_secret": creds["clientsecret"],
            "resource_group": os.getenv("AICORE_RESOURCE_GROUP", "default"),
            "deployment_id": os.getenv("AICORE_DEPLOYMENT_ID", ""),  # set after deploying model
        }
    # Fallback — allow direct Anthropic API for local dev
    return {
        "auth_url": None,
        "api_base": "https://api.anthropic.com",
        "client_id": None,
        "client_secret": None,
        "resource_group": None,
        "deployment_id": None,
        "anthropic_key": os.getenv("ANTHROPIC_API_KEY", ""),
    }


async def _get_token(config: dict) -> str:
    """Obtain OAuth2 bearer token from AI Core auth URL."""
    global _access_token
    if _access_token:
        return _access_token
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            config["auth_url"],
            data={
                "grant_type": "client_credentials",
                "client_id": config["client_id"],
                "client_secret": config["client_secret"],
            },
        )
        resp.raise_for_status()
        _access_token = resp.json()["access_token"]
        return _access_token


SYSTEM_PROMPT = """You are an expert SAP migration consultant specializing in converting SAP BusinessObjects (BOBJ) artifacts to SAP Datasphere entities and SAC model configurations.

Analyze the provided BOBJ artifact and return ONLY valid JSON with exactly these keys:

{
  "datasphereEntities": [
    {
      "entityName": "string",
      "entityType": "View|Entity|Dimension|Fact|Analytical Dataset",
      "description": "string",
      "columns": [{"name":"string","dataType":"string","description":"string","keyColumn":boolean}],
      "joins": [{"leftTable":"string","rightTable":"string","joinType":"INNER|LEFT|RIGHT","condition":"string"}],
      "sqlExpression": "string or null"
    }
  ],
  "sacModelConfig": {
    "modelName": "string",
    "modelType": "Analytical|Planning",
    "description": "string",
    "dimensions": [{"id":"string","name":"string","type":"Account|Date|Generic|Organization","hierarchies":["string"]}],
    "measures": [{"id":"string","name":"string","aggregation":"SUM|AVG|COUNT|MIN|MAX","format":"string","currency":"string or null"}],
    "dataConnections": [{"name":"string","type":"Datasphere|Live","entityName":"string"}]
  },
  "conversionMapping": [
    {
      "sourceObject": "string",
      "sourceType": "Table|Class|Measure|Dimension|Report|Filter",
      "targetObject": "string",
      "targetType": "Entity|View|Dimension|Measure|Model|Filter",
      "status": "Converted|Manual Review Required|Not Supported",
      "notes": "string",
      "fieldMappings": [{"sourceField":"string","targetField":"string","transformation":"string or null"}]
    }
  ],
  "summary": {
    "totalObjects": number,
    "converted": number,
    "manualReview": number,
    "notSupported": number,
    "recommendations": ["string"]
  }
}

Return ONLY the JSON object. No markdown fences, no explanation."""


async def run_conversion(input_type: str, raw_content: str) -> dict:
    """
    Call SAP AI Core (or Anthropic fallback) to convert BOBJ artifact.
    Returns parsed JSON dict with datasphereEntities, sacModelConfig, etc.
    """
    config = _get_aicore_config()

    user_message = (
        f"Convert this BOBJ {input_type.replace('_', ' ')} to Datasphere and SAC:\n\n{raw_content}"
    )

    # ── SAP AI Core path ──────────────────────────────────────────────────
    if config.get("deployment_id"):
        token = await _get_token(config)
        url = (
            f"{config['api_base']}/v2/inference/deployments/"
            f"{config['deployment_id']}/chat/completions"
        )
        headers = {
            "Authorization": f"Bearer {token}",
            "AI-Resource-Group": config["resource_group"],
            "Content-Type": "application/json",
        }
        payload = {
            "model": "gpt-4o",  # or your deployed model alias
            "max_tokens": 4000,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"]

    # ── Anthropic fallback (local dev / claude.ai hosted) ─────────────────
    else:
        api_key = config.get("anthropic_key", "")
        url = f"{config['api_base']}/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        payload = {
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 4000,
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": user_message}],
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            text = resp.json()["content"][0]["text"]

    # ── Parse JSON ────────────────────────────────────────────────────────
    clean = text.replace("```json", "").replace("```", "").strip()
    return json.loads(clean)
