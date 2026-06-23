#!/usr/bin/env python3
"""ClaraCore Gateway — 聚合 MCP 入口 (stdio transport)

把 memoria / continuity / innerlife / Grafana 工具收拢成一个 MCP server，
agent 只需挂载这一个 `claracore`，不用再各配三条。

工具名已天然带前缀（memoria_* / continuity_* / innerlife_*），
启动时建 name→provider 路由表，call 时 O(1) 分发。

使用方式:
    # Claude Code .mcp.json:
    # { "mcpServers": { "claracore": {
    #     "command": "/path/to/gateway/run_mcp.sh",
    #     "env": { "CLARACORE_AGENT_ID": "my-agent" }
    # }}}
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cognitive"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "runtime"))

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from providers import load_providers
from assembler import recall_context, record_interaction
from supervisor import Supervisor

server = Server("claracore", version="0.3.0")

# 启动时加载三家 + 建路由表
_PROVIDERS = load_providers()
_ROUTE: dict[str, str] = {}   # tool_name -> provider_name

_GATEWAY_TOOLS = [
    Tool(
        name="claracore_recall_context",
        description="一次读取相关记忆、当前共同线和 Agent 内部状态，返回统一上下文包。",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "query": {"type": "string", "default": ""},
                "compress": {"type": "boolean", "default": False},
            },
            "required": ["agent_id"],
        },
    ),
    Tool(
        name="claracore_get_agent_briefing",
        description="读取 Agent 当前完整简报。等同于不带搜索词的统一回召。",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "compress": {"type": "boolean", "default": False},
            },
            "required": ["agent_id"],
        },
    ),
    Tool(
        name="claracore_record_interaction",
        description="统一记录一次已确认的交互结果，可写入记忆并创建或更新共同线。",
        inputSchema={
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"},
                "summary": {"type": "string"},
                "memory_fact": {
                    "type": "string",
                    "default": "",
                    "description": "可观察、适合长期保存的事实。留空则不写 Memoria。",
                },
                "tags": {"type": "string", "default": ""},
                "source_session": {"type": "string", "default": ""},
                "thread_id": {"type": "string", "default": ""},
                "topic": {"type": "string", "default": ""},
                "next_step": {"type": "string", "default": ""},
                "boundary_notes": {"type": "string", "default": ""},
                "current_interpretation": {"type": "string", "default": ""},
                "user_confirmed": {"type": "boolean", "default": False},
                "record_memory": {"type": "boolean", "default": True},
                "record_continuity": {"type": "boolean", "default": True},
            },
            "required": ["agent_id", "summary"],
        },
    ),
    Tool(
        name="claracore_list_services",
        description="查看所有已登记服务及其运行、端口和健康状态。",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="claracore_start_service",
        description="启动一个已登记服务。不会接管或误停外部启动的进程。",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
    Tool(
        name="claracore_stop_service",
        description="停止由 ClaraCore 网关启动的服务。",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
    Tool(
        name="claracore_restart_service",
        description="重启由 ClaraCore 网关管理的服务。",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    ),
    Tool(
        name="claracore_tail_logs",
        description="读取一个服务最近的日志。",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "lines": {"type": "integer", "default": 100},
            },
            "required": ["name"],
        },
    ),
]


def _json_content(value) -> list[TextContent]:
    return [
        TextContent(
            type="text", text=json.dumps(value, ensure_ascii=False, indent=2)
        )
    ]


async def _build_routes() -> list:
    """聚合三家工具，建立 name→provider 路由，返回去重后的工具列表。"""
    all_tools = []
    seen: set[str] = set()
    for pname, provider in _PROVIDERS.items():
        try:
            tools = await provider.list_tools()
        except Exception as exc:
            print(f"[gateway] {pname}.list_tools 失败: {exc}", file=sys.stderr)
            continue
        for tool in tools:
            if tool.name in seen:
                print(f"[gateway] 工具名冲突,跳过 {pname}.{tool.name}", file=sys.stderr)
                continue
            seen.add(tool.name)
            _ROUTE[tool.name] = pname
            all_tools.append(tool)
    all_tools.extend(_GATEWAY_TOOLS)
    return all_tools


_TOOLS_CACHE: list | None = None


@server.list_tools()
async def handle_list_tools():
    global _TOOLS_CACHE
    if _TOOLS_CACHE is None:
        _TOOLS_CACHE = await _build_routes()
    return _TOOLS_CACHE


@server.call_tool()
async def handle_call_tool(name: str, arguments: dict):
    arguments = arguments or {}
    try:
        if name in {"claracore_recall_context", "claracore_get_agent_briefing"}:
            result = await recall_context(
                _PROVIDERS,
                agent_id=arguments["agent_id"],
                query=arguments.get("query", ""),
                compress=bool(arguments.get("compress", False)),
            )
            return _json_content(result)
        if name == "claracore_record_interaction":
            result = await record_interaction(
                _PROVIDERS,
                agent_id=arguments["agent_id"],
                summary=arguments["summary"],
                memory_fact=arguments.get("memory_fact", ""),
                tags=arguments.get("tags", ""),
                source_session=arguments.get("source_session", ""),
                thread_id=arguments.get("thread_id", ""),
                topic=arguments.get("topic", ""),
                next_step=arguments.get("next_step", ""),
                boundary_notes=arguments.get("boundary_notes", ""),
                current_interpretation=arguments.get("current_interpretation", ""),
                user_confirmed=bool(arguments.get("user_confirmed", False)),
                record_memory=bool(arguments.get("record_memory", True)),
                record_continuity=bool(arguments.get("record_continuity", True)),
            )
            return _json_content(result)
        if (
            name.startswith("claracore_") and name.endswith("service")
        ) or name in {
            "claracore_list_services", "claracore_tail_logs"
        }:
            supervisor = Supervisor()
            if name == "claracore_list_services":
                return _json_content(supervisor.list_services())
            if name == "claracore_start_service":
                return _json_content(supervisor.start(arguments["name"]))
            if name == "claracore_stop_service":
                return _json_content(supervisor.stop(arguments["name"]))
            if name == "claracore_restart_service":
                return _json_content(supervisor.restart(arguments["name"]))
            if name == "claracore_tail_logs":
                return _json_content(
                    supervisor.tail_logs(
                        arguments["name"], int(arguments.get("lines", 100))
                    )
                )
    except Exception as exc:
        return _json_content({"error": f"{type(exc).__name__}: {exc}"})

    if _TOOLS_CACHE is None:
        await handle_list_tools()  # 确保路由表已建
    pname = _ROUTE.get(name)
    if pname is None:
        return [TextContent(type="text", text=f'{{"error": "unknown tool: {name}"}}')]
    provider = _PROVIDERS[pname]
    try:
        return await provider.call_tool(name, arguments or {})
    except Exception as exc:
        return [TextContent(type="text", text=f'{{"error": "{pname}.{name} failed: {exc}"}}')]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
