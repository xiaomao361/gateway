"""Small, conservative process supervisor for ClaraCore services."""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

import yaml


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


class Supervisor:
    def __init__(
        self,
        config_path: str | Path | None = None,
        state_dir: str | Path | None = None,
        env_path: str | Path | None = None,
    ):
        runtime_dir = Path(__file__).resolve().parent
        self.config_path = Path(config_path or runtime_dir / "services.yaml")
        self.state_dir = Path(state_dir or runtime_dir / "state")
        self.logs_dir = self.state_dir / "logs"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        local_path = self.config_path.with_name("services.local.yaml")
        if local_path.exists():
            local_config = yaml.safe_load(local_path.read_text(encoding="utf-8")) or {}
            config.setdefault("services", {}).update(
                local_config.get("services", {})
            )
        private_env = _load_env_file(
            Path(env_path)
            if env_path
            else Path.home() / ".claracore" / "gateway" / "gateway.env"
        )
        self.base_env = {**private_env, **os.environ}
        defaults = {
            "CLARACORE_ROOT": str(Path(__file__).resolve().parents[2]),
            "CLARACORE_PYTHON": self.base_env.get(
                "CLARACORE_PYTHON", self.base_env.get("PYTHON", "python3")
            ),
            "CLARACORE_AGENT_ID": self.base_env.get(
                "CLARACORE_AGENT_ID", "default"
            ),
            "HOME": str(Path.home()),
        }
        self.services = self._expand(config.get("services", {}), defaults)

    @classmethod
    def _expand(cls, value, defaults: dict[str, str]):
        if isinstance(value, dict):
            return {key: cls._expand(item, defaults) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._expand(item, defaults) for item in value]
        if isinstance(value, str):
            expanded = value
            for key, replacement in defaults.items():
                expanded = expanded.replace(f"${{{key}}}", replacement)
            return os.path.expanduser(expanded)
        return value

    def _service(self, name: str) -> dict:
        if name not in self.services:
            raise ValueError(f"unknown service: {name}")
        return self.services[name]

    def _pid_path(self, name: str) -> Path:
        return self.state_dir / f"{name}.pid"

    def _read_pid(self, name: str) -> int | None:
        try:
            return int(self._pid_path(name).read_text().strip())
        except (FileNotFoundError, ValueError):
            return None

    @staticmethod
    def _alive(pid: int | None) -> bool:
        if not pid:
            return False
        try:
            waited, _ = os.waitpid(pid, os.WNOHANG)
            if waited == pid:
                return False
        except ChildProcessError:
            pass
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        try:
            state = subprocess.run(
                ["ps", "-o", "stat=", "-p", str(pid)],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            ).stdout.strip()
            return bool(state) and not state.startswith("Z")
        except Exception:
            return True

    @staticmethod
    def _port_open(port: int | None) -> bool:
        if not port:
            return False
        try:
            with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4):
                return True
        except OSError:
            return False

    @staticmethod
    def _healthy(url: str | None) -> bool | None:
        if not url:
            return None
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({})
            )
            with opener.open(url, timeout=1.5) as response:
                return 200 <= response.status < 500
        except Exception:
            return False

    @staticmethod
    def _external_pids(command: list[str], managed_pid: int | None) -> list[int]:
        if not command:
            return []
        fingerprint_parts = [
            part for part in command
            if part.endswith(".py")
            or part.endswith(".sh")
            or part.startswith("hermes_cli.")
        ]
        if not fingerprint_parts:
            generic = {
                "/usr/bin/env", "env", "python", "python3", "node",
                "conda", "run", "-m",
            }
            fingerprint_parts = [part for part in command if part not in generic][-3:]
        try:
            output = subprocess.run(
                ["ps", "-axo", "pid=,command="],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
            ).stdout
        except Exception:
            return []
        matches = []
        for line in output.splitlines():
            line = line.strip()
            if not line:
                continue
            pid_text, _, process_command = line.partition(" ")
            try:
                pid = int(pid_text)
            except ValueError:
                continue
            if pid == managed_pid or pid == os.getpid():
                continue
            if all(part in process_command for part in fingerprint_parts):
                matches.append(pid)
        return matches

    def status(self, name: str) -> dict:
        service = self._service(name)
        pid = self._read_pid(name)
        managed = self._alive(pid)
        if pid and not managed:
            self._pid_path(name).unlink(missing_ok=True)
            pid = None
        command = [str(part) for part in service.get("command", [])]
        external_pids = self._external_pids(command, pid if managed else None)
        port_open = self._port_open(service.get("port"))
        health = self._healthy(service.get("health_url")) if (managed or port_open) else None
        if managed:
            state = "running"
        elif port_open or external_pids:
            state = "external"
        else:
            state = "stopped"
        return {
            "name": name,
            "description": service.get("description", ""),
            "type": service.get("type", "process"),
            "state": state,
            "pid": pid,
            "port": service.get("port"),
            "healthy": health,
            "web_url": service.get("web_url"),
            "managed": managed,
            "external_pids": external_pids,
        }

    def list_services(self) -> list[dict]:
        return [self.status(name) for name in self.services]

    def start(self, name: str) -> dict:
        service = self._service(name)
        current = self.status(name)
        if current["state"] == "running":
            return {**current, "changed": False, "message": "already running"}
        if current["state"] == "external":
            return {
                **current,
                "changed": False,
                "message": "port is already used by a process not started here",
            }

        command = [str(part) for part in service.get("command", [])]
        if not command:
            raise ValueError(f"service has no command: {name}")
        cwd = Path(service.get("cwd", self.config_path.parent)).expanduser()
        if not cwd.exists():
            raise ValueError(f"service cwd does not exist: {cwd}")
        env = dict(self.base_env)
        env.update({str(k): str(v) for k, v in (service.get("env") or {}).items()})
        log_path = self.logs_dir / f"{name}.log"
        log_file = log_path.open("a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                command,
                cwd=str(cwd),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        finally:
            log_file.close()
        self._pid_path(name).write_text(str(proc.pid), encoding="utf-8")
        time.sleep(0.35)
        result = self.status(name)
        if result["state"] != "running":
            raise RuntimeError(f"service exited during startup; check {log_path}")
        return {**result, "changed": True, "log": str(log_path)}

    def stop(self, name: str, timeout: float = 8.0) -> dict:
        current = self.status(name)
        pid = current.get("pid")
        if not current.get("managed") or not pid:
            return {
                **current,
                "changed": False,
                "message": "not managed here; no process was stopped",
            }
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.time() + timeout
        while time.time() < deadline and self._alive(pid):
            time.sleep(0.1)
        if self._alive(pid):
            try:
                os.killpg(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        self._pid_path(name).unlink(missing_ok=True)
        return {**self.status(name), "changed": True}

    def restart(self, name: str) -> dict:
        self.stop(name)
        return self.start(name)

    def tail_logs(self, name: str, lines: int = 100) -> dict:
        self._service(name)
        path = self.logs_dir / f"{name}.log"
        if not path.exists():
            return {"name": name, "log": str(path), "lines": []}
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()
        return {"name": name, "log": str(path), "lines": content[-max(1, min(lines, 500)):]}
