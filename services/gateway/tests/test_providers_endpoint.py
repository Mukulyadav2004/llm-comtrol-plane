"""GET /v1/providers — what the data plane can actually serve."""
import pytest
from fastapi.testclient import TestClient

from app import main as app_main


@pytest.fixture
def client():
    return TestClient(app_main.app)


def test_lists_every_registered_provider(client):
    names = {p["name"] for p in client.get("/v1/providers").json()["providers"]}
    assert names == {"anthropic", "ollama", "openai_compatible"}


def test_each_entry_describes_how_to_configure_it(client):
    entry = next(p for p in client.get("/v1/providers").json()["providers"]
                 if p["name"] == "anthropic")
    assert entry["api_key_env"] == "ANTHROPIC_API_KEY"
    assert entry["requires_api_key"] is True
    assert entry["default_base_url"].startswith("https://")


def test_local_providers_are_marked_as_needing_no_key(client):
    entry = next(p for p in client.get("/v1/providers").json()["providers"]
                 if p["name"] == "ollama")
    assert entry["requires_api_key"] is False
