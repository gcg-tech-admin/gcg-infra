"""
mcp_tools.py — MCP tool registration for Instagram.

Exposes exec()-generated stubs with JSON Schema input validation.
Pattern matches gcg_google_v2.mcp_tools.
"""
import functools
import json
import logging
import time
from typing import Any, Callable

from . import __init__ as api

log = logging.getLogger(__name__)

# ── Action → function mapping ─────────────────────────────────────

_ACTION_FN_MAP: dict[str, Callable] = {
    "instagram.profile.get": api.get_profile,
    "instagram.saved_posts.list": api.list_saved_posts,
    "instagram.saved_posts.get": api.get_saved_post,
}

# ── Input schemas ──────────────────────────────────────────────────

_INPUT_SCHEMAS: dict[str, dict] = {
    "instagram.profile.get": {
        "type": "object",
        "properties": {},
        "required": [],
    },
    "instagram.saved_posts.list": {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max posts to return (1-100, default 25)",
                "minimum": 1,
                "maximum": 100,
            },
        },
        "required": [],
    },
    "instagram.saved_posts.get": {
        "type": "object",
        "properties": {
            "post_id": {
                "type": "string",
                "description": "Instagram post ID (from list_saved_posts)",
            },
        },
        "required": ["post_id"],
    },
}


def _resolve_function(action: str) -> Callable:
    fn = _ACTION_FN_MAP.get(action)
    if fn is None:
        raise KeyError(f"Unknown Instagram action: {action}")
    return fn


def register_all_tools(mcp_server):
    """Register all Instagram tools on a FastMCP server."""
    for action, fn in _ACTION_FN_MAP.items():
        schema = _INPUT_SCHEMAS.get(action, {"type": "object", "properties": {}})
        tool_name = action.replace(".", "_")

        @functools.wraps(fn)
        def _make_tool(_fn=fn, _schema=schema, _action=action):
            def _wrapper(**kwargs):
                # Validate against schema
                from jsonschema import validate, ValidationError
                validate(instance=kwargs, schema=_schema)
                start = time.monotonic()
                try:
                    result = _fn(**kwargs)
                    elapsed = (time.monotonic() - start) * 1000
                    log.info("%s → %dms", _action, int(elapsed))
                    return json.dumps(result, default=str)
                except Exception as e:
                    elapsed = (time.monotonic() - start) * 1000
                    log.error("%s → ERROR (%dms): %s", _action, int(elapsed), e)
                    raise
            return _wrapper

        wrapped = _make_tool()
        wrapped.__name__ = tool_name
        wrapped.__doc__ = schema.get("description", f"Instagram tool: {action}")

        mcp_server.tool(
            input_schema=schema,
            name=tool_name,
            description=schema.get("description", f"Call {action}"),
        )(wrapped)

    log.info("Registered %d Instagram tools", len(_ACTION_FN_MAP))
