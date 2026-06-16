"""Guardrails for tool traffic.

Two sources, applied to every tool call:
  1. Config-driven guardrails distributed from the control plane (same shape as
     the LLM Gateway: pii / toxicity / keyword / regex / length). They run on the
     string values inside the arguments (input) and the tool result (output).
  2. Always-on structural safety the gateway enforces regardless of config:
       - SSRF guard: reject URL arguments pointing at localhost / private ranges.
       - Size guard: reject oversized argument payloads.

`block` raises GuardrailViolation (the caller maps it to HTTP 422); `redact`
rewrites the offending substrings in place.
"""
from __future__ import annotations

import ipaddress
import json
import logging
import re
from typing import Any, Callable, Dict, List
from urllib.parse import urlparse

log = logging.getLogger(__name__)

_MAX_ARGS_BYTES = 50_000

_PII_PATTERNS = [
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN_REDACTED]"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[EMAIL_REDACTED]"),
    (re.compile(r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"), "[PHONE_REDACTED]"),
    (re.compile(r"\b4[0-9]{12}(?:[0-9]{3})?\b"), "[CARD_REDACTED]"),
    (re.compile(r"\b(sk|pk)-[A-Za-z0-9]{16,}\b"), "[SECRET_REDACTED]"),  # API-key-like
]
_TOXIC_KEYWORDS = {"kill yourself", "i will hurt you", "hate you", "die already"}


class GuardrailViolation(Exception):
    def __init__(self, guardrail_name: str, action: str, detail: str):
        self.guardrail_name = guardrail_name
        self.action = action
        self.detail = detail
        super().__init__(detail)


# ── public API ──────────────────────────────────────────────────────────────
def apply_input_guardrails(arguments: Dict[str, Any], guardrails: List[Dict[str, Any]]) -> Dict[str, Any]:
    _check_payload_size(arguments)
    _check_ssrf(arguments)
    active = [g for g in guardrails if g.get("apply_on_input", True)]
    if not active:
        return arguments
    return _map_strings(arguments, lambda s: _apply_all(s, active, phase="input"))


def apply_output_guardrails(result: Any, guardrails: List[Dict[str, Any]]) -> Any:
    active = [g for g in guardrails if g.get("apply_on_output", True)]
    if not active:
        return result
    return _map_strings(result, lambda s: _apply_all(s, active, phase="output"))


# ── structural safety (always on) ────────────────────────────────────────────
def _check_payload_size(arguments: Dict[str, Any]) -> None:
    if len(json.dumps(arguments, default=str)) > _MAX_ARGS_BYTES:
        raise GuardrailViolation("payload_size", "block", "Tool arguments exceed maximum size")


def _check_ssrf(arguments: Dict[str, Any]) -> None:
    def _scan(value: Any) -> None:
        if isinstance(value, str) and re.match(r"^https?://", value, re.IGNORECASE):
            host = urlparse(value).hostname or ""
            if host in ("localhost", "metadata.google.internal") or host.endswith(".local"):
                raise GuardrailViolation("ssrf", "block", f"Blocked internal URL host: {host}")
            try:
                ip = ipaddress.ip_address(host)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    raise GuardrailViolation("ssrf", "block", f"Blocked private IP: {host}")
            except ValueError:
                pass  # not a literal IP — hostname is fine
        elif isinstance(value, dict):
            for v in value.values():
                _scan(v)
        elif isinstance(value, list):
            for v in value:
                _scan(v)

    _scan(arguments)


# ── config-driven guardrails ──────────────────────────────────────────────────
def _apply_all(text: str, guardrails: List[Dict[str, Any]], phase: str) -> str:
    for g in guardrails:
        text = _apply_one(text, g, phase)
    return text


def _apply_one(text: str, g: Dict[str, Any], phase: str) -> str:
    gtype = g.get("type")
    action = g.get("action_on_violation", "block")
    name = g.get("name", gtype or "guardrail")
    cfg = g.get("config", {})

    if gtype == "pii":
        return _scan_patterns(text, _PII_PATTERNS, name, action, "PII detected")
    if gtype == "toxicity":
        lower = text.lower()
        if any(kw in lower for kw in _TOXIC_KEYWORDS):
            if action == "block":
                raise GuardrailViolation(name, action, "Toxic content detected")
            log.warning("mcp_guardrail.toxicity", guardrail=name, phase=phase)
        return text
    if gtype == "length":
        max_chars = cfg.get("max_chars", 10000)
        if len(text) > max_chars:
            if action == "block":
                raise GuardrailViolation(name, action, f"Content exceeds {max_chars} chars")
            return text[:max_chars]
        return text
    if gtype == "regex":
        pattern_str = cfg.get("pattern", "")
        if pattern_str:
            pattern = re.compile(pattern_str, re.IGNORECASE)
            if pattern.search(text):
                if action == "block":
                    raise GuardrailViolation(name, action, f"Regex matched: {pattern_str}")
                return pattern.sub(cfg.get("replacement", "[REDACTED]"), text)
        return text
    if gtype == "keyword":
        keywords = [k.lower() for k in cfg.get("keywords", [])]
        lower = text.lower()
        if any(kw in lower for kw in keywords):
            if action == "block":
                raise GuardrailViolation(name, action, "Blocked keyword detected")
            for kw in keywords:
                text = re.sub(re.escape(kw), "[REDACTED]", text, flags=re.IGNORECASE)
        return text
    return text


def _scan_patterns(text, patterns, name, action, detail) -> str:
    if not any(p.search(text) for p, _ in patterns):
        return text
    if action == "block":
        raise GuardrailViolation(name, action, detail)
    if action == "redact":
        for pattern, replacement in patterns:
            text = pattern.sub(replacement, text)
    else:
        log.warning("mcp_guardrail.detected", guardrail=name)
    return text


def _map_strings(obj: Any, fn: Callable[[str], str]) -> Any:
    """Apply fn to every string leaf, preserving structure."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: _map_strings(v, fn) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_map_strings(v, fn) for v in obj]
    return obj
