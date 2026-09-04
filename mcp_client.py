"""mcp_client.py - discover and call tools from configured MCP servers.

Reads ``mcp_servers.json`` and, for each server, opens a short-lived stdio
session to list tools (cached) or call a tool. Async MCP calls are run in a
fresh event loop inside a worker thread so they work inside FastAPI's sync
threadpool (and on Windows, where subprocess needs the Proactor loop).

The tool layer (:mod:`tools`) uses this to bind workflow steps to MCP-provided
capabilities, falling back to built-in tools when nothing matches.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import sys
from pathlib import Path
from typing import Any, Optional

BASE_DIR = Path(__file__).parent
CONFIG = BASE_DIR / "mcp_servers.json"

_discovery_cache: Optional[list[dict[str, Any]]] = None


def _load_servers() -> dict[str, dict[str, Any]]:
    if not CONFIG.exists():
        return {}
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8")).get("servers", {})
    except Exception:
        return {}


def _server_params(spec: dict[str, Any]):
    from mcp import StdioServerParameters

    command = spec.get("command") or sys.executable
    if command in ("python", "python.exe"):
        command = sys.executable  # use this venv's interpreter
    resolved: list[str] = []
    for arg in spec.get("args", []):
        candidate = BASE_DIR / arg
        resolved.append(str(candidate) if candidate.exists() else arg)
    return StdioServerParameters(
        command=command, args=resolved, env=spec.get("env") or None
    )


async def _adiscover() -> list[dict[str, Any]]:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    found: list[dict[str, Any]] = []
    for name, spec in _load_servers().items():
        try:
            async with stdio_client(_server_params(spec)) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listing = await session.list_tools()
                    for tool in listing.tools:
                        found.append(
                            {
                                "server": name,
                                "name": tool.name,
                                "description": tool.description or "",
                                "input_schema": getattr(tool, "inputSchema", None),
                            }
                        )
        except Exception as exc:  # server unavailable -> report, don't crash
            found.append(
                {"server": name, "name": None, "error": f"{type(exc).__name__}: {exc}"[:160]}
            )
    return found


async def _acall(server: str, tool: str, args: dict[str, Any]) -> str:
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    spec = _load_servers().get(server)
    if spec is None:
        raise RuntimeError(f"unknown MCP server '{server}'")
    async with stdio_client(_server_params(spec)) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments=args)
            parts = [getattr(c, "text", "") or "" for c in result.content]
            return " ".join(p for p in parts if p).strip() or "[mcp] (no text)"


def _run_async(coro) -> Any:
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result(timeout=60)


def discover_tools(refresh: bool = False) -> list[dict[str, Any]]:
    """Return the list of tools advertised by all configured MCP servers."""
    global _discovery_cache
    if _discovery_cache is None or refresh:
        try:
            _discovery_cache = _run_async(_adiscover())
        except Exception as exc:
            _discovery_cache = [{"server": "?", "name": None, "error": str(exc)[:160]}]
    return _discovery_cache


def call_tool(server: str, tool: str, args: dict[str, Any]) -> str:
    """Invoke an MCP tool and return its text result."""
    return _run_async(_acall(server, tool, args))


def available_tools() -> list[dict[str, Any]]:
    """Discovered tools that resolved successfully (no error entries)."""
    return [t for t in discover_tools() if t.get("name")]


def is_available() -> bool:
    try:
        import mcp  # noqa: F401
    except Exception:
        return False
    return bool(_load_servers())
