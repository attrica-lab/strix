"""Read the open-source user's MCP servers from ``~/.strix/mcp-servers.json``.

An open-source user lists the MCP servers they want the agent to reach in a
small JSON file. Strix reads it at the start of a run, connects to each server,
and registers its tools. The file is optional; without it the run simply gets
no MCP tools.

Parsing is fail-open. A single malformed entry is logged and skipped rather than
raising, so one bad row never blocks the servers that are valid, and a missing
or unreadable file yields an empty list.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import cast

from pydantic import ValidationError

from strix.tools.mcp.config import McpConnectionConfig


logger = logging.getLogger(__name__)


_DEFAULT_PATH: Path = Path.home() / ".strix" / "mcp-servers.json"
_PATH_ENV_VAR = "STRIX_MCP_CONFIG"


def _resolve_path(path: Path | None) -> Path:
    if path is not None:
        return path
    override = os.environ.get(_PATH_ENV_VAR)
    if override:
        return Path(override)
    return _DEFAULT_PATH


def load_user_mcp_configs(path: Path | None = None) -> list[McpConnectionConfig]:
    """Load MCP connection configs from the user's JSON file.

    The path is ``path`` if given, else ``$STRIX_MCP_CONFIG``, else
    ``~/.strix/mcp-servers.json``. The file is a JSON list of server entries.
    A missing file returns ``[]``; an unreadable or non-list file is logged and
    returns ``[]``; individual entries that fail validation are logged and
    skipped.
    """
    source = _resolve_path(path)
    if not source.exists():
        return []

    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.exception("Could not read MCP config at %s; ignoring it", source)
        return []

    if not isinstance(raw, list):
        logger.warning("MCP config at %s is not a JSON list; ignoring it", source)
        return []

    entries = cast("list[object]", raw)
    configs: list[McpConnectionConfig] = []
    for index, entry in enumerate(entries):
        try:
            configs.append(McpConnectionConfig.model_validate(entry))
        except ValidationError as exc:
            logger.warning("Skipping invalid MCP server entry #%d in %s: %s", index, source, exc)

    return configs
