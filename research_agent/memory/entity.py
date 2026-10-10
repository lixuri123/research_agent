"""实体记忆：从文本抽取实体及事实，按实体聚合存储与检索。

默认使用内存存储（零依赖）；若安装了 neo4j driver 且配置了 NEO4J_URI，
则自动切换到图数据库持久化。
"""

from __future__ import annotations

import json
import os
import uuid

from pydantic import BaseModel, Field

from research_agent.models.gateway import LLMClient, parse_json_block
from research_agent.memory.base import MemoryStore
from research_agent.core.schemas import MemoryItem


class Entity(BaseModel):
    """一个实体及其关联事实。"""

    name: str = Field(description="实体名称")
    entity_type: str = Field(default="concept", description="实体类型 person/org/concept/term")
    facts: list[str] = Field(default_factory=list, description="关于该实体的事实")


class EntityMemory(MemoryStore):
    """实体记忆：抽取 + 聚合 + 检索。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or LLMClient()
        self._entities: dict[str, Entity] = {}  # name -> Entity
        self._neo4j_driver = self._init_neo4j()

    def _init_neo4j(self):
        uri = os.environ.get("NEO4J_URI")
        if not uri:
            return None
        try:
            from neo4j import GraphDatabase  # 可选依赖

            user = os.environ.get("NEO4J_USER", "neo4j")
            pwd = os.environ.get("NEO4J_PASSWORD", "neo4j")
            return GraphDatabase.driver(uri, auth=(user, pwd))
        except Exception:
            return None

    # ---- 抽取 ----
    def extract(self, text: str) -> list[Entity]:
        """用 LLM 从文本抽取实体及事实。"""
        prompt = f"""从以下文本中抽取关键实体（人物/组织/概念/术语），并为每个实体归纳相关事实。

文本：
{text[:6000]}

输出 JSON 数组：
[{{"name": "实体名", "entity_type": "person/org/concept/term", "facts": ["事实1", "事实2"]}}]
"""
        try:
            raw = self.llm.invoke(
                [LLMClient.system("你是实体抽取专家。"), LLMClient.human(prompt)]
            )
        except Exception:
            return []
        data = parse_json_block(raw)
        entities: list[Entity] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and item.get("name"):
                    entities.append(
                        Entity(
                            name=item["name"],
                            entity_type=item.get("entity_type", "concept"),
                            facts=list(item.get("facts", [])),
                        )
                    )
        return entities

    # ---- 写入 ----
    def add(self, items: list[MemoryItem]) -> int:
        n = 0
        for item in items:
            for ent in self.extract(item.content):
                self.upsert(ent)
                n += 1
        return n

    def upsert(self, entity: Entity) -> None:
        """合并实体事实（去重）。"""
        existing = self._entities.get(entity.name)
        if existing is None:
            existing = Entity(name=entity.name, entity_type=entity.entity_type, facts=[])
            self._entities[entity.name] = existing
        seen = set(existing.facts)
        for f in entity.facts:
            if f and f not in seen:
                existing.facts.append(f)
                seen.add(f)
        self._persist(entity.name, existing)

    def _persist(self, name: str, entity: Entity) -> None:
        """Neo4j 持久化（可选）。"""
        driver = self._neo4j_driver
        if driver is None:
            return
        try:
            with driver.session() as session:
                session.run(
                    "MERGE (e:Entity {name: $name}) "
                    "SET e.type = $type, e.facts = $facts",
                    name=name,
                    type=entity.entity_type,
                    facts=entity.facts,
                )
        except Exception:
            pass

    # ---- 查询 ----
    def get(self, name: str) -> Entity | None:
        return self._entities.get(name)

    def search(self, query: str, top_k: int = 5) -> list[MemoryItem]:
        """按实体名或事实内容匹配检索，返回 MemoryItem 形式。"""
        hits: list[Entity] = []
        for name, ent in self._entities.items():
            score = 0
            if query.lower() in name.lower():
                score += 10
            for f in ent.facts:
                if any(w in f for w in query.split()):
                    score += 1
            if score > 0:
                hits.append((score, ent))
        hits.sort(key=lambda x: x[0], reverse=True)
        return [
            MemoryItem(
                id=uuid.uuid4().hex,
                content=f"实体[{ent.name}]（{ent.entity_type}）：" + "；".join(ent.facts),
                memory_type="entity",
                importance=0.6,
            )
            for _, ent in hits[:top_k]
        ]

    def clear(self) -> None:
        self._entities.clear()
