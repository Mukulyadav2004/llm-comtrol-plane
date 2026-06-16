"""Self-contained utility tools — no external network needed."""
from __future__ import annotations

import ast
import operator
import random
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.tools.base import tool

# ── calculator ────────────────────────────────────────────────────────────────
# A *safe* arithmetic evaluator built on Python's AST. We never call eval(); only
# an explicit whitelist of numeric operators is permitted, so tool arguments can
# never execute arbitrary code.
_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARYOPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("Only numeric literals are allowed")
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        return _ALLOWED_BINOPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand))
    raise ValueError("Unsupported expression")


@tool(
    name="calculator",
    description="Evaluate a basic arithmetic expression (+ - * / // % ** and parentheses) and return the numeric result.",
    input_schema={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. '2 + 2 * 10' or '(240 * 15) / 100'"}
        },
        "required": ["expression"],
    },
    toolset="utility",
    keywords=["calculate", "math", "arithmetic", "sum", "multiply", "percent", "compute", "plus", "minus"],
    tags={"cacheable": "true", "cache_ttl": "300"},
)
def calculator(args: Dict[str, Any]) -> Dict[str, Any]:
    expr = str(args.get("expression", "")).strip()
    if not expr:
        raise ValueError("'expression' is required")
    try:
        tree = ast.parse(expr, mode="eval")
        value = _eval_node(tree)
    except ZeroDivisionError:
        raise ValueError("Division by zero")
    except Exception as exc:
        raise ValueError(f"Could not evaluate expression: {exc}")
    return {"result": value, "expression": expr}


# ── current_datetime ────────────────────────────────────────────────────────────
@tool(
    name="current_datetime",
    description="Return the current date and time (UTC by default, or at a given UTC offset in hours).",
    input_schema={
        "type": "object",
        "properties": {
            "utc_offset_hours": {"type": "number", "description": "Optional UTC offset, e.g. -5 for US Eastern."}
        },
        "required": [],
    },
    toolset="utility",
    keywords=["time", "date", "now", "today", "clock", "current"],
    tags={"cacheable": "false"},
)
def current_datetime(args: Dict[str, Any]) -> Dict[str, Any]:
    offset = float(args.get("utc_offset_hours", 0) or 0)
    tz = timezone(timedelta(hours=offset))
    now = datetime.now(tz)
    return {
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "utc_offset_hours": offset,
    }


# ── unit_convert ────────────────────────────────────────────────────────────────
# Conversions are expressed relative to a canonical base unit per dimension.
_LENGTH = {"m": 1.0, "km": 1000.0, "cm": 0.01, "mi": 1609.344, "ft": 0.3048, "in": 0.0254}
_WEIGHT = {"kg": 1.0, "g": 0.001, "lb": 0.45359237, "oz": 0.0283495231}


@tool(
    name="unit_convert",
    description="Convert a value between units of length (m, km, cm, mi, ft, in), weight (kg, g, lb, oz), or temperature (c, f, k).",
    input_schema={
        "type": "object",
        "properties": {
            "value": {"type": "number"},
            "from_unit": {"type": "string"},
            "to_unit": {"type": "string"},
        },
        "required": ["value", "from_unit", "to_unit"],
    },
    toolset="utility",
    keywords=["convert", "unit", "miles", "kilometers", "celsius", "fahrenheit", "pounds", "kilograms"],
    tags={"cacheable": "true", "cache_ttl": "3600"},
)
def unit_convert(args: Dict[str, Any]) -> Dict[str, Any]:
    value = float(args["value"])
    src = str(args["from_unit"]).strip().lower()
    dst = str(args["to_unit"]).strip().lower()

    def _temp(v: float, frm: str, to: str) -> float:
        # normalise to Celsius, then to target
        c = {"c": v, "f": (v - 32) * 5 / 9, "k": v - 273.15}[frm]
        return {"c": c, "f": c * 9 / 5 + 32, "k": c + 273.15}[to]

    if src in _LENGTH and dst in _LENGTH:
        result = value * _LENGTH[src] / _LENGTH[dst]
    elif src in _WEIGHT and dst in _WEIGHT:
        result = value * _WEIGHT[src] / _WEIGHT[dst]
    elif src in {"c", "f", "k"} and dst in {"c", "f", "k"}:
        result = _temp(value, src, dst)
    else:
        raise ValueError(f"Cannot convert from '{src}' to '{dst}' (unknown or mismatched dimensions)")

    return {"result": round(result, 6), "from": src, "to": dst, "value": value}


# ── random_number ────────────────────────────────────────────────────────────────
@tool(
    name="random_number",
    description="Return a random integer between min and max (inclusive).",
    input_schema={
        "type": "object",
        "properties": {"min": {"type": "integer"}, "max": {"type": "integer"}},
        "required": ["min", "max"],
    },
    toolset="utility",
    keywords=["random", "dice", "roll", "pick", "chance"],
    tags={"cacheable": "false"},
)
def random_number(args: Dict[str, Any]) -> Dict[str, Any]:
    lo, hi = int(args["min"]), int(args["max"])
    if lo > hi:
        lo, hi = hi, lo
    return {"result": random.randint(lo, hi), "min": lo, "max": hi}


# ── text_stats ────────────────────────────────────────────────────────────────
@tool(
    name="text_stats",
    description="Count characters, words and sentences in a piece of text.",
    input_schema={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    toolset="utility",
    keywords=["count", "words", "characters", "length", "statistics", "text"],
    tags={"cacheable": "true", "cache_ttl": "300"},
)
def text_stats(args: Dict[str, Any]) -> Dict[str, Any]:
    text = str(args.get("text", ""))
    words = [w for w in text.split() if w]
    sentences = [s for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
    return {"characters": len(text), "words": len(words), "sentences": len(sentences)}
