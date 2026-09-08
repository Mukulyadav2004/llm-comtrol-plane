"""Error classification — the part a retry layer will depend on."""
import httpx
import pytest

from app.providers.base import (
    ProviderAuthError,
    ProviderBadRequestError,
    ProviderConnectionError,
    ProviderOverloadedError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    classify_http_error,
    wrap_transport_error,
)


@pytest.mark.parametrize("status,expected", [
    (400, ProviderBadRequestError),
    (401, ProviderAuthError),
    (403, ProviderAuthError),
    (404, ProviderBadRequestError),
    (422, ProviderBadRequestError),
    (429, ProviderRateLimitError),
    (500, ProviderOverloadedError),
    (502, ProviderOverloadedError),
    (503, ProviderOverloadedError),
    (529, ProviderOverloadedError),
])
def test_status_codes_map_to_the_right_error(status, expected):
    assert isinstance(classify_http_error(status, "test"), expected)


@pytest.mark.parametrize("status,retryable", [
    (400, False),
    (401, False),
    (429, True),
    (503, True),
    (529, True),
])
def test_retryability_is_decided_where_the_status_is_still_known(status, retryable):
    """Fallback and (later) retry logic branch on this flag, so it has to be set
    at classification time rather than re-derived from an error string."""
    assert classify_http_error(status, "test").retryable is retryable


def test_anthropics_529_is_overload_not_a_client_error():
    """529 is non-standard. Bucketed with 4xx it would look permanent and stop a
    fallback from ever firing."""
    error = classify_http_error(529, "anthropic")
    assert error.retryable is True


def test_rate_limit_carries_retry_after():
    error = classify_http_error(
        429, "test", headers=httpx.Headers({"retry-after": "30"})
    )
    assert error.retry_after == 30.0


def test_http_date_retry_after_degrades_to_none_rather_than_raising():
    error = classify_http_error(
        429, "test", headers=httpx.Headers({"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"})
    )
    assert error.retry_after is None
    assert error.retryable is True


def test_error_text_names_the_provider_and_status():
    error = classify_http_error(401, "anthropic", "invalid x-api-key")
    assert "anthropic" in str(error)
    assert "401" in str(error)
    assert "invalid x-api-key" in str(error)


def test_a_huge_error_body_is_truncated():
    error = classify_http_error(400, "test", "x" * 5000)
    assert len(str(error)) < 700


def test_timeouts_and_connection_failures_are_retryable():
    assert isinstance(
        wrap_transport_error(httpx.ConnectTimeout("slow"), "t"), ProviderTimeoutError
    )
    assert isinstance(
        wrap_transport_error(httpx.ConnectError("refused"), "t"), ProviderConnectionError
    )
    assert wrap_transport_error(httpx.ConnectError("refused"), "t").retryable is True
