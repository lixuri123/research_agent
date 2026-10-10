from types import SimpleNamespace as Obj

import pytest

from research_agent.evaluation import runner as evaluation


def state(citations, **extra):
    return {
        "status": "completed",
        "final_report": Obj(content="report", citations=citations, validation_errors=[]),
        "documents": [Obj(id="a", title="Paper A")],
        "evidence": [Obj(id="e1", document_id="a")],
        **extra,
    }


def citation(source="a", evidence=None):
    return Obj(source_id=source, text="", evidence_ids=evidence or ["e1"])


def test_precision_counts_extra_wrong_citations():
    assert evaluation._calc_citation_accuracy(state([citation(), citation("unknown")]), ["a"]) == 0.5


@pytest.mark.parametrize("citations,expected", [([], ["a"]), ([citation()], [])])
def test_unscorable_accuracy_is_null(citations, expected):
    assert evaluation._calc_citation_accuracy(state(citations), expected) is None


def test_title_match_uses_source_metadata_not_citation_text():
    wrong = citation("unknown")
    wrong.text = "Paper A"
    assert evaluation._calc_citation_accuracy(state([citation(), wrong]), ["Paper A"]) == 0.5


def test_validity_counts_unknown_and_wrong_evidence():
    sample = state([citation(), citation("unknown"), citation(evidence=["missing"])])
    assert evaluation._calc_citation_validity(sample) == pytest.approx(1 / 3)
    assert evaluation._calc_citation_validity(state([])) is None


def test_eval_uses_actual_context_flag_and_unique_ids(monkeypatch):
    ids = []
    def run(topic, requirements, session_id):
        ids.append(session_id)
        return state([citation()], context="recalled but unused", context_used=False)
    monkeypatch.setattr(evaluation.graph, "run_research", run)
    results = evaluation.run_eval([evaluation.EvalCase("same", expected_sources=["a"])] * 2)
    assert all(r.completed for r in results)
    assert not any(r.context_injected for r in results)
    assert len(set(ids)) == 2


def test_failure_never_becomes_full_accuracy(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("offline failure")
    monkeypatch.setattr(evaluation.graph, "run_research", fail)
    results = evaluation.run_eval([evaluation.EvalCase("x"), evaluation.EvalCase("y", expected_sources=["a"])])
    assert results[0].citation_accuracy is None
    assert results[1].citation_accuracy == 0
    assert evaluation.compute_metrics(results)["avg_citation_accuracy"] == 0
    assert evaluation.compute_metrics([])["avg_citation_accuracy"] is None


def test_failed_state_with_report_does_not_score_perfectly(monkeypatch):
    monkeypatch.setattr(evaluation.graph, "run_research", lambda *a, **k: state([citation()], status="failed"))
    result = evaluation.run_eval([evaluation.EvalCase("x", expected_sources=["a"])])[0]
    assert not result.completed
    assert result.citation_accuracy == 0


def test_report_serializes_null_scores(monkeypatch, tmp_path):
    dataset = tmp_path / "cases.json"
    dataset.write_text('[{"topic":"x"}]', encoding="utf-8")
    monkeypatch.setattr(evaluation.graph, "run_research", lambda *a, **k: state([]))
    report = evaluation.run_and_report(str(dataset))
    assert report["cases"][0]["citation_accuracy"] is None
