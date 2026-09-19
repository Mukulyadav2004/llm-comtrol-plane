from app.api import config


def test_generated_at_does_not_change_version_but_routes_do(monkeypatch):
    monkeypatch.setattr(config, "_version_counter", 100)
    monkeypatch.setattr(config, "_last_content", {})

    first = config._version_for_content(
        "gateway", {"version": "100", "generated_at": "one", "routes": []}
    )
    same = config._version_for_content(
        "gateway", {"version": first["version"], "generated_at": "two", "routes": []}
    )
    changed = config._version_for_content(
        "gateway", {"version": same["version"], "generated_at": "three", "routes": [{"name": "demo"}]}
    )

    assert first["version"] == same["version"] == "101"
    assert changed["version"] == "102"
