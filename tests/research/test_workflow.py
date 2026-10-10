import pytest
from langgraph.checkpoint.memory import MemorySaver
from research_agent.research import graph as workflow
from research_agent.core.schemas import Document, SubTask, ReviewFeedback, DraftReport, Citation, Claim


@pytest.fixture
def agents(monkeypatch):
    monkeypatch.setattr(workflow, "recall_node", lambda state: {"context": "", "recalled_memories": []})
    monkeypatch.setattr(workflow.PlannerAgent, "plan", lambda self, *args: [SubTask(id="1", description="question", query="q")])
    monkeypatch.setattr(workflow.ReaderAgent, "read_documents", lambda self, *args: {"p1": "A finding"})
    monkeypatch.setattr(workflow.RetrieverAgent, "retrieve", lambda self, task: [])
    monkeypatch.setattr(workflow, "consolidate_node", lambda state: {})
    return monkeypatch


def test_empty_retrieval_never_completed(agents):
    g = workflow.build_graph(MemorySaver())
    result = g.invoke({"topic": "q", "run_id": "empty"}, {"configurable": {"thread_id": "empty"}})
    assert result["status"] == "insufficient_evidence"
    assert result.get("final_report") is None


def test_rejections_stop_at_revision_budget(agents):
    from research_agent.core.schemas import RetrievalResult
    doc = Document(id="p1", content="A finding")
    agents.setattr(workflow.RetrieverAgent, "retrieve", lambda self, task: [RetrievalResult(documents=[doc])])
    agents.setattr(workflow.WriterAgent, "write", lambda self, *args, **kwargs: DraftReport(content="A finding [1]", citations=[Citation(index=1, source_id="p1")]))
    agents.setattr(workflow.ReviewerAgent, "review", lambda self, *args, **kwargs: ReviewFeedback(approved=False, score=0, issues=["fix"], suggestions="fix evidence"))
    g = workflow.build_graph(MemorySaver())
    result = g.invoke({"topic": "q", "run_id": "reject", "max_revisions": 2}, {"configurable": {"thread_id": "reject"}, "recursion_limit": 100})
    assert result["status"] == "partial"
    assert result["iteration"] == 2
    assert result.get("final_report") is None


def test_human_rejection_has_no_final_report(agents):
    from research_agent.core.schemas import RetrievalResult
    doc = Document(id="p1", content="A finding")
    agents.setattr(workflow.RetrieverAgent, "retrieve", lambda self, task: [RetrievalResult(documents=[doc])])
    def write(self, topic, requirements, summaries, documents, **kwargs):
        chunk = kwargs["evidence"][0]
        return DraftReport(content="A finding [1]", citations=[Citation(index=1, source_id="p1", evidence_ids=[chunk.id])], claims=[Claim(id="c1", text="A finding", evidence_ids=[chunk.id])])
    agents.setattr(workflow.WriterAgent, "write", write)
    agents.setattr(workflow.ReviewerAgent, "review", lambda self, *args, **kwargs: ReviewFeedback(approved=True, score=1, claim_verdicts={"c1":"supported"}))
    g = workflow.build_graph(MemorySaver())
    config = {"configurable": {"thread_id": "hitl"}, "recursion_limit": 100}
    g.invoke({"topic": "q", "run_id": "hitl", "require_approval": True}, config)
    from langgraph.types import Command
    result = g.invoke(Command(resume={"approved": False}), config)
    assert result["status"] == "rejected"
    assert result.get("final_report") is None


def test_review_patch_contains_verified_draft(agents):
    def review(self, draft, *args, **kwargs):
        draft.claims[0].verification = "supported"
        return ReviewFeedback(approved=True, score=1, claim_verdicts={"c": "supported"})
    agents.setattr(workflow.ReviewerAgent, "review", review)
    draft = DraftReport(content="fact", claims=[Claim(id="c", text="fact", evidence_ids=["e"])])
    result = workflow.review_node({"topic":"q", "draft":draft})
    assert result["draft"].claims[0].verification == "supported"


def test_retrieval_stops_before_next_expired_subtask(agents):
    from research_agent.core.budget import Budget, BudgetExceeded, budget_scope
    import time
    calls = []
    def retrieve(self, task):
        calls.append(task.id)
        time.sleep(.03)
        return []
    agents.setattr(workflow.RetrieverAgent, "retrieve", retrieve)
    state = {"topic":"q", "plan":[SubTask(id=str(i), description="q", query="q") for i in range(2)]}
    with budget_scope(Budget({"deadline":time.time()+.01})):
        with pytest.raises(BudgetExceeded):
            workflow.retrieve_node(state)
    assert len(calls) <= 1

def test_review_cache_preserves_verified_draft(monkeypatch):
    from research_agent.research.evidence import build_evidence
    chunks = build_evidence([Document(id='p', content='Finding')])
    draft = DraftReport(content='Finding [1]', citations=[Citation(index=1, source_id='p', evidence_ids=[chunks[0].id])], claims=[Claim(id='c', text='Finding', evidence_ids=[chunks[0].id])])
    def review(self, draft, *args, **kwargs):
        draft.claims[0].verification = 'supported'
        return ReviewFeedback(approved=True, score=1, claim_verdicts={'c':'supported'})
    monkeypatch.setattr(workflow.ReviewerAgent, 'review', review)
    class Repo:
        patch = None
        def get_stage(self, *args): return self.patch
        def save_stage(self, *args, **kwargs): self.patch = args[-1]
        def record_event(self, *args, **kwargs): pass
    repo = Repo()
    token = workflow.runtime_repository.set(repo)
    state = {'run_id':'cache', 'topic':'q', 'draft':draft, 'evidence':chunks}
    before = workflow.serialize(state)
    try:
        wrapped = workflow._guard('review', workflow.review_node)
        wrapped(state)
        restored = wrapped(workflow.restore(before))
        assert restored['draft'].claims[0].verification == 'supported'
    finally:
        workflow.runtime_repository.reset(token)


def test_graph_gate_rejects_missing_claim_verdict(monkeypatch):
    draft = DraftReport(content='Finding', claims=[Claim(id='c', text='Finding', evidence_ids=['e'])])
    monkeypatch.setattr(workflow.ReviewerAgent, 'review', lambda *a, **k: ReviewFeedback(approved=True, score=1))
    result = workflow.review_node({'draft':draft, 'topic':'q'})
    assert result['need_revision']


def test_retrieval_stops_at_document_limit(monkeypatch):
    from research_agent.core.schemas import RetrievalResult
    calls = []
    def retrieve(self, task):
        calls.append(task.id)
        return [RetrievalResult(documents=[Document(id=task.id, content='text')])]
    monkeypatch.setattr(workflow.RetrieverAgent, 'retrieve', retrieve)
    workflow.retrieve_node({'plan':[SubTask(id=str(i), description='q', query='q') for i in range(3)], 'budget':{'max_documents':1}})
    assert calls == ['0']


def test_node_discards_output_after_deadline(monkeypatch):
    from research_agent.core.budget import Budget, budget_scope
    import research_agent.core.budget as budgets
    now = [0]
    monkeypatch.setattr(budgets.time, 'time', lambda:now[0])
    def slow(state):
        now[0] = 2
        return {'status':'completed', 'final_report':'unsafe'}
    with budget_scope(Budget({'deadline':1})):
        result = workflow._guard('slow', slow)({'run_id':'r'})
    assert result['status'] == 'insufficient_evidence'
    assert result['final_report'] is None
