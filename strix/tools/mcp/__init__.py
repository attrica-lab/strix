"""Generic MCP client: connect MCP servers and expose their tools."""

from __future__ import annotations

from strix.tools.mcp.client import ConnectedMcpServer, connect_mcp_servers
from strix.tools.mcp.config import (
    BearerAuth,
    McpAuth,
    McpConnectionConfig,
)
from strix.tools.mcp.loader import load_user_mcp_configs


__all__ = [
    "BearerAuth",
    "ConnectedMcpServer",
    "McpAuth",
    "McpConnectionConfig",
    "connect_mcp_servers",
    "load_user_mcp_configs",
]
