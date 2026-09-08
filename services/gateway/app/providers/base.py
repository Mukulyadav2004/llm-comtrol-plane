"""Provider interface.

Every upstream — Ollama, anything speaking the OpenAI wire format, Anthropic —
is reached through one interface so the router never learns a provider's name.
Adding a backend means writing a subclass, not editing the router.

Three things live here that are easy to get wrong if each adapter improvises:

  * `ProviderRequest` — one value object instead of a growing kwarg list that
    every adapter has to keep in the same order.
  * A normalised error hierarchy. Providers disagree about which status code
    means "slow down" versus "you sent nonsense", and a retry layer is only as
    good as its ability to tell those apart. `classify_http_error` is the single
    place that mapping happens.
  * The streaming chunk contract, documented on `stream_chat_completion`.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar, Dict, List, Optional

import httpx


# ── Errors ────────────────────────────────────────────────────────────────────

class ProviderError(Exception):
    """Base for every upstream failure, carrying enough to decide what next.

    `retryable` is the field the (not yet written) retry and cooldown layer will
    read. It is set here, at the point where we still know what the provider
    actually said, rather than being re-derived later from a stringified error.
    """

    retryable: ClassVar[bool] = False

    def __init__(
        self,
        message: str,
        *,
        provider: str,
        status_code: Optional[int] = None,
        retry_after: Optional[float] = None,
    ):
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after

    def __str__(self) -> str:
        base = super().__str__()
        if self.status_code:
            return f"[{self.provider} {self.status_code}] {base}"
        return f"[{self.provider}] {base}"


class ProviderAuthError(ProviderError):
    """401/403. Retrying with the same credentials cannot help."""


class ProviderBadRequestError(ProviderError):
    """400/404/422. The request is malformed or the model does not exist."""


class ProviderRateLimitError(ProviderError):
    """429. Retryable, ideally after `retry_after` seconds."""

    retryable: ClassVar[bool] = True


class ProviderOverloadedError(ProviderError):
    """5xx, plus Anthropic's 529. The upstream is unwell; try elsewhere."""

    retryable: ClassVar[bool] = True


class ProviderTimeoutError(ProviderError):
    retryable: ClassVar[bool] = True


class ProviderConnectionError(ProviderError):
    retryable: ClassVar[bool] = True


def _retry_after_seconds(headers: httpx.Headers) -> Optional[float]:
    raw = headers.get("retry-after")
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        # The HTTP-date form. Not worth parsing precisely; the caller has a
        # backoff of its own and this is only a hint.
        return None


def classify_http_error(
    status_code: int,
    provider: str,
    body: str = "",
    headers: Optional[httpx.Headers] = None,
) -> ProviderError:
    """Map an upstream status code onto the error the router can act on."""
    headers = headers if headers is not None else httpx.Headers()
    detail = (body or "").strip()[:500] or f"HTTP {status_code}"
    kwargs: Dict[str, Any] = {"provider": provider, "status_code": status_code}

    if status_code in (401, 403):
        return ProviderAuthError(detail, **kwargs)
    if status_code == 429:
        return ProviderRateLimitError(
            detail, retry_after=_retry_after_seconds(headers), **kwargs
        )
    # 529 is Anthropic's "overloaded"; it is not a standard code, and treating
    # it as a client error would stop a fallback from ever firing.
    if status_code >= 500 or status_code == 529:
        return ProviderOverloadedError(detail, **kwargs)
    return ProviderBadRequestError(detail, **kwargs)


def wrap_transport_error(exc: Exception, provider: str) -> ProviderError:
    """Translate httpx transport failures into the same hierarchy."""
    if isinstance(exc, httpx.TimeoutException):
        return ProviderTimeoutError(str(exc) or "request timed out", provider=provider)
    if isinstance(exc, httpx.TransportError):
        return ProviderConnectionError(str(exc) or "connection failed", provider=provider)
    return ProviderError(str(exc), provider=provider)


# ── Request ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ProviderRequest:
    """Everything an adapter needs for one completion."""

    model: str
    messages: List[Dict[str, Any]]
    max_tokens: int = 4096
    temperature: float = 0.7
    stop: Optional[List[str]] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    timeout: float = 120.0
    # Provider-specific passthrough (top_p, seed, an Anthropic thinking budget).
    extra: Dict[str, Any] = field(default_factory=dict)

    def system_prompt(self) -> Optional[str]:
        """Concatenated system messages, or None.

        Some APIs take the system prompt as a top-level parameter rather than a
        message with role='system' (Anthropic), so adapters need it separately.
        """
        parts = [
            m.get("content", "")
            for m in self.messages
            if m.get("role") == "system" and m.get("content")
        ]
        return "\n\n".join(parts) if parts else None

    def conversation(self) -> List[Dict[str, Any]]:
        """Messages with system turns removed."""
        return [m for m in self.messages if m.get("role") != "system"]


# ── Provider ──────────────────────────────────────────────────────────────────

class BaseProvider(ABC):
    """One upstream API.

    Subclasses are stateless and instantiated once; do not stash per-request
    state on `self`.
    """

    #: Value used in a route's `provider` field.
    name: ClassVar[str] = ""
    #: Used when a route does not set `base_url`.
    default_base_url: ClassVar[Optional[str]] = None
    #: Environment variable consulted when a route names no `api_key_env`.
    api_key_env: ClassVar[Optional[str]] = None
    #: Whether a missing key is fatal. False for local providers like Ollama.
    requires_api_key: ClassVar[bool] = False
    #: Advertised in /v1/models so clients can see what a route can do.
    supports_streaming: ClassVar[bool] = True

    def _client(self, req: ProviderRequest) -> httpx.AsyncClient:
        """Build the HTTP client for one request.

        A seam, not ceremony: tests swap this for a client wired to an
        httpx.MockTransport, so adapter behaviour can be pinned against exact
        wire payloads without patching httpx globally or standing up a server.
        """
        return httpx.AsyncClient(timeout=req.timeout)

    def resolve_base_url(self, req: ProviderRequest) -> str:
        url = req.base_url or self.default_base_url or ""
        return url.rstrip("/")

    @abstractmethod
    async def chat_completion(self, req: ProviderRequest) -> Dict[str, Any]:
        """Return one OpenAI-shaped `chat.completion` dict.

        Must include `choices[0].message.content`, `choices[0].finish_reason`
        and a `usage` dict. Report zeros in `usage` rather than omitting it when
        the upstream gives no counts — the router estimates from there.
        """

    @abstractmethod
    def stream_chat_completion(self, req: ProviderRequest) -> AsyncIterator[Dict[str, Any]]:
        """Yield OpenAI-shaped `chat.completion.chunk` dicts as they arrive.

        Contract:

        * The first chunk carrying text sets `delta.role = "assistant"`.
        * Text arrives as `choices[0].delta.content`.
        * Exactly one chunk sets `choices[0].finish_reason`.
        * A chunk may carry a non-standard `_usage` key with the upstream's real
          token counts. It may be the finish chunk or a later usage-only chunk
          with `choices: []` — providers disagree about ordering, so the router
          harvests `_usage` wherever it appears and ignores empty `choices`.
        """
        raise NotImplementedError


__all__ = [
    "BaseProvider",
    "ProviderRequest",
    "ProviderError",
    "ProviderAuthError",
    "ProviderBadRequestError",
    "ProviderRateLimitError",
    "ProviderOverloadedError",
    "ProviderTimeoutError",
    "ProviderConnectionError",
    "classify_http_error",
    "wrap_transport_error",
]
