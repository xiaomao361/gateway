"""Unified recall and recording across ClaraCore cognitive services."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from compressor import compress_context


def _decode_content(content: list) -> Any:
    if not content:
        return None
    text = getattr(content[0], "text", "")
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return text


async def _call(provider, tool: str, arguments: dict) -> dict:
    if provider is None:
        return {"ok": False, "error": "provider unavailable", "data": None}
    try:
        content = await provider.call_tool(tool, arguments)
        data = _decode_content(content)
        if isinstance(data, dict) and data.get("error"):
            return {"ok": False, "error": data["error"], "data": data}
        return {"ok": True, "error": None, "data": data}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "data": None}


async def recall_context(
    providers: dict,
    *,
    agent_id: str,
    query: str = "",
    compress: bool = False,
) -> dict:
    """Fetch memory, current shared line and inner state in one call."""
    memory_task = _call(
        providers.get("memoria"),
        "memoria_recall",
        {"query": query, "limit": 10, "include_content": True},
    )
    threads_task = _call(
        providers.get("continuity"),
        "continuity_list_threads",
        {"agent_id": agent_id, "status": "active", "include_shared": True},
    )
    innerlife_task = _call(
        providers.get("innerlife"),
        "innerlife_briefing",
        {"agent_id": agent_id},
    )
    memory, threads, innerlife = await asyncio.gather(
        memory_task, threads_task, innerlife_task
    )

    continuity = threads
    thread_list = threads.get("data") if threads.get("ok") else None
    if isinstance(thread_list, list) and thread_list:
        thread_id = thread_list[0].get("thread_id")
        if thread_id:
            continuity = await _call(
                providers.get("continuity"),
                "continuity_resume",
                {
                    "agent_id": agent_id,
                    "thread_id": thread_id,
                    "action": "continue",
                },
            )
            continuity["selected_thread_id"] = thread_id

    context = {
        "agent_id": agent_id,
        "query": query,
        "memory": memory,
        "continuity": continuity,
        "innerlife": innerlife,
        "degraded": not all(
            item.get("ok") for item in (memory, continuity, innerlife)
        ),
    }
    return compress_context(context, enabled=compress)


async def record_interaction(
    providers: dict,
    *,
    agent_id: str,
    summary: str,
    memory_fact: str = "",
    tags: str = "",
    source_session: str = "",
    thread_id: str = "",
    topic: str = "",
    next_step: str = "",
    boundary_notes: str = "",
    current_interpretation: str = "",
    user_confirmed: bool = False,
    record_memory: bool = True,
    record_continuity: bool = True,
) -> dict:
    """Write an observable summary and/or update the current shared line."""
    tasks: dict[str, Any] = {}
    if record_memory and memory_fact.strip():
        tasks["memory"] = _call(
            providers.get("memoria"),
            "memoria_store",
            {
                "content": memory_fact.strip(),
                "tags": tags,
                "source": "claracore-gateway",
                "source_agent": agent_id,
                "kind": "fact",
                "authority": "confirmed" if user_confirmed else "reported",
            },
        )
    if record_continuity:
        args = {
            "agent_id": agent_id,
            "last_position": summary,
            "next_step": next_step,
            "source_session": source_session,
            "topic": topic,
            "boundary_notes": boundary_notes,
            "current_interpretation": current_interpretation,
            "user_confirmed": user_confirmed,
            "actor": "claracore-gateway",
        }
        if thread_id:
            args["thread_id"] = thread_id
        tasks["continuity"] = _call(
            providers.get("continuity"), "continuity_capture_thread", args
        )

    names = list(tasks)
    values = await asyncio.gather(*(tasks[name] for name in names))
    results = dict(zip(names, values))
    if record_memory and not memory_fact.strip():
        results["memory"] = {
            "ok": True,
            "skipped": True,
            "error": None,
            "data": None,
            "reason": "no observable memory_fact supplied",
        }
    return {
        "agent_id": agent_id,
        "results": results,
        "ok": bool(results) and all(item.get("ok") for item in results.values()),
    }
