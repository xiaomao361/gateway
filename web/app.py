#!/usr/bin/env python3
"""Local-only web console for the ClaraCore service supervisor."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime"))

from supervisor import Supervisor


app = FastAPI(title="ClaraCore Gateway", version="0.1.0")
STATIC_DIR = Path(__file__).resolve().parent / "static"


class ServiceAction(BaseModel):
    action: str


def supervisor() -> Supervisor:
    return Supervisor()


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/app.js", include_in_schema=False)
async def javascript():
    return FileResponse(STATIC_DIR / "app.js", media_type="text/javascript")


@app.get("/styles.css", include_in_schema=False)
async def stylesheet():
    return FileResponse(STATIC_DIR / "styles.css", media_type="text/css")


@app.get("/api/services")
async def list_services():
    items = await asyncio.to_thread(supervisor().list_services)
    return {"services": items}


@app.post("/api/services/{name}/action")
async def service_action(name: str, request: ServiceAction):
    service_supervisor = supervisor()
    actions = {
        "start": service_supervisor.start,
        "stop": service_supervisor.stop,
        "restart": service_supervisor.restart,
    }
    handler = actions.get(request.action)
    if handler is None:
        raise HTTPException(status_code=400, detail="unsupported action")
    try:
        return await asyncio.to_thread(handler, name)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/actions/{action}")
async def bulk_action(action: str):
    if action not in {"start-web", "stop-managed"}:
        raise HTTPException(status_code=400, detail="unsupported action")
    service_supervisor = supervisor()
    services = await asyncio.to_thread(service_supervisor.list_services)
    results = []
    for item in services:
        try:
            if action == "start-web":
                if item["type"] != "web" or item["state"] != "stopped":
                    continue
                result = await asyncio.to_thread(
                    service_supervisor.start, item["name"]
                )
            else:
                if not item["managed"]:
                    continue
                result = await asyncio.to_thread(
                    service_supervisor.stop, item["name"]
                )
            results.append(result)
        except Exception as exc:
            results.append({"name": item["name"], "error": str(exc)})
    return {"results": results}


@app.get("/api/services/{name}/logs")
async def service_logs(
    name: str,
    lines: int = Query(default=120, ge=1, le=500),
):
    try:
        return await asyncio.to_thread(supervisor().tail_logs, name, lines)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def main() -> None:
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
