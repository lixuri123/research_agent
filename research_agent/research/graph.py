"""Evidence-aware, bounded research workflow with durable stage artifacts."""
from __future__ import annotations
import hashlib
import json
import time
import uuid
from contextvars import ContextVar
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt
from research_agent.research.agents import PlannerAgent, ReaderAgent, RetrieverAgent, ReviewerAgent, WriterAgent
from research_agent.core.budget import Budget, BudgetExceeded, budget_scope, current_budget
from research_agent.persistence.checkpoint import get_checkpointer
from research_agent.core.config import get_settings
from research_agent.research.evidence import build_evidence, validate_report
from research_agent.core.schemas import Document, DraftReport, EvidenceChunk, Finding, MemoryItem, ResearchState, ReviewFeedback, RetrievalResult, SubTask

runtime_repository = ContextVar('runtime_repository', default=None)
runtime_worker_id = ContextVar('runtime_worker_id', default=None)
runtime_lease_check = ContextVar('runtime_lease_check', default=lambda: None)
runtime_document_accept = ContextVar('runtime_document_accept', default=lambda docs: docs)


def serialize(value):
    if hasattr(value, 'model_dump'):
        return serialize(value.model_dump())
    if isinstance(value, dict):
        return {k: serialize(v) for k, v in value.items() if k != '__interrupt__'}
    if isinstance(value, (list, tuple)):
        return [serialize(v) for v in value]
    return value


def restore(value):
    value = dict(value)
    lists = {'documents': Document, 'evidence': EvidenceChunk, 'findings': Finding,
             'plan': SubTask, 'retrieval_results': RetrievalResult, 'recalled_memories': MemoryItem}
    for key, cls in lists.items():
        if key in value:
            value[key] = [cls.model_validate(v) for v in value[key]]
    for key, cls in {'draft': DraftReport, 'final_report': DraftReport, 'review': ReviewFeedback}.items():
        if value.get(key) is not None:
            value[key] = cls.model_validate(value[key])
    return value


