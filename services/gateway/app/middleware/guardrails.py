"""Guardrail middleware — applies input/output filters to LLM traffic.

Scanning guardrails run **per message**, not over a flattened blob of the whole
conversation. The previous implementation joined every message into one string,
scanned that, and then wrote the redacted result back into the *last user
message* — so a match in an earlier turn silently rewrote the wrong message and
let the original text through untouched. Redaction has to land where the match
was found.

Two deliberate semantics:

  * `system` messages are skipped by default. A system prompt is operator
    authored, not user input; silently rewriting it breaks the application's
    contract with its own model. Opt in per guardrail with
    ``config.include_system: true``.
  * The `length` guardrail is evaluated against the **total** prompt, since it
    is a budget on the whole request rather than a property of one message.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

log = logging.getLogger(__name__)

# Minimal PII patterns. Deliberately conservative — this is a backstop, not a
# replacement for a real PII engine (Presidio et al.).
_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE_REDACTED]"),
    # Visa / Mastercard / Amex / Discover. The old pattern only caught Visa,
    # so a Mastercard walked straight through a "PII" guardrail.
    (re.compile(r"\b(?:4\d{12}(?:\d{3})?"
                r"|5[1-5]\d{14}"
                r"|3[47]\d{13}"
                r"|6(?:011|5\d{2})\d{12})\b"), "[CARD_REDACTED]"),
]

_TOXIC_KEYWORDS = {"kill yourself", "i will hurt you", "hate you", "die already"}

# Longest literal a pattern can match. The streaming guard holds back this many
# characters so a pattern straddling two chunks is still caught.
MAX_PATTERN_SPAN = 64

_SCANNING_TYPES = {"pii", "toxicity", "regex", "keyword"}


class GuardrailViolation(Exception):
    def __init__(self, guardrail_name: str, action: str, detail: str):
        self.guardrail_name = guardrail_name
        self.action = action
        self.detail = detail
        super().__init__(detail)


# ── Public API ────────────────────────────────────────────────────────────────

def apply_input_guardrails(
    messages: List[Dict[str, str]],
    guardrails: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    """Scan/redact each message in place. Returns a new message list."""
    active = [g for g in guardrails if g.get("apply_on_input", True)]
    if not active:
        return messages

    out = [dict(m) for m in messages]

    for g in active:
        if g["type"] == "length":
            # Budget on the whole prompt, not any single message.
            total = _messages_to_text(out)
            _length_guardrail(total, g["name"], g.get("action_on_violation", "block"),
                              g.get("config", {}))
            continue

        include_system = bool(g.get("config", {}).get("include_system", False))
        for msg in out:
            if msg.get("role") == "system" and not include_system:
                continue
            content = msg.get("content")
            if not isinstance(content, str):
                continue
            msg["content"] = _apply_to_text(content, g)

    return out


def apply_output_guardrails(text: str, guardrails: List[Dict[str, Any]]) -> str:
    for g in guardrails:
        if not g.get("apply_on_output", True):
            continue
        if g["type"] == "length":
            text = _length_guardrail(text, g["name"], g.get("action_on_violation", "block"),
                                     g.get("config", {}))
            continue
        text = _apply_to_text(text, g)
    return text


def scanning_guardrails(guardrails: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Output guardrails safe to run incrementally over a stream.

    `length` is excluded: you cannot judge a total-length budget from a prefix
    without truncating output that would have been fine.
    """
    return [
        g for g in guardrails
        if g.get("apply_on_output", True) and g["type"] in _SCANNING_TYPES
    ]


# ── Internals ─────────────────────────────────────────────────────────────────

def _apply_to_text(text: str, g: Dict[str, Any]) -> str:
    gtype = g["type"]
    action = g.get("action_on_violation", "block")
    cfg = g.get("config", {})

    if gtype == "pii":
        return _pii_guardrail(text, g["name"], action)
    if gtype == "toxicity":
        return _toxicity_guardrail(text, g["name"], action)
    if gtype == "length":
        return _length_guardrail(text, g["name"], action, cfg)
    if gtype == "regex":
        return _regex_guardrail(text, g["name"], action, cfg)
    if gtype == "keyword":
        return _keyword_guardrail(text, g["name"], action, cfg)
    return text


