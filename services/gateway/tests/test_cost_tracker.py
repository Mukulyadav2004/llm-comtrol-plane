"""Cost counters."""
from app.middleware.cost_tracker import (
    get_all_stats,
    get_route_stats,
    list_tracked_routes,
    record_usage,
)


def test_usage_accumulates_across_requests():
    record_usage("r", 100, 50, 0.002)
    record_usage("r", 100, 50, 0.002)
    stats = get_route_stats("r")
    assert stats["total_requests"] == 2
    assert stats["total_tokens"] == 300
    assert stats["prompt_tokens"] == 200
    assert stats["completion_tokens"] == 100


def test_cost_is_tokens_over_thousand_times_rate():
    record_usage("r", 500, 500, 0.002)
    assert get_route_stats("r")["total_usd"] == 0.002


def test_zero_rate_costs_nothing():
    record_usage("free", 1000, 1000, 0.0)
    assert get_route_stats("free")["total_usd"] == 0.0


def test_unknown_route_reports_zeroes():
    assert get_route_stats("never-used")["total_requests"] == 0


def test_tracked_routes_are_discovered():
    record_usage("alpha", 1, 1, 0.0)
    record_usage("beta", 1, 1, 0.0)
    assert list_tracked_routes() == ["alpha", "beta"]


def test_route_names_containing_colons_survive_key_parsing():
    record_usage("team:prod", 1, 1, 0.0)
    assert "team:prod" in list_tracked_routes()


def test_all_stats_covers_every_tracked_route():
    record_usage("alpha", 1, 1, 0.0)
    record_usage("beta", 1, 1, 0.0)
    assert {row["route"] for row in get_all_stats()} == {"alpha", "beta"}


def test_no_usage_yields_no_rows():
    assert get_all_stats() == []
