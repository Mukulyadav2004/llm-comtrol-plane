"""Incremental output guardrails over a token stream."""
import pytest

from app.middleware.guardrails import GuardrailViolation, compile_stream_rules
from app.middleware.stream_guard import StreamGuard


def g(gtype, action="redact", **config):
    return {
        "id": gtype, "name": gtype, "type": gtype,
        "action_on_violation": action,
        "apply_on_input": True, "apply_on_output": True,
        "config": config,
    }


def drain(guard, deltas):
    out = "".join(guard.push(d) for d in deltas)
    return out + guard.flush()


def test_no_rules_passes_text_through_immediately():
    guard = StreamGuard([])
    assert guard.push("hello ") == "hello "
    assert guard.push("world") == "world"
    assert guard.flush() == ""


def test_pattern_split_across_chunks_is_still_caught():
    """The reason a stream needs a hold-back buffer at all: providers split
    tokens arbitrarily, so no individual delta contains the whole match."""
    guard = StreamGuard(compile_stream_rules([g("pii")]))
    result = drain(guard, ["contact al", "ice@exa", "mple.com", " today"])

    assert "alice@example.com" not in result
    assert "[EMAIL_REDACTED]" in result
    assert result.startswith("contact ")
    assert result.endswith(" today")


def test_released_property_matches_what_was_emitted():
    guard = StreamGuard(compile_stream_rules([g("pii")]))
    result = drain(guard, ["my ssn is ", "123-45", "-6789 ok"])
    assert guard.released == result
    assert "123-45-6789" not in guard.released


def test_text_is_held_back_until_flush():
    guard = StreamGuard(compile_stream_rules([g("pii")]), hold_back=16)
    assert guard.push("short") == ""
    assert guard.flush() == "short"


def test_long_stream_releases_progressively():
    guard = StreamGuard(compile_stream_rules([g("pii")]), hold_back=8)
    emitted = [guard.push("abcdefgh") for _ in range(3)]
    assert any(chunk for chunk in emitted), "should release once past hold-back"
    assert drain(StreamGuard(compile_stream_rules([g("pii")]), 8), ["abcdefgh"] * 3) == "abcdefgh" * 3


def test_blocking_rule_raises_mid_stream():
    guard = StreamGuard(compile_stream_rules([g("keyword", action="block", keywords=["secret"])]))
    with pytest.raises(GuardrailViolation):
        drain(guard, ["the sec", "ret is out", " " * 80])


def test_keyword_rule_redacts_across_a_boundary():
    guard = StreamGuard(compile_stream_rules([g("keyword", keywords=["hunter2"])]))
    result = drain(guard, ["pw is hun", "ter2 ok"])
    assert "hunter2" not in result
    assert "[REDACTED]" in result


def test_clean_stream_is_reassembled_exactly():
    guard = StreamGuard(compile_stream_rules([g("pii")]))
    deltas = ["The ", "quick ", "brown ", "fox ", "jumps."]
    assert drain(guard, deltas) == "".join(deltas)
