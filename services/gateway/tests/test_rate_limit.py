"""Sliding-window rate limiter."""
from app.middleware.rate_limit import check_rate_limit, check_rate_limit_detailed


def test_allows_requests_under_the_limit():
    for _ in range(4):
        assert check_rate_limit("r", 5, "client-a") is True


def test_denies_once_the_limit_is_reached():
    for _ in range(5):
        assert check_rate_limit("r", 5, "client-a") is True
    assert check_rate_limit("r", 5, "client-a") is False


def test_rejected_requests_do_not_consume_window_slots():
    """Regression: the pipeline version did ZADD before ZCARD, so every denied
    request still occupied a slot and kept extending its own lockout."""
    for _ in range(3):
        check_rate_limit("r", 3, "spammer")

    for _ in range(20):
        assert check_rate_limit("r", 3, "spammer") is False

    decision = check_rate_limit_detailed("r", 3, "spammer")
    assert decision.current == 3, "denied requests must not grow the window"


def test_clients_are_isolated_from_each_other():
    for _ in range(5):
        check_rate_limit("r", 5, "noisy")
    assert check_rate_limit("r", 5, "noisy") is False
    assert check_rate_limit("r", 5, "quiet") is True


def test_routes_are_isolated_from_each_other():
    for _ in range(5):
        check_rate_limit("route-a", 5, "c")
    assert check_rate_limit("route-a", 5, "c") is False
    assert check_rate_limit("route-b", 5, "c") is True


def test_denied_decision_reports_a_usable_retry_after():
    for _ in range(2):
        check_rate_limit("r", 2, "c")
    decision = check_rate_limit_detailed("r", 2, "c")
    assert decision.allowed is False
    assert decision.retry_after_ms > 0
    assert decision.retry_after_seconds >= 1


def test_remaining_counts_down_to_zero():
    assert check_rate_limit_detailed("r", 3, "c").remaining == 2
    assert check_rate_limit_detailed("r", 3, "c").remaining == 1
    assert check_rate_limit_detailed("r", 3, "c").remaining == 0


def test_zero_limit_denies_everything():
    assert check_rate_limit("r", 0, "c") is False
