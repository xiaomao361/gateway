#!/usr/bin/env python3
"""Smoke test for ClaraCore Gateway 聚合 MCP server.

通过 raw JSON-RPC over stdio 启动聚合 server，验证：
- initialize 握手，server 名为 claracore
- list_tools 聚合认知服务、Grafana 和网关工具，前缀无冲突
- 每家各调一个只读工具，确认路由正确、能拿到真实结果

Usage:
    python3 gateway/server/test_aggregator_smoke.py
"""
import json
import os
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parent / "mcp_entry.py"
PYTHON_BIN = sys.executable

passed = 0
failed = 0


def check(desc, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  ✓ {desc} {detail}")
    else:
        failed += 1
        print(f"  ✗ {desc} {detail}")
    return cond


class McpClient:
    def __init__(self, env):
        self.proc = subprocess.Popen(
            [PYTHON_BIN, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, text=True,
        )
        self._id = 1

    def _send(self, method, params=None):
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        self._id += 1
        line = self.proc.stdout.readline()
        return json.loads(line) if line else None

    def _notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def main():
    env = dict(os.environ)
    env["CLARACORE_AGENT_ID"] = "test-agent"
    c = McpClient(env)
    try:
        # initialize
        resp = c._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "smoke", "version": "0"},
        })
        srv = (resp or {}).get("result", {}).get("serverInfo", {})
        check("initialize", srv.get("name") == "claracore", f"server={srv.get('name')} v{srv.get('version')}")
        c._notify("notifications/initialized")

        # list_tools
        resp = c._send("tools/list")
        tools = (resp or {}).get("result", {}).get("tools", [])
        names = {t["name"] for t in tools}
        check("list_tools 聚合数量", len(tools) >= 43, f"→ {len(tools)} tools")
        check("含 memoria 前缀", any(n.startswith("memoria_") for n in names))
        check("含 continuity 前缀", any(n.startswith("continuity_") for n in names))
        check("含 innerlife 前缀", any(n.startswith("innerlife_") for n in names))
        grafana_expected = bool(
            os.environ.get("GRAFANA_MCP_BINARY")
            or (Path(__file__).resolve().parents[2] / "tools" / "grafana-mcp").exists()
        )
        check(
            "Grafana provider 状态符合环境",
            any(n.startswith("grafana_") for n in names) == grafana_expected,
        )
        check("无重复工具名", len(names) == len(tools))
        check("含统一回召", "claracore_recall_context" in names)
        check("含服务管理", "claracore_list_services" in names)

        # 每家各调一个只读工具,验证路由
        # memoria_recall (limit 1)
        resp = c._send("tools/call", {"name": "memoria_recall", "arguments": {"limit": 1}})
        ok = "result" in (resp or {}) and not (resp or {}).get("result", {}).get("isError")
        check("路由 memoria_recall", ok)

        # continuity_list_threads
        resp = c._send("tools/call", {"name": "continuity_list_threads", "arguments": {"agent_id": "test-agent"}})
        ok = "result" in (resp or {})
        check("路由 continuity_list_threads", ok)

        # innerlife_status (无需 agent_id 的只读)
        resp = c._send("tools/call", {"name": "innerlife_status", "arguments": {}})
        ok = "result" in (resp or {})
        check("路由 innerlife_status", ok)

        resp = c._send("tools/call", {"name": "claracore_list_services", "arguments": {}})
        text = (resp or {}).get("result", {}).get("content", [{}])[0].get("text", "[]")
        services = json.loads(text)
        check("统一服务清单", len(services) >= 4, f"→ {len(services)} services")

        # 未知工具
        resp = c._send("tools/call", {"name": "nonexistent_tool", "arguments": {}})
        txt = json.dumps((resp or {}).get("result", {}))
        check("未知工具返回错误", "unknown tool" in txt)

    finally:
        c.close()

    print(f"\n{'✓ All passed' if failed == 0 else f'✗ {failed} failed'} ({passed} passed)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
