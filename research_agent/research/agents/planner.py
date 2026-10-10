"""Planner 智能体：任务分解。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from research_agent.models.gateway import LLMClient, parse_json_block
from research_agent.models.router import get_llm_for
from research_agent.core.schemas import SubTask


class PlanOutput(BaseModel):
    """结构化规划结果。"""

    subtasks: list[SubTask] = Field(description="分解出的子任务列表")


class PlannerAgent:
    """将研究主题拆解为可执行的子任务，每个子任务对应一个检索 query。"""

    def __init__(self, llm: LLMClient | None = None) -> None:
        self.llm = llm or get_llm_for("planner")

    def plan(self, topic: str, requirements: str) -> list[SubTask]:
        prompt = f"""你是科研规划智能体（Planner）。

研究主题：{topic}
用户要求：{requirements or "无"}

请把该研究任务分解为 3-5 个子任务，每个子任务：
1. 有明确的 description（要解决什么问题）
2. 有具体的 query（用于检索论文/网页/知识库的自然语言查询）
   学术论文检索使用简短英文关键词（2-6 个词），不要写完整问句或混入工具名称。

请以 JSON 数组形式输出，形如：
[{{"id":"1","description":"...","query":"...","status":"pending"}}]
"""
        raw = self.llm.invoke(
            [LLMClient.system("你是严谨的科研任务规划专家。"), LLMClient.human(prompt)]
        )
        data = parse_json_block(raw)
        subtasks = data.get("subtasks", []) if isinstance(data, dict) else data
        result: list[SubTask] = []
        if isinstance(subtasks, list):
            for i, item in enumerate(subtasks):
                if isinstance(item, dict):
                    result.append(
                        SubTask(
                            id=str(item.get("id", i + 1)),
                            description=item.get("description", ""),
                            query=item.get("query", ""),
                            status="pending",
                        )
                    )
        # 兜底：LLM 输出异常时至少拆一个任务
        if not result:
            result = [SubTask(id="1", description=topic, query=topic, status="pending")]
        return result
