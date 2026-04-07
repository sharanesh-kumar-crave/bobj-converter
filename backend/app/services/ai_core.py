import os
import json
import logging
import httpx
from typing import Any

logger = logging.getLogger(__name__)

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "https://CraveOpenAI-1.openai.azure.com/")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "GPT-4o-1")


async def convert_bobj_artifact(
    input_type: str,
    artifact_name: str,
    raw_content: str,
) -> dict[str, Any]:
    """Convert BOBJ artifact using Azure OpenAI GPT-4o."""

    prompt = f"""You are an SAP expert converting BOBJ artifacts to SAP Datasphere and SAC.

Input Type: {input_type}
Artifact Name: {artifact_name}
Content:
{raw_content}

Analyze this BOBJ artifact and return a JSON response with:
{{
  "datasphere_entities": [
    {{
      "name": "entity name",
      "type": "dimension|fact|analytic_model",
      "columns": [{{"name": "col", "type": "string|integer|decimal|date", "is_key": false}}],
      "description": "description"
    }}
  ],
  "sac_model_config": {{
    "model_name": "model name",
    "model_type": "planning|analytic",
    "description": "description",
    "dimensions": [{{"name": "dim", "type": "dimension"}}],
    "measures": [{{"name": "measure", "aggregation": "SUM|AVG|COUNT"}}],
    "data_connections": []
  }},
  "conversion_mapping": [
    {{"source": "source object", "target": "target object", "status": "converted|manual_review|not_supported", "notes": "notes"}}
  ],
  "summary": {{
    "total_objects": 0,
    "converted": 0,
    "manual_review": 0,
    "not_supported": 0,
    "recommendations": ["recommendation 1"]
  }}
}}

Return ONLY valid JSON, no markdown, no explanation."""

    url = (
        f"{AZURE_ENDPOINT.rstrip('/')}/openai/deployments/"
        f"{AZURE_CHAT_DEPLOYMENT}/chat/completions"
        f"?api-version={AZURE_API_VERSION}"
    )

    headers = {
        "Content-Type": "application/json",
        "api-key": AZURE_API_KEY,
    }

    body = {
        "messages": [
            {"role": "system", "content": "You are an SAP BOBJ to Datasphere/SAC migration expert. Always respond with valid JSON only."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 4000,
        "temperature": 0.1,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            content = data["choices"][0]["message"]["content"].strip()
            # Strip markdown if present
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content.strip())
    except Exception as e:
        logger.error(f"Azure OpenAI error: {e}")
        raise