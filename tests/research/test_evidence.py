from research_agent.core.schemas import Document, Citation, DraftReport, Claim
from research_agent.research.evidence import build_evidence, validate_report


def test_abstract_scope_and_stable_offsets():
    doc = Document(id="p1", title="Paper", abstract="abcdefghij")
    chunks = build_evidence([doc], chunk_chars=4)
    assert [c.text for c in chunks] == ["abcd", "efgh", "ij"]
    assert [c.start for c in chunks] == [0, 4, 8]
    assert all(c.scope == "abstract" for c in chunks)
    assert chunks == build_evidence([doc], chunk_chars=4)


def test_unknown_citations_are_validation_errors():
    chunks = build_evidence([Document(id="p1", content="original evidence")])
    report = DraftReport(content="Unsupported [2]", citations=[Citation(index=2, source_id="invented")])
    errors = validate_report(report, chunks)
    assert errors and any("2" in e for e in errors)


def test_citation_alone_is_not_a_supported_claim():
    chunks = build_evidence([Document(id="p1", content="original evidence")])
    report = DraftReport(content="A finding [1]", citations=[Citation(index=1, source_id="p1")])
    assert validate_report(report, chunks)


def test_displayed_citation_must_match_claim_evidence():
    chunks = build_evidence([Document(id="a", content="A"), Document(id="b", content="B")])
    report = DraftReport(content="Result [1]", citations=[Citation(index=1, source_id="a", evidence_ids=[chunks[0].id])],
                         claims=[Claim(id="c", text="Result", evidence_ids=[chunks[1].id])])
    assert validate_report(report, chunks)

def test_claim_citation_must_match_its_evidence():
    from research_agent.core.schemas import Claim
    chunks = build_evidence([Document(id='a', content='Alpha'), Document(id='b', content='Beta')])
    report = DraftReport(content='Beta [1]', citations=[Citation(index=1, source_id='a', evidence_ids=[chunks[0].id])], claims=[Claim(id='c', text='Beta', evidence_ids=[chunks[1].id])])
    assert validate_report(report, chunks)
    report.citations[0] = Citation(index=1, source_id='b', evidence_ids=[chunks[1].id])
    assert validate_report(report, chunks) == []
