"""API key resolution — routes name an env var, never carry a secret."""
import pytest

from app.providers import get_provider
from app.providers.base import ProviderAuthError
from app.providers.credentials import resolve_api_key


def route(**kwargs):
    base = {"name": "r", "provider": "openai_compatible", "model": "m"}
    base.update(kwargs)
    return base


def test_route_env_var_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_secret")
    key = resolve_api_key(get_provider("openai_compatible"),
                          route(api_key_env="GROQ_API_KEY"))
    assert key == "gsk_secret"


def test_route_env_var_beats_the_provider_default(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-default")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-specific")
    key = resolve_api_key(get_provider("openai_compatible"),
                          route(api_key_env="GROQ_API_KEY"))
    assert key == "gsk-specific"


def test_provider_default_is_used_when_the_route_names_nothing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-default")
    assert resolve_api_key(get_provider("openai_compatible"), route()) == "sk-default"


def test_naming_an_unset_env_var_fails_loudly(monkeypatch):
    """Asking for a key from GROQ_API_KEY is a statement of intent. Sending the
    request unauthenticated instead turns a config mistake into a confusing 401
    from a third party."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(ProviderAuthError, match="GROQ_API_KEY"):
        resolve_api_key(get_provider("openai_compatible"),
                        route(api_key_env="GROQ_API_KEY"))


def test_an_empty_env_var_counts_as_unset(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "   ")
    with pytest.raises(ProviderAuthError):
        resolve_api_key(get_provider("openai_compatible"),
                        route(api_key_env="GROQ_API_KEY"))


def test_whitespace_around_a_key_is_stripped(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "  gsk_secret\n")
    key = resolve_api_key(get_provider("openai_compatible"),
                          route(api_key_env="GROQ_API_KEY"))
    assert key == "gsk_secret"


def test_local_providers_need_no_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert resolve_api_key(get_provider("ollama"), route(provider="ollama")) is None


def test_a_provider_that_requires_a_key_refuses_to_proceed_without_one(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ProviderAuthError, match="ANTHROPIC_API_KEY"):
        resolve_api_key(get_provider("anthropic"), route(provider="anthropic"))


def test_an_unauthenticated_openai_compatible_route_is_allowed(monkeypatch):
    """The same adapter serves a local vLLM, which wants no credential."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert resolve_api_key(get_provider("openai_compatible"), route()) is None
