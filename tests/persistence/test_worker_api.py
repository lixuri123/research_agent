from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import MemorySaver
from research_agent.persistence.repository import Repository
from research_agent.worker import Worker
from research_agent import api
from research_agent.research import graph as graph
from research_agent.core.schemas import Document, RetrievalResult, SubTask, DraftReport, Citation, Claim, ReviewFeedback
import pytest
import time


def fake_agents(monkeypatch):
    monkeypatch.setattr(graph.PlannerAgent, "plan", lambda self, *a: [SubTask(id="1", description="q", query="q")])
    monkeypatch.setattr(graph.RetrieverAgent, "retrieve", lambda self, *a: [RetrievalResult(documents=[Document(id="p", content="The effect is small.")])])
    monkeypatch.setattr(graph.ReaderAgent, "read_documents", lambda self, docs, topic: {d.id: "The effect is small." for d in docs})
    def write(self, *a, **kw):
        e = kw["evidence"][0]
        return DraftReport(content="The effect is small. [1]", citations=[Citation(index=1, source_id="p", evidence_ids=[e.id])], claims=[Claim(id="c", text="The effect is small.", evidence_ids=[e.id])])
    monkeypatch.setattr(graph.WriterAgent, "write", write)
    monkeypatch.setattr(graph.ReviewerAgent, "review", lambda self, *a, **k: ReviewFeedback(approved=True, score=1, claim_verdicts={"c":"supported"}))


def test_api_submit_is_durable_and_idempotent(tmp_path, monkeypatch):
    repo = Repository("sqlite:///" + str(tmp_path / "tasks.db"))
    monkeypatch.setattr(api, "get_repository", lambda: repo)
    with TestClient(api.app) as client:
        a = client.post("/research", json={"topic":"topic"}, headers={"Idempotency-Key":"key"})
        b = client.post("/research", json={"topic":"topic"}, headers={"Idempotency-Key":"key"})
        assert a.status_code == 200
        assert a.json() == b.json()
        assert client.get("/research/"+a.json()["task_id"]).json()["status"] == "queued"
        assert client.post("/research", json={"topic":"other"}, headers={"Idempotency-Key":"key"}).status_code == 409


def test_list_research_returns_newest_tasks_with_topics(tmp_path, monkeypatch):
    repo = Repository("sqlite:///" + str(tmp_path / "tasks.db"))
    monkeypatch.setattr(api, "get_repository", lambda: repo)
    # Give chronological fixtures distinct timestamps even on coarse clocks.
    from itertools import count
    from types import SimpleNamespace
    from research_agent.persistence import repository as storage
    clock = count(100.0, 0.01)
    monkeypatch.setattr(storage, 'time', SimpleNamespace(time=lambda: next(clock)))
    older = repo.submit("早期课题", session_id="session-a")
    newer = repo.submit("近期课题", session_id="session-b")

    with TestClient(api.app) as client:
        response = client.get("/research", params={"limit": 1, "offset": 0})

    assert response.status_code == 200
    assert response.json() == {
        "tasks": [{
            "task_id": newer["task_id"],
            "run_id": newer["run_id"],
            "status": "queued",
            "topic": "近期课题",
            "session_id": "session-b",
            "current_report_version_id": None,
            "created_at": newer["created_at"],
        }]
    }
    assert older["task_id"] != newer["task_id"]


def test_worker_recreation_resumes_approved_version(tmp_path, monkeypatch):
    fake_agents(monkeypatch)
    repo = Repository("sqlite:///" + str(tmp_path / "tasks.db"))
    saver = graph.get_checkpointer("sqlite:///" + str(tmp_path / "checkpoints.db"))
    record = repo.submit("topic", budget={"require_approval": True})
    Worker(repo, graph.build_graph(saver)).run_once()
    waiting = repo.get_run(record["run_id"])
    assert waiting["status"] == "waiting_approval"
    assert waiting["result"].get("final_report") is None
    version = waiting["current_report_version_id"]
    repo.approve(version, True)
    new_repo = Repository(str(repo.engine.url))
    new_saver = graph.get_checkpointer("sqlite:///" + str(tmp_path / "checkpoints.db"))
    Worker(new_repo, graph.build_graph(new_saver)).run_once()
    finished = new_repo.get_run(record["run_id"])
    assert finished["status"] == "completed"
    assert finished["current_report_version_id"] == version
    assert finished["result"]["final_report"]["content"] == repo.get_report(version)["report"]["content"]


def test_rejected_version_never_published(tmp_path, monkeypatch):
    fake_agents(monkeypatch)
    repo = Repository("sqlite:///" + str(tmp_path / "tasks.db"))
    r = repo.submit("q", budget={"require_approval": True})
    w = Worker(repo, graph.build_graph(MemorySaver()))
    w.run_once()
    waiting = repo.get_run(r["run_id"])
    repo.approve(waiting["current_report_version_id"], False)
    assert not w.run_once()
    assert repo.get_run(r["run_id"])["result"].get("final_report") is None
    assert repo.get_stage(r["run_id"], "verified_conclusion", "v1") is None


def test_restart_after_reader_does_not_repeat_completed_work(tmp_path, monkeypatch):
    fake_agents(monkeypatch)
    repo = Repository("sqlite:///" + str(tmp_path / "tasks.db"))
    url = "sqlite:///" + str(tmp_path / "checkpoints.db")
    r = repo.submit("q")
    calls = []
    original_read = graph.ReaderAgent.read_documents
    def read(self, *args):
        calls.append("read")
        return original_read(self, *args)
    monkeypatch.setattr(graph.ReaderAgent, "read_documents", read)
    def crash(*args, **kwargs):
        raise SystemExit("simulate worker process exit")
    monkeypatch.setattr(graph.ReviewerAgent, "review", crash)
    with pytest.raises(SystemExit):
        Worker(repo, graph.build_graph(graph.get_checkpointer(url))).run_once()
    # Simulate the crashed process's lease expiring without a fragile 100 ms
    # lease or dependence on whether provider settings return copies.
    from sqlalchemy import update
    from research_agent.persistence.repository import runs
    with repo.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id == r['run_id'])
                     .values(lease_expires_at=time.time() - 1))
    monkeypatch.setattr(graph.ReviewerAgent, "review", lambda *a, **k: ReviewFeedback(approved=True, score=1, claim_verdicts={"c":"supported"}))
    Worker(Repository(str(repo.engine.url)), graph.build_graph(graph.get_checkpointer(url))).run_once()
    assert repo.get_run(r["run_id"])["status"] == "completed"
    assert calls == ["read"]
