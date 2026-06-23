#!/usr/bin/env python3
"""ClaraCore Gateway — Providers (后端适配器)

加载 memoria / continuity / innerlife，并按需代理 Grafana MCP。
只透传已有工具，不重写底层业务逻辑。

边界：Gateway 只做"找谁、怎么组合、怎么暴露"，底层服务继续独立可用。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Awaitable, Callable

# 三家在 services/ 下的固定布局
SERVICES_ROOT = Path(__file__).resolve().parents[2] / "services"

# (模块文件, list_tools 函数名, call_tool 函数名)
_PROVIDER_SPECS = {
    "memoria": (
        SERVICES_ROOT / "memoria" / "server" / "mcp_server.py",
        "handle_list_tools",
        "handle_call_tool",
    ),
    "continuity": (
        SERVICES_ROOT / "continuity" / "server" / "mcp_server.py",
        "handle_list_tools",
        "handle_call_tool",
    ),
    "innerlife": (
        SERVICES_ROOT / "innerlife" / "server" / "mcp_server.py",
        "list_tools",
        "call_tool",
    ),
}


def propagate_agent_id() -> str | None:
    """把 CLARACORE_AGENT_ID 转译成各家所需的环境变量。

    必须在加载 provider 模块之前调用，因为模块顶层 import 时即读取环境。
    各家 agent_id 实际在 call 时读取，所以启动时设好即可。
    """
    agent_id = (os.environ.get("CLARACORE_AGENT_ID") or "").strip()
    if not agent_id:
        return None
    os.environ["CONTINUITY_AGENT_ID"] = agent_id
    os.environ["INNERLIFE_AGENT_ID"] = agent_id
    return agent_id


class Provider:
    """单个后端系统的句柄：缓存其 list_tools / call_tool。"""

    def __init__(self, name: str, module: ModuleType, list_fn: str, call_fn: str):
        self.name = name
        self.module = module
        self._list: Callable[[], Awaitable[list]] = getattr(module, list_fn)
        self._call: Callable[[str, dict], Awaitable[list]] = getattr(module, call_fn)

    async def list_tools(self) -> list:
        return await self._list()

    async def call_tool(self, name: str, arguments: dict) -> list:
        return await self._call(name, arguments)


def load_providers() -> dict[str, Provider]:
    """加载三家模块 + Grafana subprocess，返回 {name: Provider}。

    缺失或加载失败的 provider 会被跳过（降级），不拖垮其他家。
    """
    propagate_agent_id()
    providers: dict[str, Provider] = {}

    # ---- Python in-process providers (memoria / continuity / innerlife) ----
    for name, (path, list_fn, call_fn) in _PROVIDER_SPECS.items():
        if not path.exists():
            print(
                f"[gateway] provider '{name}' 模块不存在: {path}",
                file=sys.stderr,
            )
            continue
        try:
            spec = importlib.util.spec_from_file_location(f"_ccgw_{name}", str(path))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            providers[name] = Provider(name, module, list_fn, call_fn)
        except Exception as exc:
            print(
                f"[gateway] provider '{name}' 加载失败: {exc}",
                file=sys.stderr,
            )

    # ---- Grafana subprocess provider (Go binary) ----
    try:
        from grafana_provider import GrafanaProvider

        grafana_url = os.environ.get("GRAFANA_URL", "")
        grafana_key = os.environ.get("GRAFANA_API_KEY", "")
        grafana = GrafanaProvider(
            grafana_url=grafana_url, api_key=grafana_key
        )
        providers["grafana"] = grafana
    except Exception as exc:
        print(
            f"[gateway] provider 'grafana' 加载失败: {exc}",
            file=sys.stderr,
        )

    return providers
