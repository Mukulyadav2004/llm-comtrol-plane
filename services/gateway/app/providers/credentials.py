"""API key resolution.

Routes never carry a secret. They carry the *name* of an environment variable:

    {"provider": "openai_compatible", "model": "openai/gpt-oss-20b",
     "base_url": "https://api.groq.com/openai/v1", "api_key_env": "GROQ_API_KEY"}

That constraint is deliberate. Route config is authored in the Broker, rendered
by the Control Plane, cached in Redis and polled by every gateway replica. A key
placed in that config would come to rest, in plaintext, in a cache nobody
treats as a secret store, and would show up in `/v1/routes` and in any config
dump. Passing the variable's name keeps the secret in the gateway's own
environment, where a container secret or a mounted file can supply it.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from app.providers.base import BaseProvider, ProviderAuthError

log = logging.getLogger(__name__)


def resolve_api_key(provider: BaseProvider, route: Dict[str, Any]) -> Optional[str]:
    """Look up the key for a route, or raise if one is required and absent.

    Precedence:
      1. the route's `api_key_env`
      2. the provider's default env var (e.g. ANTHROPIC_API_KEY)
    """
    env_name = route.get("api_key_env") or provider.api_key_env

    if not env_name:
        if provider.requires_api_key:
            raise ProviderAuthError(
                f"Provider '{provider.name}' requires an API key but route "
                f"'{route.get('name', '?')}' names no api_key_env and the provider "
                f"declares no default environment variable.",
                provider=provider.name,
            )
        return None

    key = os.environ.get(env_name, "").strip()
    if not key:
        # An explicitly named api_key_env is a statement of intent: the operator
        # said this route authenticates. Failing here beats letting the request
        # go out unauthenticated and surface as a confusing 401 from upstream.
        explicitly_requested = bool(route.get("api_key_env"))
        if provider.requires_api_key or explicitly_requested:
            raise ProviderAuthError(
                f"Environment variable '{env_name}' is unset or empty, and provider "
                f"'{provider.name}' requires an API key. Set it on the gateway "
                f"container.",
                provider=provider.name,
            )
        log.debug("credentials.absent env=%s provider=%s", env_name, provider.name)
        return None

    return key
