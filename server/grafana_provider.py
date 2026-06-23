#!/usr/bin/env python3
"""Subprocess-based MCP provider for Grafana (Go binary).

Spawns the grafana-mcp binary, holds an MCP client session over stdio,
and proxies tools with a ``grafana_`` prefix so they route cleanly
through the ClaraCore gateway.
"""
from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.types import Tool


_BINARY = Path(
    os.environ.get(
        "GRAFANA_MCP_BINARY",
        str(Path(__file__).resolve().parents[2] / "tools" / "grafana-mcp"),
    )
).expanduser()

# Features we disable by default (keep the read-only / query-oriented ones).
_DEFAULT_DISABLE = [
    "--disable-dashboard",
    "--disable-incident",
    "--disable-oncall",
    "--disable-sift",
]


class GrafanaProvider:
    """Proxies the Grafana MCP Go binary as a gateway provider."""

    def __init__(
        self,
        binary: str | Path = str(_BINARY),
        grafana_url: str | None = None,
        api_key: str | None = None,
        disable_features: list[str] | None = None,
    ):
        self.binary = str(binary)
        self.env = dict(os.environ)
        if grafana_url:
            self.env["GRAFANA_URL"] = grafana_url
        if api_key:
            self.env["GRAFANA_API_KEY"] = api_key
        self.disable_features = disable_features or _DEFAULT_DISABLE

        self._tools: list[Tool] = []

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def _session_scope(self):
        """Open and close the child MCP in the same request task.

        The MCP Python client owns AnyIO cancel scopes that cannot safely be
        entered in one gateway request task and closed or reused in another.
        A short-lived session avoids taking down the whole gateway.
        """
        args = ["-t", "stdio"] + self.disable_features
        params = StdioServerParameters(
            command=self.binary, args=args, env=self.env
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def shutdown(self) -> None:
        self._tools.clear()

    # ------------------------------------------------------------------
    # provider protocol
    # ------------------------------------------------------------------

    async def list_tools(self) -> list[Tool]:
        if self._tools:
            return list(self._tools)
        async with self._session_scope() as session:
            result = await session.list_tools()
        self._tools = [
            Tool(
                name=f"grafana_{tool.name}",
                description=f"[Grafana] {tool.description or ''}",
                inputSchema=tool.inputSchema,
            )
            for tool in result.tools
        ]
        print(
            f"[gateway] grafana provider ready — {len(self._tools)} tools",
            file=sys.stderr,
        )
        return list(self._tools)

    async def call_tool(self, name: str, arguments: dict) -> list:
        original = name[len("grafana_"):]  # strip prefix
        async with self._session_scope() as session:
            result = await session.call_tool(original, arguments or {})
        # CallToolResult.content is already the list of content blocks.
        return list(result.content)
