"""Guardrails — per-message application."""
import pytest

from app.middleware.guardrails import (
    GuardrailViolation,
    apply_input_guardrails,
    apply_output_guardrails,
)


def g(gtype, action="redact", name=None, **config):
    return {
        "id": name or gtype,
        "name": name or gtype,
        "type": gtype,
        "action_on_violation": action,
        "apply_on_input": True,
        "apply_on_output": True,
        "config": config,
    }


def test_redaction_lands_in_the_message_that_matched():
    """Regression: the old code flattened every message into one string, scanned
    that, then wrote the result back to the *last user message* — so a match in
    an earlier turn rewrote the wrong message and leaked the original."""
    messages = [
        {"role": "user", "content": "my email is alice@example.com"},
        {"role": "assistant", "content": "noted"},
        {"role": "user", "content": "what did I say?"},
    ]
    out = apply_input_guardrails(messages, [g("pii")])

    assert "alice@example.com" not in out[0]["content"]
    assert "[EMAIL_REDACTED]" in out[0]["content"]
    assert out[2]["content"] == "what did I say?", "unmatched message must be untouched"


def test_clean_messages_pass_through_unchanged():
    messages = [{"role": "user", "content": "hello there"}]
    assert apply_input_guardrails(messages, [g("pii")]) == messages


def test_input_guardrails_do_not_mutate_the_caller_list():
    messages = [{"role": "user", "content": "ssn 123-45-6789"}]
    apply_input_guardrails(messages, [g("pii")])
    assert messages[0]["content"] == "ssn 123-45-6789"


def test_system_messages_are_skipped_by_default():
    messages = [
        {"role": "system", "content": "escalate to ops@corp.com"},
        {"role": "user", "content": "hi"},
    ]
    out = apply_input_guardrails(messages, [g("pii")])
    assert out[0]["content"] == "escalate to ops@corp.com"


def test_system_messages_can_opt_in():
    messages = [{"role": "system", "content": "escalate to ops@corp.com"}]
    out = apply_input_guardrails(messages, [g("pii", include_system=True)])
    assert "[EMAIL_REDACTED]" in out[0]["content"]


def test_block_action_raises():
    messages = [{"role": "user", "content": "ssn 123-45-6789"}]
    with pytest.raises(GuardrailViolation) as exc:
        apply_input_guardrails(messages, [g("pii", action="block")])
    assert exc.value.guardrail_name == "pii"


@pytest.mark.parametrize("card", [
    "4111111111111111",      # Visa
    "5500005555555559",      # Mastercard
    "371449635398431",       # Amex
    "6011000990139424",      # Discover
])
def test_card_numbers_are_caught_for_every_major_network(card):
    """The original pattern only matched Visa, so a Mastercard walked straight
    through a guardrail whose whole job was catching card numbers."""
    out = apply_input_guardrails([{"role": "user", "content": f"card {card}"}], [g("pii")])
    assert card not in out[0]["content"]


def test_keyword_guardrail_redacts_case_insensitively():
    out = apply_input_guardrails(
        [{"role": "user", "content": "the Password is hunter2"}],
        [g("keyword", keywords=["password"])],
    )
    assert "Password" not in out[0]["content"]
    assert "[REDACTED]" in out[0]["content"]


def test_regex_guardrail_blocks():
    with pytest.raises(GuardrailViolation):
        apply_input_guardrails(
            [{"role": "user", "content": "AKIAIOSFODNN7EXAMPLE"}],
            [g("regex", action="block", pattern=r"AKIA[0-9A-Z]{16}")],
        )


def test_length_guardrail_measures_the_whole_prompt_not_one_message():
    messages = [{"role": "user", "content": "x" * 60} for _ in range(3)]
    with pytest.raises(GuardrailViolation):
        apply_input_guardrails(messages, [g("length", action="block", max_chars=100)])


def test_length_guardrail_allows_a_prompt_under_budget():
    messages = [{"role": "user", "content": "x" * 20}]
    apply_input_guardrails(messages, [g("length", action="block", max_chars=100)])


def test_guardrail_not_marked_for_input_is_skipped():
    rule = g("pii")
    rule["apply_on_input"] = False
    messages = [{"role": "user", "content": "alice@example.com"}]
    assert apply_input_guardrails(messages, [rule]) == messages


def test_output_guardrails_redact_response_text():
    assert "[EMAIL_REDACTED]" in apply_output_guardrails("reach me at a@b.com", [g("pii")])


def test_warn_action_leaves_content_intact():
    out = apply_input_guardrails(
        [{"role": "user", "content": "alice@example.com"}], [g("pii", action="warn")]
    )
    assert "alice@example.com" in out[0]["content"]


def test_multiple_guardrails_compose():
    out = apply_input_guardrails(
        [{"role": "user", "content": "mail a@b.com and say password"}],
        [g("pii"), g("keyword", name="kw", keywords=["password"])],
    )
    assert "a@b.com" not in out[0]["content"]
    assert "password" not in out[0]["content"]
