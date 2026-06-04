"""Guardrail middleware — applies input/output filters to LLM traffic."""
from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Minimal PII patterns for demonstration
_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),          # US SSN
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b4[0-9]{12}(?:[0-9]{3})?\b"), "[CARD_REDACTED]"),    # Visa
]

_TOXIC_KEYWORDS = {"kill yourself", "i will hurt you", "hate you", "die already"}


class GuardrailViolation(Exception):
    def __init__(self, guardrail_name: str, action: str, detail: str):
        self.guardrail_name = guardrail_name
        self.action = action
        self.detail = detail
        super().__init__(detail)


def apply_input_guardrails(text: str, guardrails: List[Dict[str, Any]]) -> str:
    for g in guardrails:
        if not g.get("apply_on_input", True):
            continue
        text = _apply_guardrail(text, g, phase="input")
    return text


def apply_output_guardrails(text: str, guardrails: List[Dict[str, Any]]) -> str:
    for g in guardrails:
        if not g.get("apply_on_output", True):
            continue
        text = _apply_guardrail(text, g, phase="output")
    return text


def _apply_guardrail(text: str, g: Dict[str, Any], phase: str) -> str:
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
    detected = any(p.search(text) for p, _ in _PII_PATTERNS)
    if not detected:
        return text
    if action == "block":
        raise GuardrailViolation(name, action, "PII detected in content")
    if action == "redact":
        for pattern, replacement in _PII_PATTERNS:
            text = pattern.sub(replacement, text)
        return text
    log.warning("guardrail.pii_detected", guardrail=name)
    return text


def _toxicity_guardrail(text: str, name: str, action: str) -> str:
    lower = text.lower()
    if not any(kw in lower for kw in _TOXIC_KEYWORDS):
        return text
    if action == "block":
        raise GuardrailViolation(name, action, "Toxic content detected")
    log.warning("guardrail.toxicity_detected", guardrail=name)
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
    lower = text.lower()
    if not any(kw in lower for kw in keywords):
        return text
    if action == "block":
        raise GuardrailViolation(name, action, "Blocked keyword detected")
    for kw in keywords:
        text = re.sub(re.escape(kw), "[REDACTED]", text, flags=re.IGNORECASE)
    return text
