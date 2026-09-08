"""Provider registry."""
import pytest

from app.providers import (
    BaseProvider,
    UnknownProviderError,
    available_providers,
    get_provider,
    register,
)


def test_every_shipped_provider_is_registered():
    assert set(available_providers()) == {"anthropic", "ollama", "openai_compatible"}


@pytest.mark.parametrize("name", ["anthropic", "ollama", "openai_compatible"])
def test_lookup_returns_an_instance_whose_name_matches(name):
    provider = get_provider(name)
    assert isinstance(provider, BaseProvider)
    assert provider.name == name


def test_providers_are_singletons():
    assert get_provider("ollama") is get_provider("ollama")


def test_unknown_provider_error_names_what_is_available():
    with pytest.raises(UnknownProviderError) as exc:
        get_provider("gpt5-turbo-max")
    message = str(exc.value)
    assert "gpt5-turbo-max" in message
    assert "ollama" in message, "the error should tell you what you could have said"


def test_registering_a_duplicate_name_is_refused():
    with pytest.raises(ValueError, match="already registered"):
        @register
        class Dupe(BaseProvider):
            name = "ollama"

            async def chat_completion(self, req):
                ...

            async def stream_chat_completion(self, req):
                yield {}


def test_a_provider_must_declare_a_name():
    with pytest.raises(ValueError, match="non-empty"):
        @register
        class Nameless(BaseProvider):
            async def chat_completion(self, req):
                ...

            async def stream_chat_completion(self, req):
                yield {}


def test_base_url_falls_back_to_the_provider_default(provider_request):
    provider = get_provider("ollama")
    assert provider.resolve_base_url(provider_request()) == "http://ollama:11434"


def test_route_base_url_wins_and_loses_its_trailing_slash(provider_request):
    provider = get_provider("openai_compatible")
    req = provider_request(base_url="https://api.groq.com/openai/v1/")
    assert provider.resolve_base_url(req) == "https://api.groq.com/openai/v1"
