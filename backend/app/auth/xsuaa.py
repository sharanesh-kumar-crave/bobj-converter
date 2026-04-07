import json
import logging
import os

import jwt
from fastapi import HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

logger = logging.getLogger(__name__)
_jwks_client: PyJWKClient | None = None


def _get_xsuaa_config() -> dict:
    """Extract XSUAA credentials from VCAP_SERVICES."""
    vcap_raw = os.getenv("VCAP_SERVICES", "{}")
    vcap = json.loads(vcap_raw)
    xsuaa_list = vcap.get("xsuaa", [])
    if not xsuaa_list:
        raise RuntimeError("XSUAA service binding not found in VCAP_SERVICES")
    creds = xsuaa_list[0]["credentials"]
    return {
        "clientid": creds["clientid"],
        "url": creds["url"],  # e.g. https://<subdomain>.authentication.eu20.hana.ondemand.com
        "xsappname": creds["xsappname"],
        "jwks_uri": f"{creds['url']}/token_keys",
    }


def _get_jwks_client(jwks_uri: str) -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(jwks_uri, cache_keys=True)
    return _jwks_client


security = HTTPBearer()


async def verify_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials = security,
) -> dict:
    import os
    if os.getenv("ENVIRONMENT", "local") == "local":
        request.state.user = {
            "sub": "local-dev-user",
            "scope": "bobj-converter.convert bobj-converter.read bobj-converter.push bobj-converter.admin",
            "email": "dev@local.test",
        }
        return request.state.user
    # ... rest of existing function below unchanged
    """
    Validate JWT issued by XSUAA / SAP IAS.
    Returns the decoded token payload (user info + scopes).
    """
    token = credentials.credentials
    try:
        xsuaa = _get_xsuaa_config()
        jwks_client = _get_jwks_client(xsuaa["jwks_uri"])
        signing_key = jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=xsuaa["clientid"],
            options={"verify_exp": True},
        )
        request.state.user = payload
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as e:
        logger.warning("Invalid JWT: %s", str(e))
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    except Exception as e:
        logger.error("Token verification error: %s", str(e))
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Auth error")


def require_scope(scope: str):
    """Dependency factory — raises 403 if user lacks the required XSUAA scope."""

    async def _check(request: Request):
        payload = getattr(request.state, "user", {})
        scopes = payload.get("scope", "").split()
        xsuaa = _get_xsuaa_config()
        full_scope = f"{xsuaa['xsappname']}.{scope}"
        if full_scope not in scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope: {full_scope}",
            )

    return _check
