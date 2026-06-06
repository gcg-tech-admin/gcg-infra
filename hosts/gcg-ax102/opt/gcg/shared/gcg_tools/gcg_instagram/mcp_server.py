"""
mcp_server.py — Instagram STDIO MCP server entry point.

Started by OpenClaw runtime as:
    python -m gcg_instagram.mcp_server

Environment:
    INSTAGRAM_ACCESS_TOKEN  (required) — Meta Graph API long-lived OAuth token

Get a token: https://developers.facebook.com/tools/explorer/
    → Select Instagram Graph API
    → Add scopes: instagram_basic, pages_read_engagement
    → Generate token → Exchange for long-lived token (60 day)
"""
import os
import sys
import logging

_SERVER_DIR = os.path.dirname(os.path.abspath(__file__))
_PARENT = os.path.dirname(_SERVER_DIR)
sys.path.insert(0, _PARENT)
sys.path.insert(0, os.path.dirname(_PARENT))

from mcp.server.fastmcp import FastMCP

from . import mcp_tools

log = logging.getLogger(__name__)


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stderr,
    )

    agent_id = os.environ.get("GCG_MCP_AGENT_ID", "unknown")
    log.info("Instagram MCP server starting for agent=%s", agent_id)

    # Check token
    token = os.environ.get("INSTAGRAM_ACCESS_TOKEN")
    if not token:
        log.critical(
            "INSTAGRAM_ACCESS_TOKEN not set. "
            "Set it in the MCP server env or as an environment variable."
        )
        sys.exit(1)

    log.info("Token loaded (%d chars)", len(token))

    # Build server
    mcp = FastMCP("gcg-instagram-mcp")

    # Register tools
    mcp_tools.register_all_tools(mcp)

    log.info("Instagram MCP server ready — %d tools", 3)
    mcp.run()


if __name__ == "__main__":
    main()
