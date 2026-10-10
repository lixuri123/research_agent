"""记忆压缩器：滚动摘要，把短期窗口内容压缩为长期记忆。"""

from __future__ import annotations

import time
import uuid

from research_agent.models.gateway import LLMClient
from research_agent.core.schemas import MemoryItem


class MemoryCompressor:
    """将短期记忆溢出内容用 LLM 总结压缩，沉淀为长期记忆。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()

    def summarize(self, items: list[MemoryItem], topic: str = "") -> MemoryItem:
        """把多条记忆压缩成一条摘要记忆。"""
        if not items:
            raise ValueError("无可压缩的记忆")
        joined = "\n".join(f"- {i.content}" for i in items)
        prompt = f"""你是记忆压缩智能体。请把以下会话片段压缩成一段 150-300 字的精炼摘要，
保留关键信息（主题、结论、重要事实），去除冗余。

主题背景：{topic or "通用"}

片段：
{joined[:6000]}

只输出摘要正文，不要任何前缀。
"""
        try:
            summary = self.llm.invoke(
                [LLMClient.system("你是严谨的记忆压缩专家。"), LLMClient.human(prompt)]
            )
        except Exception:
            # LLM 失败降级：直接拼接截断
            summary = joined[:500]

        # 重要度取源记忆的平均值
        importance = sum(i.importance for i in items) / len(items)
        return MemoryItem(
            id=uuid.uuid4().hex,
            content=summary.strip(),
            memory_type="long_term",
            importance=round(importance, 3),
            created_at=time.time(),
        )

    def consolidate_window(self, items: list[MemoryItem], topic: str = "") -> list[MemoryItem]:
        """把溢出的短期窗口压缩成若干条长期记忆（当前为单条摘要）。"""
        if not items:
            return []
        return [self.summarize(items, topic)]
