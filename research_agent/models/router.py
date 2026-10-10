"""多模型路由：按任务角色路由到不同模型，权衡成本与质量。

- 规划/反思/写作：强模型（推理质量优先）
- 阅读/摘要/检索改写：弱模型（成本优先）
"""

from __future__ import annotations

from research_agent.core.config import get_settings
from research_agent.models.gateway import LLMClient

# 角色 -> 配置字段映射
_ROLE_TO_CONFIG = {
    "planner": "planner_model",
    "reviewer": "reviewer_model",
    "writer": "writer_model",
    "summary": "summary_model",
    "reader": "summary_model",
}


def resolve_model(role: str) -> str:
    """解析某角色应使用的模型名（未配置时回退默认 llm_model）。"""
    s = get_settings()
    field = _ROLE_TO_CONFIG.get(role)
    model = getattr(s, field, "") if field else ""
    return model or s.llm_model


def get_llm_for(role: str) -> LLMClient:
    """返回指定角色对应的 LLM 客户端。"""
    return LLMClient(model=resolve_model(role))


def route_table() -> dict[str, str]:
    """返回当前角色 → 模型 的完整路由表。"""
    return {role: resolve_model(role) for role in _ROLE_TO_CONFIG}
