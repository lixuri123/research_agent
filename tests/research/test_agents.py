from research_agent.research.agents.writer import WriterAgent
from research_agent.research.agents.reviewer import ReviewerAgent
from research_agent.models.gateway import parse_json_block
from research_agent.core.schemas import Document, DraftReport, Claim
from research_agent.research.evidence import build_evidence


class FakeLLM:
    def __init__(self, raw):
        self.raw = raw
        self.messages = []

    def invoke(self, messages):
        self.messages = messages
        return self.raw


def test_invalid_number_remains_in_report():
    doc = Document(id="p", content="evidence")
    report = WriterAgent(FakeLLM('{"content":"text [999]", "claims":[]}')).write(
        "q", "", {}, [doc], evidence=build_evidence([doc]))
    assert report.citations[0].index == 999
    assert report.validation_errors


def test_reviewer_cannot_approve_without_claim_verdicts():
    chunks = build_evidence([Document(id="p", content="evidence")])
    draft = DraftReport(content="fact", claims=[Claim(id="c", text="fact", evidence_ids=[chunks[0].id])])
    feedback = ReviewerAgent(FakeLLM('{"approved":true,"score":1}')).review(draft, "q", evidence=chunks)
    assert not feedback.approved


def test_planner_array_json_is_parsed():
    assert parse_json_block('[{"id":"1"},{"id":"2"}]') == [{"id":"1"}, {"id":"2"}]
