"""Intent classifier for semantic routing.

Two-stage approach:
  1. Keyword scan — O(1), no LLM call needed if keywords match with high confidence.
  2. LLM classification — asks Ollama to pick the intent from the registered set.
     Uses the smallest/fastest available model to keep latency low.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

from app.config import settings

log = logging.getLogger(__name__)

_CLASSIFIER_PROMPT = """\
You are a request classifier. Given a user message, output ONLY a JSON object with two keys:
  "intent": one of the allowed intents listed below
  "confidence": float from 0.0 to 1.0

Allowed intents: {intents}

Rules:
- Choose the single best-matching intent.
- If nothing fits well, choose the intent marked as default (priority 0, listed last).
- Output raw JSON only, no markdown, no explanation.

User message:
{message}"""


def _keyword_scan(
    text: str, rules: List[Dict[str, Any]]
) -> Optional[Tuple[str, str, float]]:
    """Fast O(n·k) keyword scan before invoking the LLM.

    Returns (intent, route_name, confidence) if any rule's keyword_hints match,
    or None if no match found.
    """
    lower = text.lower()
    best: Optional[Tuple[int, str, str]] = None  # (priority, intent, route_name)

    for rule in rules:
        hints = [h.lower() for h in rule.get("keyword_hints", [])]
        if not hints:
            continue
        matches = sum(1 for h in hints if h in lower)
        if matches == 0:
            continue
        confidence = min(1.0, matches / len(hints))
        priority = rule.get("priority", 0)
        if best is None or priority > best[0]:
            best = (priority, rule["intent"], rule["route_name"], confidence)

    if best:
        _, intent, route_name, confidence = best
        return intent, route_name, confidence
    return None


async def classify_intent(
    messages: List[Dict[str, str]],
    rules: List[Dict[str, Any]],
    classifier_base_url: str,
    classifier_model: str,
) -> Tuple[str, str, float]:
    """Classify the request intent and return (intent, route_name, confidence).

    Falls back to the highest-priority rule if classification fails.
    """
    if not rules:
        raise ValueError("No semantic routing rules configured")

    user_text = " ".join(
        m["content"] for m in messages if m.get("role") == "user"
    )

    keyword_result = _keyword_scan(user_text, rules)
    if keyword_result and keyword_result[2] >= 0.8:
        intent, route_name, confidence = keyword_result
        log.info(
            "classifier.keyword_match",
            intent=intent,
            route=route_name,
            confidence=confidence,
        )
        return intent, route_name, confidence

    intent_names = [r["intent"] for r in rules]
    prompt = _CLASSIFIER_PROMPT.format(
        intents=", ".join(intent_names),
        message=user_text[:1000],
    )

    try:
        result = await _call_ollama(classifier_base_url, classifier_model, prompt)
        intent = result.get("intent", "").strip()
        confidence = float(result.get("confidence", 0.5))

        matched_rule = next((r for r in rules if r["intent"] == intent), None)
        if matched_rule:
            log.info(
                "classifier.llm_match",
                intent=intent,
                route=matched_rule["route_name"],
                confidence=confidence,
            )
            return intent, matched_rule["route_name"], confidence

    except Exception:
        log.exception("classifier.llm_failed")

    # Fallback: use keyword scan result or highest-priority rule
    if keyword_result:
        return keyword_result

    fallback = max(rules, key=lambda r: r.get("priority", 0))
    log.warning("classifier.fallback", route=fallback["route_name"])
    return fallback["intent"], fallback["route_name"], 0.0


async def _call_ollama(base_url: str, model: str, prompt: str) -> Dict[str, Any]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 64},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{base_url.rstrip('/')}/api/chat", json=payload)
        resp.raise_for_status()
        content = resp.json()["message"]["content"].strip()
        # Strip markdown code fences if model adds them
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)
