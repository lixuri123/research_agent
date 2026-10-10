"""上下文拼接器：把召回的记忆片段组装成 prompt 上下文。"""

from __future__ import annotations

from research_agent.core.schemas import MemoryItem


class ContextAssembler:
    """将短期窗口 + 检索到的长期/实体记忆拼接为一段上下文文本。"""

    def __init__(self, max_chars: int = 4000) -> None:
        self.max_chars = max_chars

    def assemble(
        self,
        query: str,
        memories: list[MemoryItem],
        short_window: list[MemoryItem] | None = None,
    ) -> str:
        """拼接上下文，按重要度排序，超长截断。"""
        window = short_window or []
        # 合并去重
        seen: set[str] = set()
        merged: list[MemoryItem] = []
        for item in window + memories:
            if item.id not in seen:
                seen.add(item.id)
                merged.append(item)
        # 按重要度降序
        merged.sort(key=lambda x: x.importance, reverse=True)

        parts: list[str] = []
        budget = self.max_chars
        for item in merged:
            snippet = item.content
            if len(snippet) > budget:
                snippet = snippet[:budget]
            parts.append(snippet)
            budget -= len(snippet)
            if budget <= 0:
                break

        if not parts:
            return ""

        header = "【相关历史记忆】\n" + "\n---\n".join(parts)
        return header

    def assemble_prompt(
        self,
        query: str,
        memories: list[MemoryItem],
        short_window: list[MemoryItem] | None = None,
        system_prefix: str = "",
    ) -> str:
        """生成最终的完整提示词 = system 前缀 + 记忆上下文 + 当前 query。"""
        context = self.assemble(query, memories, short_window)
        blocks = [b for b in [system_prefix, context, f"当前任务：{query}"] if b]
        return "\n\n".join(blocks)
