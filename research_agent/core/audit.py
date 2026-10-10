"""审计日志：记录谁在何时执行了什么操作，可追溯、可导出。"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from research_agent.core.observability import current_run_id

_records: list[dict[str, Any]] = []
_max_records = 10000


def log(
    event_type: str,
    actor: str,
    action: str,
    detail: str = "",
    status: str = "success",
    **meta,
) -> dict[str, Any]:
    """记录一条审计事件。"""
    record = {
        "id": uuid.uuid4().hex,
        "timestamp": time.time(),
        "trace_id": current_run_id(),
        "event_type": event_type,  # tool_call / node / approval / memory / ...
        "actor": actor,            # planner / retriever / human / ...
        "action": action,          # 具体动作
        "detail": detail[:2000],
        "status": status,          # success / failed / denied
        "meta": meta,
    }
    _records.append(record)
    if len(_records) > _max_records:
        del _records[: len(_records) - _max_records]
    return record


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """返回最近的审计记录（倒序）。"""
    return list(reversed(_records[-limit:]))


def clear() -> None:
    _records.clear()


def export_jsonl(path: str) -> int:
    """导出审计日志到 JSONL 文件，返回条数。"""
    with open(path, "w", encoding="utf-8") as f:
        for r in _records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(_records)


def log_tool_call(tool_name: str, query: str, status: str, detail: str = "", **meta) -> None:
    log("tool_call", actor="retriever", action=f"call:{tool_name}", detail=detail or query, status=status, **meta)


def log_approval(session_id: str, approved: bool, actor: str = "human") -> None:
    log("approval", actor=actor, action="approve" if approved else "reject", detail=session_id, status="success")
