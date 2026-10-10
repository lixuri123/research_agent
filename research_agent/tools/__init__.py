"""工具注册中心：注册、降级、沙箱（权限分级）与统一调用所有外部工具。"""

from __future__ import annotations

from typing import Callable, Literal, Optional
from contextvars import ContextVar

from research_agent.core.audit import log_tool_call
from research_agent.core.schemas import RetrievalResult
from research_agent.core.observability import traceable
from research_agent.core.budget import current_budget, BudgetExceeded

# 工具函数签名：输入 query 字符串，返回 RetrievalResult
ToolFn = Callable[[str], RetrievalResult]

# 危险等级：safe = 只读操作，sensitive = 写/危险操作（需授权）
DangerLevel = Literal["safe", "sensitive"]

_registry: dict[str, dict] = {}
current_tool_allowlist = ContextVar('tool_allowlist', default=None)


def register_tool(name: str, danger_level: DangerLevel = "safe", description: str = ""):
    def decorator(fn: ToolFn) -> ToolFn:
        _registry[name] = {
            "fn": fn,
            "danger_level": danger_level,
            "description": description,
        }
        return fn

    return decorator


def list_tools() -> list[str]:
    return list(_registry.keys())


def list_tool_meta() -> dict[str, dict]:
    return {
        name: {"danger_level": m["danger_level"], "description": m["description"]}
        for name, m in _registry.items()
    }


def is_sensitive(name: str) -> bool:
    meta = _registry.get(name)
    return bool(meta and meta["danger_level"] == "sensitive")


@traceable(run_type="tool", name="run_tool")
def run_tool(name: str, query: str, authorized: bool = False) -> RetrievalResult:
    """调用工具，带沙箱权限检查与异常降级。

    - sensitive 工具在 authorized=False 时拒绝执行（沙箱拦截）
    - 任何异常被捕获并封装进结果（异常降级）
    - 每次调用记录审计日志
    """
    if current_budget.get():
        current_budget.get().check()
    allowed = current_tool_allowlist.get()
    if allowed is not None and name not in allowed:
        return RetrievalResult(query=query, tool_used=name, error=f'工具 {name} 未授权给当前子任务')
    meta = _registry.get(name)
    if meta is None:
        log_tool_call(name, query, status="failed", detail=f"工具不存在")
        return RetrievalResult(query=query, tool_used=name, error=f"工具 {name} 不存在")

    # 沙箱：敏感工具需授权
    if meta["danger_level"] == "sensitive" and not authorized:
        log_tool_call(name, query, status="denied", detail="敏感工具未授权")
        return RetrievalResult(
            query=query,
            tool_used=name,
            error=f"工具 {name} 为敏感操作，需显式授权后执行",
        )

    try:
        result = meta["fn"](query)
        if current_budget.get():
            current_budget.get().check()
        status = "failed" if getattr(result, "error", None) else "success"
        log_tool_call(name, query, status=status, detail=str(getattr(result, "error", "") or ""))
        return result
    except BudgetExceeded:
        raise
    except Exception as exc:  # 工具异常降级
        log_tool_call(name, query, status="failed", detail=str(exc))
        return RetrievalResult(
            query=query, tool_used=name, error=f"工具 {name} 调用失败: {exc}"
        )
