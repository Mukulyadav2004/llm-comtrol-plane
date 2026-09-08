"""Incremental output guardrails for streamed responses.

A streamed response cannot be scanned after the fact — by the time you have the
full text you have already shipped it to the client. But scanning each SSE delta
in isolation is also wrong: providers split tokens arbitrarily, so an email
address arrives as ``"al"``, ``"ice@exa"``, ``"mple.com"`` and no individual
chunk matches anything.

StreamGuard closes that gap with a hold-back buffer. Text is released only once
``MAX_PATTERN_SPAN`` further characters have arrived behind it, so any pattern
overlapping the release boundary is still whole and still in the buffer. If a
match does straddle the boundary, the release point is pulled back to the start
of that match rather than splitting it.

    incoming:  "...contact al" | "ice@exa" | "mple.com now"
                                 ^ nothing released yet — tail is held back
    on flush:  "...contact [EMAIL_REDACTED] now"

The cost is latency: the client sees output up to MAX_PATTERN_SPAN characters
behind the provider. That is the honest trade for scanning a stream at all.
"""
from __future__ import annotations

from typing import List, Tuple

from app.middleware.guardrails import (
    MAX_PATTERN_SPAN,
    GuardrailViolation,
    StreamRule,
)


class StreamGuard:
    """Buffers streamed text and releases only guardrail-settled prefixes."""

    def __init__(self, rules: List[StreamRule], hold_back: int = MAX_PATTERN_SPAN):
        self._rules = rules
        self._hold_back = hold_back
        self._buf = ""
        # Full redacted text, accumulated for tracing / cost attribution.
        self.released = ""

    @property
    def active(self) -> bool:
        return bool(self._rules)

    def push(self, delta: str) -> str:
        """Feed one provider delta. Returns text safe to emit (may be empty)."""
        if not self._rules:
            self.released += delta
            return delta

        self._buf += delta
        release_at = len(self._buf) - self._hold_back
        if release_at <= 0:
            return ""
        return self._release(release_at)

    def flush(self) -> str:
        """End of stream — release everything still buffered."""
        if not self._rules:
            return ""
        return self._release(len(self._buf))

    # ── internals ────────────────────────────────────────────────────────────

    def _release(self, release_at: int) -> str:
        matches = self._collect(self._buf)

        # A match crossing the release point must stay whole: pull the boundary
        # back to where it starts rather than emitting half of it.
        for start, end, _rule in matches:
            if start < release_at < end:
                release_at = start

        if release_at <= 0:
            return ""

        settled = [(s, e, r) for (s, e, r) in matches if e <= release_at]
        emitted = self._redact(self._buf[:release_at], settled)
        self._buf = self._buf[release_at:]
        self.released += emitted
        return emitted

    def _collect(self, text: str) -> List[Tuple[int, int, StreamRule]]:
        found: List[Tuple[int, int, StreamRule]] = []
        for rule in self._rules:
            for m in rule.pattern.finditer(text):
                if m.start() == m.end():
                    continue
                found.append((m.start(), m.end(), rule))
        found.sort(key=lambda t: (t[0], -t[1]))
        return found

    def _redact(self, text: str, matches: List[Tuple[int, int, StreamRule]]) -> str:
        if not matches:
            return text

        out: List[str] = []
        cursor = 0
        for start, end, rule in matches:
            if start < cursor:  # overlapping match already handled
                continue
            if rule.action == "block":
                raise GuardrailViolation(
                    rule.name, "block", f"Blocked content in streamed output: {rule.name}"
                )
            out.append(text[cursor:start])
            if rule.action == "redact":
                out.append(rule.replacement)
            else:  # "warn" — leave the text, just don't drop it
                out.append(text[start:end])
            cursor = end

        out.append(text[cursor:])
        return "".join(out)
