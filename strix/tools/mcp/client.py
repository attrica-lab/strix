"""Connect to MCP servers and expose their tools to the agent.

Given one :class:`McpConnectionConfig` per server, :func:`connect_mcp_servers`
lists each server's tools, keeps the ones on the connection's allowlist (or all
of them when none is set), prefixes each with the connection name so servers do
not collide, and registers them through the agent factory. The factory applies
output bounding, per-call timeouts, and structured errors to every registered
tool, so this layer does not reimplement them.

A server that cannot connect, or a tool set that cannot be registered, is logged
and skipped, so one bad connection never fails the run.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast

from agents.exceptions import ModelBehaviorError
from agents.mcp import (
    MCPServer,
    MCPServerStdio,
    MCPServerStdioParams,
    MCPServerStreamableHttp,
    MCPServerStreamableHttpParams,
    MCPUtil,
    create_static_tool_filter,
)

from strix.agents.factory import register_agent_tools
from strix.tools.mcp.config import BearerAuth, McpConnectionConfig


if TYPE_CHECKING:
    from collections.abc import Callable

    from agents.tool import FunctionTool, Tool
    from mcp.types import Tool as MCPTool

    # Runs on each tool's structured result before it reaches the agent. Called
    # ``result_transform(namespaced_tool_name, structured_result)`` and its return
    # value becomes the tool's output. ``structured_result`` is the parsed
    # ``CallToolResult`` as a dict (not a serialized string), so the transform can
    # project or drop individual fields.
    ResultTransform = Callable[[str, Any], Any]


logger = logging.getLogger(__name__)


def _auth_headers(config: McpConnectionConfig) -> dict[str, str]:
    """Build the per-server request headers from the connection's auth."""
    auth = config.auth
    if auth is None:
        return {}
    if isinstance(auth, BearerAuth):
        return {"Authorization": f"Bearer {auth.token}"}

    # The only other variant is AWS SigV4.
    # TODO: AWS SigV4 transport and auth are UNVERIFIED. Confirm how the target
    # AWS MCP server is reached (stdio vs streamable HTTP) and how it accepts
    # SigV4-signed requests before enabling this branch. Do not fabricate request
    # signing here.
    raise NotImplementedError(
        "AWS SigV4 MCP auth is not verified yet; confirm the server's transport "
        "and request signing before connecting an aws_sigv4 connection."
    )


def _build_server(config: McpConnectionConfig) -> MCPServer:
    """Construct (but do not connect) the SDK server for one connection.

    When ``allowed_tools`` is a list the static filter means the server will not
    even list tools outside it; :func:`_register_server_tools` re-applies the
    same allowlist as the authoritative gate on what gets registered. When it is
    ``None`` no filter is applied and every listed tool is registered.
    """
    tool_filter = (
        create_static_tool_filter(allowed_tool_names=config.allowed_tools)
        if config.allowed_tools is not None
        else None
    )

    if config.transport == "stdio":
        stdio_params: MCPServerStdioParams = {
            "command": cast("str", config.command),
            "args": config.args,
            "env": config.env,
        }
        return MCPServerStdio(
            params=stdio_params,
            name=config.name,
            tool_filter=tool_filter,
            cache_tools_list=True,
        )

    http_params: MCPServerStreamableHttpParams = {
        "url": cast("str", config.url),
        "headers": _auth_headers(config),
    }
    return MCPServerStreamableHttp(
        params=http_params,
        name=config.name,
        tool_filter=tool_filter,
        cache_tools_list=True,
    )


def _build_tool(
    config: McpConnectionConfig,
    server: MCPServer,
    mcp_tool: MCPTool,
    result_transform: ResultTransform | None,
) -> FunctionTool:
    """Build one namespaced FunctionTool from a listed MCP tool.

    Without ``result_transform`` this is exactly the SDK's stock conversion. With
    one, the SDK still builds the tool (so name override, input schema, approval
    policy, error-as-result handling, and tool-origin metadata are unchanged), but
    we route the underlying MCP call through :func:`_install_result_transform` so
    the transform sees the structured result and decides the tool's output.
    """
    namespaced_name = f"{config.name}.{mcp_tool.name}"
    tool = MCPUtil.to_function_tool(
        mcp_tool,
        server,
        convert_schemas_to_strict=False,
        tool_name_override=namespaced_name,
    )
    if result_transform is not None:
        _install_result_transform(tool, server, mcp_tool.name, namespaced_name, result_transform)
    return tool


