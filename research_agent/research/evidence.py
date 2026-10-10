"""Deterministic provenance and structural validation; semantic review is separate."""
from __future__ import annotations

import hashlib
import re
from research_agent.core.schemas import Document, DraftReport, EvidenceChunk


def build_evidence(documents: list[Document], chunk_chars: int = 3000) -> list[EvidenceChunk]:
    if chunk_chars < 1:
        raise ValueError("chunk_chars must be positive")
    chunks = []
    for doc in documents:
        text = doc.content or doc.abstract
        if not text.strip():
            continue
        scope = "full_text" if doc.content else "abstract"
        digest = hashlib.sha256(text.encode()).hexdigest()
        version = hashlib.sha256(f"{doc.id}:{scope}:{digest}".encode()).hexdigest()
        for start in range(0, len(text), chunk_chars):
            end = min(start + chunk_chars, len(text))
            chunks.append(EvidenceChunk(
                id=f"{version}:{start}:{end}", document_id=doc.id,
                version_id=version, text=text[start:end], start=start, end=end,
                scope=scope, source_url=doc.url,
            ))
    return chunks


def validate_report(report: DraftReport, evidence: list[EvidenceChunk]) -> list[str]:
    errors = []
    by_id = {e.id: e for e in evidence}
    numbers = {int(n) for n in re.findall(r"\[(\d+)\]", report.content)}
    citations = {c.index: c for c in report.citations}
    if len(citations) != len(report.citations):
        errors.append("引用编号重复")
    for n in numbers | set(citations):
        c = citations.get(n)
        if c is None or n < 1:
            errors.append(f"引用 [{n}] 无映射")
            continue
        if not c.evidence_ids or any(i not in by_id or by_id[i].document_id != c.source_id for i in c.evidence_ids):
            errors.append(f"引用 [{n}] 缺少有效原文证据")
    if not numbers:
        errors.append("报告没有正文引用")
    if not report.claims:
        errors.append("报告没有可核验论断")
    seen = set()
    for claim in report.claims:
        if claim.id in seen:
            errors.append(f"论断 {claim.id} 重复")
        seen.add(claim.id)
        if claim.text not in report.content:
            errors.append(f"论断 {claim.id} 未出现在正文")
        if not claim.evidence_ids or any(i not in by_id for i in claim.evidence_ids):
            errors.append(f"论断 {claim.id} 缺少有效原文证据")
        if claim.text and claim.text in report.content:
            end = report.content.index(claim.text) + len(claim.text)
            adjacent = re.match(r"(?:\s*\[\d+\])+", report.content[end:])
            cited = {int(n) for n in re.findall(r"\[(\d+)\]", claim.text + (adjacent.group(0) if adjacent else ""))}
            sources = {by_id[i].document_id for i in claim.evidence_ids if i in by_id}
            displayed = {citations[n].source_id for n in cited if n in citations}
            if not cited or displayed != sources:
                errors.append(f"论断 {claim.id} 的正文引用与原文证据不一致")
    return errors
