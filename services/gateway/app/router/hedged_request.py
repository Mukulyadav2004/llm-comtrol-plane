"""Hedged request engine — tail-latency killer.

The "hedged requests" pattern (from Google's Tail at Scale, 2013):
  1. Fire the primary request.
  2. If no response arrives within a hedge threshold (e.g. P95 of that route),
     fire a SECOND request to a fallback route simultaneously.
  3. Return whichever completes first. Cancel the other.

This eliminates long-tail latency without the cost of always running two requests —
the hedge fires only when the primary is slower than expected (~5% of the time at P95).

                          ┌─────────────────────────────┐
  client ──────────────── │       hedged_call()          │
                          │                              │
                          │  fire primary ──────────────►│ response A
                          │      │                       │
                          │  wait hedge_ms               │
                          │      │                       │
                          │  if no response yet:         │
                          │  fire secondary ────────────►│ response B
                          │                              │
                          │  return first winner ────────┤
                          └─────────────────────────────┘
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.middleware.latency_tracker import get_percentile, record_latency
from app.router.config_store import get_route

log = logging.getLogger(__name__)

# Hedge fires when primary is slower than P{HEDGE_PERCENTILE} of its history.
_HEDGE_PERCENTILE = 0.90
# Absolute minimum hedge delay — never fire secondary before this many ms.
_MIN_HEDGE_MS = 200
# If no latency history exists, use this as a conservative default.
_DEFAULT_HEDGE_MS = 1000


async def hedged_call(
    primary_route_name: str,
    messages: List[Dict[str, str]],
    call_fn,  # async callable(route_name, messages) -> dict
    hedge_route_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a hedged request.

    Args:
        primary_route_name: Route to try first.
        messages: Chat messages payload.
        call_fn: Async function that takes (route_name, messages) and returns a result dict.
        hedge_route_name: Fallback route for the secondary request.
                         If None, hedging is skipped and we just call the primary.
    """
    if hedge_route_name is None or hedge_route_name == primary_route_name:
        return await call_fn(primary_route_name, messages)

    hedge_ms = _compute_hedge_threshold(primary_route_name)
    log.info(
        "hedge.starting",
        primary=primary_route_name,
        secondary=hedge_route_name,
        hedge_ms=hedge_ms,
    )

    loop = asyncio.get_event_loop()
    primary_task = loop.create_task(
        _timed_call(primary_route_name, messages, call_fn),
        name=f"hedge-primary-{primary_route_name}",
    )

    secondary_task: Optional[asyncio.Task] = None
    winner_result: Optional[Dict[str, Any]] = None
    winner_route: Optional[str] = None

    try:
        # Wait for primary up to hedge threshold
        done, _ = await asyncio.wait({primary_task}, timeout=hedge_ms / 1000)

        if primary_task in done and not primary_task.exception():
            # Primary finished before hedge threshold — fast path, no secondary needed
            duration_ms, result = primary_task.result()
            record_latency(primary_route_name, duration_ms)
            result["_hedged"] = False
            return result

        # Primary is slow — fire secondary in parallel
        log.info("hedge.firing_secondary", secondary=hedge_route_name, delay_ms=hedge_ms)
        secondary_task = loop.create_task(
            _timed_call(hedge_route_name, messages, call_fn),
            name=f"hedge-secondary-{hedge_route_name}",
        )

        # Wait for whichever finishes first
        done, pending = await asyncio.wait(
            {primary_task, secondary_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in done:
            if not task.exception():
                duration_ms, result = task.result()
                route = primary_route_name if task is primary_task else hedge_route_name
                record_latency(route, duration_ms)
                winner_result = result
                winner_route = route
                break

        # Cancel the slower task
        for task in pending:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

        if winner_result is None:
            raise RuntimeError("Both hedged requests failed")

        winner_result["_hedged"] = True
        winner_result["_hedge_winner"] = winner_route
        log.info("hedge.winner", route=winner_route)
        return winner_result

    except Exception:
        # Ensure cleanup on unexpected error
        for task in [primary_task, secondary_task]:
            if task and not task.done():
                task.cancel()
        raise


async def _timed_call(
    route_name: str,
    messages: List[Dict[str, str]],
    call_fn,
) -> tuple[float, Dict[str, Any]]:
    start = time.monotonic()
    result = await call_fn(route_name, messages)
    duration_ms = (time.monotonic() - start) * 1000
    return duration_ms, result


def _compute_hedge_threshold(route_name: str) -> float:
    """Hedge fires when primary exceeds its historical P90 latency."""
    p90 = get_percentile(route_name, _HEDGE_PERCENTILE)
    if p90 is None:
        return _DEFAULT_HEDGE_MS
    return max(_MIN_HEDGE_MS, p90)
