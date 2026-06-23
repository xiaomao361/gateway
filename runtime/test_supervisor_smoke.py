#!/usr/bin/env python3
"""Smoke test the supervisor with a disposable local web service."""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

from supervisor import Supervisor


TMP = Path(tempfile.mkdtemp(prefix="claracore-supervisor-"))
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


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.1)
    return False


def config(port: int) -> Path:
    path = TMP / f"services-{port}.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "services": {
                    "test-web": {
                        "description": "temporary test server",
                        "type": "web",
                        "command": [
                            "${CLARACORE_PYTHON}",
                            "-m",
                            "http.server",
                            str(port),
                            "--bind",
                            "127.0.0.1",
                        ],
                        "cwd": str(TMP),
                        "port": port,
                        "health_url": f"http://127.0.0.1:{port}/",
                        "web_url": f"http://127.0.0.1:{port}/",
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def main() -> None:
    port = free_port()
    env_path = TMP / "gateway.env"
    env_path.write_text(
        f"CLARACORE_PYTHON={sys.executable}\n",
        encoding="utf-8",
    )
    supervisor = Supervisor(config(port), TMP / "managed-state", env_path)
    try:
        check(
            "读取私密 Python 配置",
            supervisor.services["test-web"]["command"][0] == sys.executable,
        )
        check("初始状态停止", supervisor.status("test-web")["state"] == "stopped")
        started = supervisor.start("test-web")
        check("服务已启动", started["state"] == "running")
        check(
            "健康检查通过",
            wait_for(lambda: supervisor.status("test-web").get("healthy") is True),
        )
        duplicate = supervisor.start("test-web")
        check("重复启动被拦截", duplicate.get("changed") is False)
        check("状态列表可读", len(supervisor.list_services()) == 1)
        check("日志入口可读", "lines" in supervisor.tail_logs("test-web", 10))
        stopped = supervisor.stop("test-web")
        check("服务已停止", stopped["state"] == "stopped")

        external_port = free_port()
        external = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "http.server",
                str(external_port),
                "--bind",
                "127.0.0.1",
            ],
            cwd=TMP,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            external_supervisor = Supervisor(
                config(external_port), TMP / "external-state", env_path
            )
            check(
                "识别外部启动的服务",
                wait_for(
                    lambda: external_supervisor.status("test-web")["state"]
                    == "external"
                ),
            )
            refused = external_supervisor.start("test-web")
            check("不会重复启动外部服务", refused.get("changed") is False)
            untouched = external_supervisor.stop("test-web")
            check(
                "不会误停外部服务",
                untouched.get("changed") is False and external.poll() is None,
            )
        finally:
            if external.poll() is None:
                os.killpg(external.pid, 15)
                external.wait(timeout=5)
    finally:
        try:
            supervisor.stop("test-web")
        except Exception:
            pass
        shutil.rmtree(TMP, ignore_errors=True)

    print(f"\n{'✓ All passed' if failed == 0 else f'✗ {failed} failed'} ({passed} passed)")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
