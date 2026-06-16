"""Optional API-key auth for the OpenAI-compatible API.

Behaviour mirrors hosted gateways (LiteLLM, OpenAI):
  - The client sends `Authorization: Bearer <key>`.
  - When `settings.require_auth` is False (default) any non-empty key is
    accepted, so dropping the gateway's base_url into the OpenAI SDK just works.
  - When `settings.require_auth` is True the key must be in `settings.api_keys`.

Returns the caller's key (or "anonymous") so downstream code can attribute
usage / rate limits per key later.
"""
from typing import Optional

from fastapi import Header, HTTPException, status

from app.config import settings


def require_api_key(authorization: Optional[str] = Header(default=None)) -> str:
    if not settings.require_auth:
        # Auth disabled — accept anything, including no header at all.
        if authorization and authorization.lower().startswith("bearer "):
            return authorization.split(" ", 1)[1].strip()
        return "anonymous"

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or malformed Authorization header. Expected 'Bearer <api-key>'.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    key = authorization.split(" ", 1)[1].strip()
    if key not in settings.api_key_set:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return key
