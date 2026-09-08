"""Provider registry.

Adapters register themselves on import. The router resolves a route's `provider`
string through `get_provider`, so adding a backend touches this package only.
"""
from __future__ import annotations

from typing import Dict, List, Type

from app.providers.base import (  # noqa: F401  (re-exported for adapters)
    BaseProvider,
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderConnectionError,
    ProviderError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderRequest,
    ProviderTimeoutError,
    classify_http_error,
    wrap_transport_error,
)

_REGISTRY: Dict[str, BaseProvider] = {}


class UnknownProviderError(KeyError):
    def __init__(self, name: str):
        super().__init__(name)
        self.name = name

    def __str__(self) -> str:
        known = ", ".join(available_providers()) or "none"
        return f"Unknown provider '{self.name}'. Registered providers: {known}."


def register(cls: Type[BaseProvider]) -> Type[BaseProvider]:
    """Class decorator. Providers are stateless, so one instance is enough."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set a non-empty `name`.")
    if cls.name in _REGISTRY:
        raise ValueError(f"Provider '{cls.name}' is already registered.")
    _REGISTRY[cls.name] = cls()
    return cls


def get_provider(name: str) -> BaseProvider:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise UnknownProviderError(name) from None


def available_providers() -> List[str]:
    return sorted(_REGISTRY)


# Importing these modules is what populates the registry.
from app.providers import anthropic, ollama, openai_compatible  # noqa: E402,F401

__all__ = [
    "BaseProvider",
    "ProviderRequest",
    "ProviderError",
    "ProviderAuthError",
    "ProviderBadRequestError",
    "ProviderConnectionError",
    "ProviderOverloadedError",
    "ProviderRateLimitError",
    "ProviderTimeoutError",
    "UnknownProviderError",
    "classify_http_error",
    "wrap_transport_error",
    "get_provider",
    "available_providers",
    "register",
]
