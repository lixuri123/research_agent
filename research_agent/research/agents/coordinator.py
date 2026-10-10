"""多 Agent 辩论（Coordinator）：多角色生成 + 相互批判 + 择优融合。

用于高精度决策场景：让多个 Writer 从不同视角生成候选报告，
再由 Reviewer 相互评审，最后融合批判意见重写出最优版本。
"""

from __future__ import annotations

from research_agent.research.agents.reviewer import ReviewerAgent
from research_agent.research.agents.writer import WriterAgent
from research_agent.core.schemas import Document, DraftReport, ReviewFeedback

# 辩论视角
_ANGLES = [
    "严谨学术视角（强调方法学与可验证性）",
    "应用实践视角（强调落地价值与工程可行性）",
    "批判性综述视角（强调局限性与争议点）",
]


class DebateCoordinator:
    """多 Agent 辩论主控：生成候选 → 相互评审 → 择优融合。"""

    def __init__(self) -> None:
        self.writer = WriterAgent()
        self.reviewer = ReviewerAgent()

    def debate(
        self,
        topic: str,
        requirements: str,
        summaries: dict[str, str],
        documents: list[Document],
        n_candidates: int = 2,
        n_rounds: int = 1,
    ) -> tuple[DraftReport, list[ReviewFeedback]]:
        """执行辩论，返回（最终报告，各候选评审反馈）。"""
        n = max(2, min(n_candidates, len(_ANGLES)))

        # 第 1 轮：多视角生成候选
        candidates: list[DraftReport] = []
        for i in range(n):
            angle = _ANGLES[i % len(_ANGLES)]
            draft = self.writer.write(
                topic,
                f"{requirements}\n写作视角：{angle}".strip(),
                summaries,
                documents,
            )
            candidates.append(draft)

        # 第 2 轮：相互评审
        reviews = [self.reviewer.review(d, topic) for d in candidates]

        # 择优
        best_idx = max(range(n), key=lambda i: reviews[i].score)

        # 额外批判轮：融合所有评审意见重写
        criticisms = "；".join(r.suggestions for r in reviews if r.suggestions)
        if criticisms:
            final = self.writer.write(
                topic,
                f"{requirements}\n请融合以下评审意见改进：{criticisms[:1200]}".strip(),
                summaries,
                documents,
            )
        else:
            final = candidates[best_idx]

        return final, reviews
