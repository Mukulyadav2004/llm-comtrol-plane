"""Token counting and usage reconciliation."""
from app.middleware.token_counter import count_message_tokens, count_tokens, resolve_usage


def test_empty_text_is_zero_tokens():
    assert count_tokens("") == 0
    assert count_tokens(None) == 0


def test_longer_text_costs_more_tokens():
    assert count_tokens("hello world, this is a longer sentence") > count_tokens("hi")


def test_message_token_count_includes_framing_overhead():
    messages = [{"role": "user", "content": "hi"}]
    assert count_message_tokens(messages) > count_tokens("hi")


def test_provider_reported_usage_wins():
    usage = resolve_usage(
        {"prompt_tokens": 11, "completion_tokens": 7},
        [{"role": "user", "content": "anything at all"}],
        "some generated response",
    )
    assert usage == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}


def test_missing_usage_is_estimated():
    usage = resolve_usage(None, [{"role": "user", "content": "hello"}], "a reply")
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0
    assert usage["total_tokens"] == usage["prompt_tokens"] + usage["completion_tokens"]


def test_zero_completion_with_output_text_is_treated_as_unreported():
    """A provider claiming 0 completion tokens for real output would silently
    collapse that request's cost to zero."""
    usage = resolve_usage(
        {"prompt_tokens": 5, "completion_tokens": 0},
        [{"role": "user", "content": "hi"}],
        "a genuinely non-empty answer",
    )
    assert usage["completion_tokens"] > 0
    assert usage["prompt_tokens"] == 5


def test_empty_completion_stays_zero():
    usage = resolve_usage({"prompt_tokens": 5, "completion_tokens": 0},
                          [{"role": "user", "content": "hi"}], "")
    assert usage["completion_tokens"] == 0
