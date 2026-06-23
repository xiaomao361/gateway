#!/usr/bin/env python3
"""End-to-end smoke test for unified cognitive tools using temporary data."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SERVER = Path(__file__).resolve().parent / "mcp_entry.py"
PYTHON = Path(sys.executable)
TMP = Path(tempfile.mkdtemp(prefix="claracore-cognitive-"))
passed = 0
failed = 0


def check(label: str, condition: bool, detail: str = "") -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {label} {detail}")
    else:
        failed += 1
        print(f"  ✗ {label} {detail}")


class Client:
    def __init__(self, env: dict[str, str]):
        self.proc = subprocess.Popen(
            [str(PYTHON), str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        self.next_id = 1

    def send(self, method: str, params: dict | None = None) -> dict:
        message = {"jsonrpc": "2.0", "id": self.next_id, "method": method}
        self.next_id += 1
        if params is not None:
            message["params"] = params
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        return json.loads(line) if line else {}

    def notify(self, method: str) -> None:
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    def call(self, name: str, arguments: dict) -> dict:
        response = self.send("tools/call", {"name": name, "arguments": arguments})
        text = response.get("result", {}).get("content", [{}])[0].get("text", "{}")
        return json.loads(text)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def prepare_innerlife(env: dict[str, str]) -> None:
    code = """
from innerlife.config import Settings
from innerlife.storage import Storage
s = Storage(Settings.from_env().db_path)
s.init_db()
s.create_agent({
    "agent_id": "smoke-agent",
    "display_name": "Smoke Agent",
    "host": "test",
    "initial_state": {"recent_focus": "gateway verification"},
})
"""
    subprocess.run(
        [str(PYTHON), "-c", code],
        cwd=ROOT / "services/innerlife",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def prepare_continuity(env: dict[str, str]) -> None:
    subprocess.run(
        [str(PYTHON), str(ROOT / "services/continuity/cli.py"), "init"],
        cwd=ROOT / "services/continuity",
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )


def main() -> None:
    env = dict(os.environ)
    env.update(
        {
            "CLARACORE_AGENT_ID": "smoke-agent",
            "CONTINUITY_AGENT_ID": "smoke-agent",
            "MEMORIA_ROOT": str(TMP / "memoria"),
            "CONTINUITY_ROOT": str(TMP / "continuity"),
            "INNERLIFE_ROOT": str(TMP / "innerlife"),
            "INNERLIFE_LLM_BACKEND": "fake",
        }
    )
    prepare_continuity(env)
    prepare_innerlife(env)
    client = Client(env)
    try:
        init = client.send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "cognitive-smoke", "version": "1"},
            },
        )
        check("网关启动", init.get("result", {}).get("serverInfo", {}).get("name") == "claracore")
        client.notify("notifications/initialized")

        recorded = client.call(
            "claracore_record_interaction",
            {
                "agent_id": "smoke-agent",
                "summary": "统一记录流程已经跑到验证阶段",
                "memory_fact": "2026-06-23 完成 ClaraCore 网关统一记录测试",
                "tags": "claracore,gateway",
                "topic": "网关开发",
                "next_step": "验证统一回召",
                "source_session": "smoke-session",
                "user_confirmed": True,
            },
        )
        check("统一记录成功", recorded.get("ok") is True, json.dumps(recorded, ensure_ascii=False))
        thread = recorded.get("results", {}).get("continuity", {}).get("data", {})
        check("共同线已创建", bool(thread.get("thread_id")), thread.get("thread_id", ""))
        memory = recorded.get("results", {}).get("memory", {}).get("data", {})
        check("长期事实已保存", bool(memory.get("id")), memory.get("id", ""))

        recalled = client.call(
            "claracore_recall_context",
            {"agent_id": "smoke-agent", "query": "ClaraCore 网关"},
        )
        check("统一回召未降级", recalled.get("degraded") is False)
        check(
            "回召包含长期事实",
            bool(recalled.get("memory", {}).get("data")),
        )
        check(
            "回召包含共同线",
            recalled.get("continuity", {}).get("selected_thread_id") == thread.get("thread_id"),
        )
        check(
            "回召包含内部状态",
            recalled.get("innerlife", {}).get("data", {}).get("agent_id") == "smoke-agent",
        )

        skipped = client.call(
            "claracore_record_interaction",
            {
                "agent_id": "smoke-agent",
                "summary": "只更新共同线，不应写长期记忆",
                "thread_id": thread.get("thread_id"),
            },
        )
        check(
            "无事实时不会误写长期记忆",
            skipped.get("results", {}).get("memory", {}).get("skipped") is True,
        )
    finally:
        client.close()
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{'✓ All passed' if failed == 0 else f'✗ {failed} failed'} ({passed} passed)")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
