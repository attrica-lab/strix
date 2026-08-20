"""Tests for the generic MCP client: config contract, namespacing, and filtering."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from agents.mcp import MCPServer, MCPServerStdio, MCPServerStreamableHttp
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

from strix.agents import factory
from strix.tools.mcp import (
    BearerAuth,
    McpConnectionConfig,
    load_user_mcp_configs,
)
from strix.tools.mcp.client import _auth_headers, _build_server, _register_server_tools


if TYPE_CHECKING:
    from pathlib import Path

    from agents.tool import Tool


class FakeMCPServer(MCPServer):
    """A connected MCP server stand-in, so tests never touch the network."""

    def __init__(self, name: str, tools: list[MCPTool]) -> None:
        super().__init__()
        self._name = name
        self._tools = tools
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    @property
    def name(self) -> str:
        return self._name

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def list_tools(
        self,
        run_context: Any = None,
        agent: Any = None,
    ) -> list[MCPTool]:
        return list(self._tools)

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        meta: dict[str, Any] | None = None,
    ) -> CallToolResult:
        self.calls.append((tool_name, arguments))
        return CallToolResult(content=[TextContent(type="text", text=f"routed:{tool_name}")])

    async def list_prompts(self) -> Any:
        raise NotImplementedError

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        raise NotImplementedError


def _mcp_tool(name: str) -> MCPTool:
    return MCPTool(
        name=name,
        description=f"remote tool {name}",
        inputSchema={"type": "object", "properties": {}},
    )


def _config(name: str, allowed_tools: list[str]) -> McpConnectionConfig:
    return McpConnectionConfig(
        name=name,
        url="https://mcp.example.com",
        auth=BearerAuth(token="run-token"),
        allowed_tools=allowed_tools,
    )


@pytest.fixture(autouse=True)
def _reset_registry() -> Any:
    saved = list(factory._EXTRA_TOOLS)
    factory._EXTRA_TOOLS.clear()
    try:
        yield
    finally:
        factory._EXTRA_TOOLS[:] = saved


# --- config contract ---------------------------------------------------------


def test_bearer_config_parses_from_dict() -> None:
    config = McpConnectionConfig.model_validate(
        {
            "name": "files_main",
            "transport": "http",
            "url": "https://mcp.example.com",
            "auth": {"kind": "bearer", "token": "abc"},
            "allowed_tools": ["list_files"],
        }
    )

    assert isinstance(config.auth, BearerAuth)
    assert config.auth.token == "abc"
    assert config.allowed_tools == ["list_files"]


def test_unknown_auth_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        McpConnectionConfig.model_validate(
            {
                "name": "x",
                "url": "https://mcp.example.com",
                "auth": {"kind": "oauth", "token": "abc"},
            }
        )


def test_stdio_config_parses_from_dict() -> None:
    config = McpConnectionConfig.model_validate(
        {
            "name": "local_fs",
            "transport": "stdio",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/srv/data"],
            "env": {"FOO": "bar"},
        }
    )

    assert config.transport == "stdio"
    assert config.command == "npx"
    assert config.args == ["-y", "@modelcontextprotocol/server-filesystem", "/srv/data"]
    assert config.env == {"FOO": "bar"}
    # A local stdio server needs no auth, and omitting allowed_tools means "all".
    assert config.auth is None
    assert config.allowed_tools is None


def test_http_config_without_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        McpConnectionConfig.model_validate(
            {
                "name": "x",
                "transport": "http",
                "auth": {"kind": "bearer", "token": "abc"},
            }
        )


def test_stdio_config_without_command_is_rejected() -> None:
    with pytest.raises(ValidationError):
        McpConnectionConfig.model_validate(
            {
                "name": "x",
                "transport": "stdio",
            }
        )


def test_empty_name_is_rejected() -> None:
    with pytest.raises(ValidationError):
        McpConnectionConfig.model_validate(
            {
                "name": "",
                "url": "https://mcp.example.com",
                "auth": {"kind": "bearer", "token": "abc"},
            }
        )


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        McpConnectionConfig.model_validate(
            {
                "name": "x",
                "url": "https://mcp.example.com",
                "auth": {"kind": "bearer", "token": "abc"},
                "surprise": True,
            }
        )


# --- auth headers ------------------------------------------------------------


def test_bearer_auth_builds_authorization_header() -> None:
    headers = _auth_headers(_config("files_main", []))

    assert headers == {"Authorization": "Bearer run-token"}


# --- namespacing and filtering -----------------------------------------------


def _registered_names() -> list[str]:
    return [tool.name for tool in factory.registered_agent_tools()]


@pytest.mark.asyncio
async def test_tools_are_namespaced_per_connection() -> None:
    server_a = FakeMCPServer("conn_a", [_mcp_tool("describe")])
    server_b = FakeMCPServer("conn_b", [_mcp_tool("describe")])

    await _register_server_tools(_config("conn_a", ["describe"]), server_a)
    await _register_server_tools(_config("conn_b", ["describe"]), server_b)

    # Same remote tool name on two connections does not collide.
    assert _registered_names() == ["conn_a.describe", "conn_b.describe"]


@pytest.mark.asyncio
async def test_disallowed_tool_is_not_registered() -> None:
    server = FakeMCPServer(
        "files_main",
        [_mcp_tool("list_files"), _mcp_tool("search")],
    )

    await _register_server_tools(_config("files_main", ["list_files"]), server)

    names = _registered_names()
    assert "files_main.list_files" in names
    assert "files_main.search" not in names


@pytest.mark.asyncio
async def test_allowed_tools_none_registers_every_listed_tool() -> None:
    server = FakeMCPServer(
        "local_fs",
        [_mcp_tool("read_file"), _mcp_tool("write_file")],
    )
    config = McpConnectionConfig(name="local_fs", url="https://mcp.example.com", allowed_tools=None)

    await _register_server_tools(config, server)

    names = _registered_names()
    assert "local_fs.read_file" in names
    assert "local_fs.write_file" in names


@pytest.mark.asyncio
async def test_allowed_tools_list_restricts_registration() -> None:
    server = FakeMCPServer(
        "local_fs",
        [_mcp_tool("read_file"), _mcp_tool("write_file")],
    )

    await _register_server_tools(_config("local_fs", ["read_file"]), server)

    names = _registered_names()
    assert names == ["local_fs.read_file"]


@pytest.mark.asyncio
async def test_registered_tool_routes_to_its_server_with_the_original_name() -> None:
    server = FakeMCPServer("files_main", [_mcp_tool("list_files")])

    tools: list[Tool] = await _register_server_tools(
        _config("files_main", ["list_files"]), server
    )
    tool = tools[0]

    output = await tool.on_invoke_tool(None, "{}")  # type: ignore[union-attr]

    # The call reaches the right server, addressed by the unprefixed remote name.
    assert server.calls == [("list_files", {})]
    assert output == {"type": "text", "text": "routed:list_files"}


# --- result transform --------------------------------------------------------


@pytest.mark.asyncio
async def test_result_transform_receives_namespaced_name_and_structured_result() -> None:
    server = FakeMCPServer("files_main", [_mcp_tool("list_files")])
    seen: list[tuple[str, Any]] = []

    def transform(name: str, structured: Any) -> Any:
        seen.append((name, structured))
        return {"kept": structured["content"][0]["text"]}

    tools: list[Tool] = await _register_server_tools(
        _config("files_main", ["list_files"]), server, result_transform=transform
    )

    output = await tools[0].on_invoke_tool(None, "{}")  # type: ignore[union-attr]

    # The underlying MCP call still routes by the unprefixed remote name.
    assert server.calls == [("list_files", {})]

    # The transform is called with the namespaced name and the parsed result.
    assert len(seen) == 1
    name, structured = seen[0]
    assert name == "files_main.list_files"
    # A parsed CallToolResult (dict/list), not a pre-serialized string.
    assert structured["content"][0]["text"] == "routed:list_files"
    assert structured["isError"] is False

    # The transform's return value is exactly what the tool yields.
    assert output == {"kept": "routed:list_files"}


@pytest.mark.asyncio
async def test_result_transform_can_rewrite_the_tool_output() -> None:
    server = FakeMCPServer("files_main", [_mcp_tool("list_files")])

    def transform(_name: str, structured: Any) -> Any:
        # Keep only a truncated view of the text field.
        return structured["content"][0]["text"][:6]

    tools: list[Tool] = await _register_server_tools(
        _config("files_main", ["list_files"]), server, result_transform=transform
    )

    output = await tools[0].on_invoke_tool(None, "{}")  # type: ignore[union-attr]

    assert output == "routed"


@pytest.mark.asyncio
async def test_without_result_transform_output_is_unchanged() -> None:
    server = FakeMCPServer("files_main", [_mcp_tool("list_files")])

    tools: list[Tool] = await _register_server_tools(
        _config("files_main", ["list_files"]), server, result_transform=None
    )

    output = await tools[0].on_invoke_tool(None, "{}")  # type: ignore[union-attr]

    # Same shape the SDK produces today: no transform in the path.
    assert server.calls == [("list_files", {})]
    assert output == {"type": "text", "text": "routed:list_files"}


# --- server build branch -----------------------------------------------------


def test_build_server_stdio_branch() -> None:
    config = McpConnectionConfig(
        name="local_fs",
        transport="stdio",
        command="my-server",
        args=["--flag", "value"],
        env={"TOKEN": "x"},
    )

    server = _build_server(config)

    # Built, not connected: no subprocess is launched here.
    assert isinstance(server, MCPServerStdio)
    assert server.name == "local_fs"
    assert server.params.command == "my-server"
    assert server.params.args == ["--flag", "value"]
    assert server.params.env == {"TOKEN": "x"}


def test_build_server_http_branch() -> None:
    server = _build_server(_config("files_main", ["list_files"]))

    assert isinstance(server, MCPServerStreamableHttp)
    assert server.name == "files_main"


# --- loader ------------------------------------------------------------------


def test_loader_parses_stdio_and_http_entries(tmp_path: Path) -> None:
    config_file = tmp_path / "mcp-servers.json"
    config_file.write_text(
        json.dumps(
            [
                {
                    "name": "local_fs",
                    "transport": "stdio",
                    "command": "npx",
                    "args": ["-y", "server-filesystem"],
                },
                {
                    "name": "files_main",
                    "transport": "http",
                    "url": "https://mcp.example.com",
                    "auth": {"kind": "bearer", "token": "abc"},
                    "allowed_tools": ["list_files"],
                },
            ]
        ),
        encoding="utf-8",
    )

    configs = load_user_mcp_configs(config_file)

    assert [c.name for c in configs] == ["local_fs", "files_main"]
    assert configs[0].transport == "stdio"
    assert configs[1].allowed_tools == ["list_files"]


def test_loader_skips_bad_entry_but_keeps_good_ones(tmp_path: Path) -> None:
    config_file = tmp_path / "mcp-servers.json"
    config_file.write_text(
        json.dumps(
            [
                {"name": "broken", "transport": "http"},  # missing url
                {
                    "name": "local_fs",
                    "transport": "stdio",
                    "command": "npx",
                },
            ]
        ),
        encoding="utf-8",
    )

    configs = load_user_mcp_configs(config_file)

    assert [c.name for c in configs] == ["local_fs"]


def test_loader_returns_empty_when_file_absent(tmp_path: Path) -> None:
    assert load_user_mcp_configs(tmp_path / "does-not-exist.json") == []


def test_loader_reads_env_var_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_file = tmp_path / "from-env.json"
    config_file.write_text(
        json.dumps([{"name": "local_fs", "transport": "stdio", "command": "npx"}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("STRIX_MCP_CONFIG", str(config_file))

    configs = load_user_mcp_configs()

    assert [c.name for c in configs] == ["local_fs"]
