"""评测框架：任务完成率、引用准确率、响应延迟三维度评测。

支持加载真实论文做评测：评测集里每条包含研究主题 + 参考答案关键词 + 期望引用文献。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from research_agent.research import graph as graph


@dataclass
class EvalCase:
    topic: str
    requirements: str = ""
    expected_keywords: list[str] = field(default_factory=list)
    expected_sources: list[str] = field(default_factory=list)  # 期望引用的文献标题/ID


@dataclass
class EvalResult:
    case: EvalCase
    completed: bool
    citation_accuracy: float | None
    latency_ms: float
    report: str = ""
    error: str = ""
    memory_recalled: int = 0  # 召回的记忆片段数
    context_injected: bool = False  # 是否注入了记忆上下文
    citation_validity: float | None = None


def load_dataset(path: str) -> list[EvalCase]:
    """加载评测集（JSON）。支持真实论文评测：从 JSON 读 expected_sources。"""
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    cases = []
    for item in data:
        cases.append(
            EvalCase(
                topic=item["topic"],
                requirements=item.get("requirements", ""),
                expected_keywords=item.get("expected_keywords", []),
                expected_sources=item.get("expected_sources", []),
            )
        )
    return cases


def _completion_rate(results: list[EvalResult]) -> float:
    if not results:
        return 0.0
    return sum(1 for r in results if r.completed) / len(results)


def _citation_accuracy(result: EvalResult) -> float | None:
    return result.citation_accuracy if result.case.expected_sources else None


def _average_optional(values: list[float | None]) -> float | None:
    scored = [value for value in values if value is not None]
    return round(sum(scored) / len(scored), 4) if scored else None


def _avg_latency(results: list[EvalResult]) -> float:
    done = [r.latency_ms for r in results if r.completed]
    if not done:
        return 0.0
    return sum(done) / len(done)


def compute_metrics(results: list[EvalResult]) -> dict:
    return {
        "total": len(results),
        "completion_rate": round(_completion_rate(results), 4),
        "avg_citation_accuracy": _average_optional([_citation_accuracy(r) for r in results]),
        "avg_citation_validity": _average_optional([r.citation_validity for r in results]),
        "citation_accuracy_scored_cases": sum(_citation_accuracy(r) is not None for r in results),
        "citation_validity_scored_cases": sum(r.citation_validity is not None for r in results),
        "avg_latency_ms": round(_avg_latency(results), 1),
        "avg_memory_recalled": round(
            sum(r.memory_recalled for r in results) / len(results), 2
        )
        if results
        else 0.0,
        "context_injection_rate": round(
            sum(1 for r in results if r.context_injected) / len(results), 4
        )
        if results
        else 0.0,
    }


def _field(item, name, default=None):
    return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)


def _calc_citation_accuracy(state: dict, expected_sources: list[str]) -> float | None:
    """Expected-source precision; every submitted citation is in the denominator."""
    draft = state.get("final_report")
    citations = _field(draft, "citations", [])
    expected = set(expected_sources)
    if not citations or not expected:
        return None
    titles = {_field(doc, "id"): _field(doc, "title", "") for doc in state.get("documents", [])}
    hits = sum(
        _field(c, "source_id") in titles
        and (_field(c, "source_id") in expected or titles.get(_field(c, "source_id")) in expected)
        for c in citations
    )
    return hits / len(citations)


def _calc_citation_validity(state: dict) -> float | None:
    """Structural evidence validity, not semantic claim support."""
    citations = _field(state.get("final_report"), "citations", [])
    if not citations:
        return None
    evidence = {
        _field(chunk, "id", _field(chunk, "chunk_id")): _field(chunk, "document_id", _field(chunk, "source_id"))
        for chunk in state.get("evidence", [])
    }
    valid = 0
    for citation in citations:
        ids = _field(citation, "evidence_ids", [])
        source = _field(citation, "source_id")
        if source and ids and all(chunk_id in evidence and evidence[chunk_id] == source for chunk_id in ids):
            valid += 1
    return valid / len(citations)


def run_eval(cases: list[EvalCase]) -> list[EvalResult]:
    """逐个执行评测，记录完成情况、引用准确率、延迟。"""
    results: list[EvalResult] = []
    for case in cases:
        start = time.perf_counter()
        try:
            state = graph.run_research(case.topic, case.requirements, session_id=f"eval-{uuid4().hex}")
            latency = (time.perf_counter() - start) * 1000
            report = ""
            if state.get("final_report"):
                report = _field(state["final_report"], "content", "")
            acc = _calc_citation_accuracy(state, case.expected_sources)
            completed = state.get("status") in {"completed", "done"} and bool(report)
            if not completed and case.expected_sources:
                acc = 0.0
            recalled = len(state.get("recalled_memories", []))
            injected = bool(state.get("context_used", False))
            results.append(
                EvalResult(
                    case=case,
                    completed=completed,
                    citation_accuracy=acc,
                    latency_ms=latency,
                    report=report,
                    memory_recalled=recalled,
                    context_injected=injected,
                    citation_validity=_calc_citation_validity(state) if completed else None,
                )
            )
        except Exception as exc:
            latency = (time.perf_counter() - start) * 1000
            results.append(
                EvalResult(
                    case=case,
                    completed=False,
                    citation_accuracy=0.0 if case.expected_sources else None,
                    latency_ms=latency,
                    error=str(exc),
                )
            )
    return results


def run_and_report(dataset_path: str) -> dict:
    cases = load_dataset(dataset_path)
    if not cases:
        return {"error": f"评测集为空或不存在: {dataset_path}"}
    results = run_eval(cases)
    metrics = compute_metrics(results)
    metrics["cases"] = [
        {
            "topic": r.case.topic,
            "completed": r.completed,
            "citation_accuracy": round(r.citation_accuracy, 4) if r.citation_accuracy is not None else None,
            "citation_validity": round(r.citation_validity, 4) if r.citation_validity is not None else None,
            "latency_ms": round(r.latency_ms, 1),
            "memory_recalled": r.memory_recalled,
            "context_injected": r.context_injected,
            "error": r.error,
        }
        for r in results
    ]
    return metrics
