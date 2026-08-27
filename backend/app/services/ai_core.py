import json
import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

AZURE_ENDPOINT = os.getenv("AZURE_OPENAI_ENDPOINT", "https://CraveOpenAI-1.openai.azure.com/")
AZURE_API_KEY = os.getenv("AZURE_OPENAI_API_KEY", "")
AZURE_API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
AZURE_CHAT_DEPLOYMENT = os.getenv("AZURE_OPENAI_CHAT_DEPLOYMENT", "GPT-4o-1")

SYSTEM_PROMPT = (
    "You are an SAP BOBJ to Datasphere/SAC migration expert. Analyze the BOBJ "
    'artifact and return a JSON response with the top-level keys "analysis", '
    '"datasphereEntities", "sacModelConfig", "conversionMapping" and '
    '"summary". Return ONLY valid JSON, no markdown, no explanation.'
)


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

Analyze this BOBJ artifact IN DETAIL and return a JSON response with:
{{
  "analysis": {{
    "dataSources": [{{"name": "source table/object", "type": "BW ADSO|Master Data|Time Characteristic|Universe Table", "description": "what it provides"}}],
    "dimensions": [{{"name": "dim", "dataType": "string|integer|decimal|date", "source": "source object"}}],
    "measures": [{{"name": "measure", "dataType": "decimal|integer", "aggregation": "SUM|AVG|COUNT|MIN|MAX"}}],
    "calculations": [{{"name": "calc name", "formula": "the actual formula/expression", "critical": true, "description": "what it computes + migration note"}}]
  }},
  "datasphereEntities": [
    {{
      "name": "entity name",
      "type": "dimension|fact|analytic_model",
      "columns": [{{"name": "col", "type": "string|integer|decimal|date", "is_key": false}}],
      "description": "description"
    }}
  ],
  "sacModelConfig": {{
    "model_name": "model name",
    "model_type": "planning|analytic",
    "description": "description",
    "dimensions": [{{"name": "dim", "type": "dimension"}}],
    "measures": [{{"name": "measure", "aggregation": "SUM|AVG|COUNT"}}],
    "data_connections": []
  }},
  "conversionMapping": [
    {{"source": "source object", "target": "target object", "status": "converted|manual_review|not_supported", "notes": "notes"}}
  ],
  "summary": {{
    "totalObjects": 0,
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
            {
                "role": "system",
                "content": SYSTEM_PROMPT,
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": 6000,
        "temperature": 0.1,
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            data = response.json()
            if "choices" in data:  # Azure OpenAI / OpenAI chat format
                content = data["choices"][0]["message"]["content"]
            elif "content" in data:  # Anthropic Messages format
                content = data["content"][0]["text"]
            else:
                raise KeyError("Unrecognized LLM response format")
            content = content.strip()
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
            return json.loads(content.strip())
    except Exception as e:
        logger.error(f"Azure OpenAI error: {e}")
        raise


async def run_conversion(input_type: str, raw_content: str) -> dict[str, Any]:
    """Alias used by conversion router — delegates to convert_bobj_artifact."""
    return await convert_bobj_artifact(
        input_type=input_type,
        artifact_name="artifact",
        raw_content=raw_content,
    )
