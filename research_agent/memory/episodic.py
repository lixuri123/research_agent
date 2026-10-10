"""过程记忆（情景记忆）：记录历史任务轨迹，支持复盘与相似任务召回。"""

from __future__ import annotations

import time
import uuid

from pydantic import BaseModel, Field

from research_agent.memory.base import MemoryStore
from research_agent.core.schemas import MemoryItem


class Episode(BaseModel):
    """一次任务的轨迹记录。"""

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    topic: str = ""
    outcome: str = ""  # 结果摘要 / 结论
    status: str = "done"
    latency_ms: float = 0.0
    created_at: float = Field(default_factory=time.time)


class EpisodicMemory(MemoryStore):
    """过程记忆：存储任务轨迹，支持按主题召回历史经验。"""

    def __init__(self) -> None:
        self._episodes: list[Episode] = []

    def record(self, topic: str, outcome: str, status: str = "done", latency_ms: float = 0.0) -> Episode:
        ep = Episode(topic=topic, outcome=outcome, status=status, latency_ms=latency_ms)
        self._episodes.append(ep)
        return ep

    def add(self, items: list[MemoryItem]) -> int:
        for it in items:
            self._episodes.append(
                Episode(topic=it.metadata.get("topic", ""), outcome=it.content)
            )
        return len(items)

    def search(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        scored = []
        for ep in self._episodes:
            score = 0
            q = query.lower()
            if q in ep.topic.lower():
                score += 5
            if q in ep.outcome.lower():
                score += 3
            if score > 0:
                scored.append((score, ep))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            MemoryItem(
                id=ep.id,
                content=f"历史任务[{ep.topic}]：{ep.outcome[:400]}",
                memory_type="episodic",
                importance=0.5,
                metadata={"status": ep.status, "latency_ms": ep.latency_ms},
            )
            for _, ep in scored[:top_k]
        ]

    def list_episodes(self, limit: int = 20) -> list[Episode]:
        return list(reversed(self._episodes[-limit:]))

    def clear(self) -> None:
        self._episodes.clear()
