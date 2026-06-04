"""Renders Jinja2 templates with live context from the broker."""
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.context.loader import build_full_context

_TEMPLATES_DIR = Path(__file__).parent
_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape([]),  # JSON templates — no HTML escaping
    trim_blocks=True,
    lstrip_blocks=True,
)


def render_gateway_config(version: int) -> Dict[str, Any]:
    ctx = build_full_context()
    tmpl = _env.get_template("gateway_config.json.j2")
    rendered = tmpl.render(
        version=version,
        generated_at=datetime.now(timezone.utc).isoformat(),
        **ctx,
    )
    return json.loads(rendered)


def render_mcp_registry(version: int) -> Dict[str, Any]:
    ctx = build_full_context()
    tmpl = _env.get_template("mcp_registry.json.j2")
    rendered = tmpl.render(
        version=version,
        **ctx,
    )
    return json.loads(rendered)