def _install_result_transform(
    tool: FunctionTool,
    server: MCPServer,
    base_tool_name: str,
    namespaced_name: str,
    result_transform: ResultTransform,
) -> None:
    """Route a tool's MCP call through ``result_transform``, innermost.

    ``MCPUtil.to_function_tool`` serializes the result inside its own invoke, so
    the structured result cannot be intercepted through it. Instead we call
    ``server.call_tool`` ourselves, hand the parsed :class:`CallToolResult` to the
    transform, and return the transform's output as the tool result.

    This runs INSIDE the tool's invoke. The agent factory wraps a registered
    tool's ``on_invoke_tool`` with output bounding, disk spill, and tracing at
    agent-build time, which is OUTSIDE this invoke, so the transform is genuinely
    the innermost step: nothing sees the raw result before the transform does.

    ``to_function_tool`` wraps the real invoke in the SDK's failure-handling
    invoker, which stores the inner coroutine on ``_invoke_tool_impl`` and calls
    it inside its try/except. Swapping that inner impl keeps the SDK's
    error-as-result handling and all tool metadata while inserting the transform.
    If the SDK ever renames that attribute we fail loudly rather than silently
    skip the transform.
    """

    async def _invoke(_ctx: Any, input_json: str) -> Any:
        parsed: Any = json.loads(input_json) if input_json else {}
        if not isinstance(parsed, dict):
            raise ModelBehaviorError(
                f"Invalid JSON input for tool {namespaced_name}: expected a JSON object"
            )
        args = cast("dict[str, Any]", parsed)
        result = await server.call_tool(base_tool_name, args)
        structured_result = result.model_dump(mode="json")
        return result_transform(namespaced_name, structured_result)

    # ``tool.on_invoke_tool`` is the SDK's failure-handling invoker; it is a plain
    # object with the inner coroutine on ``_invoke_tool_impl``, not a function, so
    # treat it as untyped to swap that attribute.
    invoker = cast("Any", tool.on_invoke_tool)
    if not hasattr(invoker, "_invoke_tool_impl"):
        raise RuntimeError(
            "agents SDK FunctionTool invoker shape changed: cannot install the "
            "result transform without risking it being silently skipped."
        )
    invoker._invoke_tool_impl = _invoke


async def _register_server_tools(
    config: McpConnectionConfig,
    server: MCPServer,
    result_transform: ResultTransform | None = None,
) -> list[Tool]:
    """List a connected server's tools, prefix + filter them, and register them.

    ``allowed_tools`` of ``None`` registers every listed tool; a list restricts
    to exactly those names.
    """
    allowed = config.allowed_tools
    mcp_tools = await server.list_tools()

    tools: list[Tool] = [
        _build_tool(config, server, mcp_tool, result_transform)
        for mcp_tool in mcp_tools
        if allowed is None or mcp_tool.name in allowed
    ]

    register_agent_tools(*tools)
    return tools


async def connect_mcp_servers(
    configs: list[McpConnectionConfig],
    result_transform: ResultTransform | None = None,
) -> list[MCPServer]:
    """Connect to each MCP server and register its tools.

    When ``result_transform`` is given, every registered tool routes its result
    through it before the result reaches the agent (see
    :func:`_install_result_transform`). When it is ``None`` the tools behave
    exactly as the SDK builds them.

    Returns the servers that connected, so the caller can clean them up when the
    run ends. Connections that fail are skipped rather than raised.
    """
    connected: list[MCPServer] = []
    for config in configs:
        server = _build_server(config)
        try:
            await server.connect()  # type: ignore[no-untyped-call]
            tools = await _register_server_tools(config, server, result_transform)
        except Exception:
            logger.exception("Skipping MCP connection %r", config.name)
            await server.cleanup()  # type: ignore[no-untyped-call]
            continue

        logger.info("Connected MCP server %r (%d tools)", config.name, len(tools))
        connected.append(server)

    return connected