def _pii_guardrail(text: str, name: str, action: str) -> str:
    if not any(p.search(text) for p, _ in _PII_PATTERNS):
        return text
    if action == "block":
        raise GuardrailViolation(name, action, "PII detected in content")
    if action == "redact":
        for pattern, replacement in _PII_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
    log.warning("guardrail.pii_detected guardrail=%s", name)
    return text


def _toxicity_guardrail(text: str, name: str, action: str) -> str:
    lower = text.lower()
    if not any(kw in lower for kw in _TOXIC_KEYWORDS):
        return text
    if action == "block":
        raise GuardrailViolation(name, action, "Toxic content detected")
    if action == "redact":
        for kw in _TOXIC_KEYWORDS:
            text = re.sub(re.escape(kw), "[REDACTED]", text, flags=re.IGNORECASE)
        return text
    log.warning("guardrail.toxicity_detected guardrail=%s", name)
    return text


def _length_guardrail(text: str, name: str, action: str, cfg: Dict) -> str:
    max_chars = cfg.get("max_chars", 10000)
    if len(text) <= max_chars:
        return text
    if action == "block":
        raise GuardrailViolation(name, action, f"Content exceeds {max_chars} chars")
    return text[:max_chars]


def _regex_guardrail(text: str, name: str, action: str, cfg: Dict) -> str:
    pattern_str = cfg.get("pattern", "")
    if not pattern_str:
        return text
    pattern = re.compile(pattern_str, re.IGNORECASE)
    if not pattern.search(text):
        return text
    if action == "block":
        raise GuardrailViolation(name, action, f"Regex pattern matched: {pattern_str}")
    return pattern.sub(cfg.get("replacement", "[REDACTED]"), text)


def _keyword_guardrail(text: str, name: str, action: str, cfg: Dict) -> str:
    keywords = [k.lower() for k in cfg.get("keywords", [])]
    if not keywords:
        return text
    lower = text.lower()
    if not any(kw in lower for kw in keywords):
        return text
    if action == "block":
        raise GuardrailViolation(name, action, "Blocked keyword detected")
    for kw in keywords:
        text = re.sub(re.escape(kw), "[REDACTED]", text, flags=re.IGNORECASE)
    return text


def _messages_to_text(messages: List[Dict[str, str]]) -> str:
    return " ".join(m.get("content", "") or "" for m in messages)


# ── Stream rules ──────────────────────────────────────────────────────────────
#
# Streaming needs span information, not just text->text substitution, so the
# stream guard can tell whether a match straddles the release boundary. Each
# scanning guardrail is lowered to a list of (pattern, replacement) rules here.

class StreamRule:
    __slots__ = ("name", "action", "pattern", "replacement")

    def __init__(self, name: str, action: str, pattern: "re.Pattern", replacement: str):
        self.name = name
        self.action = action
        self.pattern = pattern
        self.replacement = replacement


def compile_stream_rules(guardrails: List[Dict[str, Any]]) -> List[StreamRule]:
    """Lower output-side scanning guardrails into span-aware regex rules."""
    rules: List[StreamRule] = []

    for g in scanning_guardrails(guardrails):
        name = g["name"]
        action = g.get("action_on_violation", "block")
        cfg = g.get("config", {})
        gtype = g["type"]

        if gtype == "pii":
            for pattern, replacement in _PII_PATTERNS:
                rules.append(StreamRule(name, action, pattern, replacement))
        elif gtype == "toxicity":
            joined = "|".join(re.escape(k) for k in sorted(_TOXIC_KEYWORDS))
            rules.append(StreamRule(name, action, re.compile(joined, re.IGNORECASE), "[REDACTED]"))
        elif gtype == "regex":
            if cfg.get("pattern"):
                rules.append(StreamRule(
                    name, action,
                    re.compile(cfg["pattern"], re.IGNORECASE),
                    cfg.get("replacement", "[REDACTED]"),
                ))
        elif gtype == "keyword":
            words = [k for k in cfg.get("keywords", []) if k]
            if words:
                joined = "|".join(re.escape(k) for k in words)
                rules.append(StreamRule(
                    name, action, re.compile(joined, re.IGNORECASE), "[REDACTED]"
                ))

    return rules