def _guard(name, fn):
    def node(state):
        runtime_lease_check.get()()
        budget = current_budget.get()
        try:
            if budget:
                budget.check()
            repo = runtime_repository.get()
            # Immutable inputs identify cached stage outputs; revisions are distinct.
            key = hashlib.sha256(json.dumps(serialize(state), sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if repo and name not in {'human_confirm', 'finalize', 'consolidate'}:
                cached = repo.get_stage(state['run_id'], name, key)
                if cached is not None:
                    return restore(cached)
            if repo:
                repo.record_event(state['run_id'], runtime_worker_id.get(), 'stage_started', {'stage': name})
            result = fn(state)
            if budget:
                budget.check()
            runtime_lease_check.get()()
            if repo and name not in {'human_confirm', 'finalize', 'consolidate'}:
                repo.save_stage(state['run_id'], name, key, serialize(result), worker_id=runtime_worker_id.get())
            if repo:
                repo.record_event(state['run_id'], runtime_worker_id.get(), 'stage_finished', {'stage': name})
            return result
        except BudgetExceeded as exc:
            return {'status': 'partial' if state.get('draft') else 'insufficient_evidence',
                    'errors': state.get('errors', []) + [str(exc)], 'final_report': None}
    return node


def recall_node(state):
    # Legacy global memory is not an authorized evidence or preference source.
    return {'context': '', 'recalled_memories': [], 'context_used': False}


def plan_node(state):
    return {'plan': PlannerAgent().plan(state['topic'], state.get('requirements', ''))}


def retrieve_node(state):
    import research_agent.tools.load  # register only when retrieval executes
    results, docs, retrieval_errors = [], {}, []
    limit = state.get('budget', {}).get('max_documents', get_settings().max_documents)
    for subtask in state.get('plan', []):
        if current_budget.get():
            current_budget.get().check()
        if len(docs) >= limit:
            break
        query_task = subtask.model_copy(deep=True)
        if state.get('review'):
            query_task.query += '\n补充检索要求：' + state['review'].suggestions
        for result in RetrieverAgent().retrieve(query_task):
            results.append(result)
            if result.error:
                retrieval_errors.append(result.error)
            for doc in result.documents:
                if len(docs) >= limit:
                    break
                docs.setdefault(doc.id, doc)
        if current_budget.get():
            current_budget.get().check()
    documents = list(docs.values())[:limit]
    documents = runtime_document_accept.get()(documents)
    evidence = build_evidence(documents)
    return {'retrieval_results': results, 'documents': documents, 'evidence': evidence,
            'errors': state.get('errors', []) + (retrieval_errors if not evidence else []),
            'status': 'running' if evidence else 'insufficient_evidence', 'final_report': None}


def read_node(state):
    reader = ReaderAgent()
    # Each reading unit is a located source fragment, never a truncated full paper.
    source_docs = {d.id: d for d in state.get('documents', [])}
    fragments = [Document(id=e.id, title=source_docs[e.document_id].title, content=e.text,
                          url=e.source_url, source=source_docs[e.document_id].source)
                 for e in state.get('evidence', [])]
    summaries = reader.read_documents(fragments, state['topic'])
    findings = []
    for e in state.get('evidence', []):
        text = summaries.get(e.id) or summaries.get(e.document_id)
        if text:
            findings.append(Finding(id='finding:' + e.id, document_id=e.document_id,
                                    text=text, evidence_ids=[e.id],
                                    limitations='仅摘要证据' if e.scope == 'abstract' else ''))
    per_document = {}
    for f in findings:
        per_document[f.document_id] = per_document.get(f.document_id, '') + '\n' + f.text
    return {'summaries': per_document, 'findings': findings,
            'status': 'running' if findings else 'insufficient_evidence'}


def write_node(state):
    draft = WriterAgent().write(state['topic'], state.get('requirements', ''),
        state.get('summaries', {}), state.get('documents', []),
        evidence=state.get('evidence', []), findings=state.get('findings', []),
        feedback=state.get('review'))
    draft.validation_errors = validate_report(draft, state.get('evidence', []))
    return {'draft': draft}


def review_node(state):
    draft = state['draft'].model_copy(deep=True)
    if draft.validation_errors:
        feedback = ReviewFeedback(issues=draft.validation_errors, suggestions='修复所有引用与论断映射错误', action='rewrite')
    else:
        feedback = ReviewerAgent().review(draft, state['topic'], evidence=state.get('evidence', []),
            requirements=state.get('requirements', ''), plan=state.get('plan', []))
    # A high score cannot override explicit rejection or failed evidence checks.
    verified = bool(draft.claims) and all(feedback.claim_verdicts.get(c.id) == 'supported' for c in draft.claims)
    for claim in draft.claims:
        verdict = feedback.claim_verdicts.get(claim.id, 'uncertain')
        claim.verification = verdict if verdict in {'supported', 'unsupported', 'uncertain'} else 'uncertain'
    approved = feedback.approved and feedback.score >= get_settings().review_threshold and verified
    feedback.approved = approved
    return {'review': feedback, 'need_revision': not approved, 'draft': draft}


def revise_node(state):
    return {'iteration': state.get('iteration', 0) + 1}


def human_confirm_node(state):
    if state.get('require_approval', get_settings().require_human_approval):
        decision = interrupt({'question': '批准此版本？', 'draft_hash': hashlib.sha256(state['draft'].model_dump_json().encode()).hexdigest()})
        approved = isinstance(decision, dict) and decision.get('approved') is True
        return {'human_approved': approved, 'status': 'running' if approved else 'rejected', 'final_report': None}
    return {'human_approved': True}


def finalize_node(state):
    if state.get('need_revision', True) or not state.get('human_approved', False):
        return {'status': 'rejected' if state.get('status') == 'rejected' else 'partial', 'final_report': None}
    return {'final_report': state['draft'], 'status': 'completed'}


def consolidate_node(state):
    # Worker commits publication first; only then may verified conclusions be indexed.
    return {}


def route_after_review(state):
    if state.get('need_revision', True):
        if state.get('iteration', 0) < state.get('max_revisions', get_settings().max_iterations) and state['review'].action != 'stop':
            return 'revise'
        return 'partial'
    return 'human_confirm'


def route_after_human(state):
    return 'finalize' if state.get('human_approved') else END


def _route(normal):
    return lambda state: END if state.get('status') in {'partial', 'insufficient_evidence', 'failed', 'rejected', 'cancelled'} else normal


def partial_node(state):
    return {'status': 'partial', 'final_report': None, 'errors': state.get('errors', []) + ['审核未通过，修订预算耗尽或审核要求停止']}


def build_graph(checkpointer=None):
    w = StateGraph(ResearchState)
    nodes = {'recall': recall_node, 'plan': plan_node, 'retrieve': retrieve_node, 'read': read_node,
             'write': write_node, 'review': review_node, 'revise': revise_node, 'partial': partial_node,
             'human_confirm': human_confirm_node, 'finalize': finalize_node, 'consolidate': consolidate_node}
    for name, fn in nodes.items():
        w.add_node(name, _guard(name, fn))
    w.set_entry_point('recall')
    for source, target in [('recall','plan'), ('plan','retrieve'), ('retrieve','read'), ('read','write'), ('write','review')]:
        w.add_conditional_edges(source, _route(target))
    w.add_conditional_edges('review', lambda s: END if s.get('status') in {'partial','insufficient_evidence'} else route_after_review(s))
    w.add_conditional_edges('revise', lambda s: {'retrieve_more':'retrieve', 'extract_again':'read'}.get(s['review'].action, 'write'))
    w.add_edge('partial', END)
    w.add_conditional_edges('human_confirm', route_after_human)
    w.add_conditional_edges('finalize', lambda s: 'consolidate' if s.get('status') == 'completed' else END)
    w.add_edge('consolidate', END)
    return w.compile(checkpointer=checkpointer if checkpointer is not None else get_checkpointer())


graph = build_graph()


def initial_state(topic, requirements='', session_id=None, run_id=None, budget=None, require_approval=None):
    s = get_settings()
    return {'topic': topic, 'requirements': requirements, 'session_id': session_id or uuid.uuid4().hex,
        'run_id': run_id or uuid.uuid4().hex, 'iteration': 0, 'status': 'running',
        'draft': None, 'final_report': None, 'review': None, 'human_approved': False,
        'need_revision': True, 'errors': [], 'documents': [], 'evidence': [], 'findings': [],
        'summaries': {}, 'max_revisions': s.max_iterations,
        'require_approval': s.require_human_approval if require_approval is None else require_approval,
        'budget': budget or {'deadline': time.time()+s.task_deadline_seconds, 'max_calls': s.max_model_calls,
                            'max_tokens': s.max_task_tokens, 'max_documents': s.max_documents}}


def run_research(topic, requirements='', session_id=None, run_id=None):
    state = initial_state(topic, requirements, session_id, run_id)
    with budget_scope(Budget(state['budget'])):
        return graph.invoke(state, {'configurable': {'thread_id': state['run_id']}, 'recursion_limit': 1000})


def approve_research(run_id, approved=True):
    return graph.invoke(Command(resume={'approved': approved}), {'configurable': {'thread_id': run_id}, 'recursion_limit': 1000})


def is_waiting_approval(run_id):
    snap = get_run_state(run_id)
    return any(getattr(t, 'interrupts', ()) for t in snap.tasks)


def get_run_state(run_id):
    return graph.get_state({'configurable': {'thread_id': run_id}})


def debate_research(*args, **kwargs):
    raise NotImplementedError('辩论模式尚未接入证据核验与审批；请使用标准科研工作流')
