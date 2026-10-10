import threading
from types import SimpleNamespace

import pytest

from research_agent.core.budget import Budget
from research_agent.models.gateway import ModelGateway
from research_agent.persistence.repository import Repository


class Capabilities:
    def __init__(self, *args, **kwargs):
        self.allowed, self.loaded = set(), {}

    def instructions(self):
        return ''

    def restore(self, trace):
        pass

    def call(self, name, arguments, query='', operation_id=None):
        return {'documents': [], 'content': 'source:' + arguments['query'], 'error': None}


class Model:
    system = staticmethod(ModelGateway.system)
    human = staticmethod(ModelGateway.human)

    def __init__(self, enter=None, release=None):
        self.calls = 0
        self.enter, self.release = enter, release

    def invoke_structured(self, messages, schema):
        self.calls += 1
        if self.enter:
            self.enter.set()
        if self.release:
            assert self.release.wait(5)
        return schema(action='completed', reason='done', content='完成阅读')


@pytest.fixture
def environment(tmp_path):
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit('比较两篇论文', task_type='agent')
    record = repo.claim('worker')
    tools = {
        'browser_read': {'danger_level': 'safe'},
        'workspace_read': {'danger_level': 'safe'},
        'workspace_propose_write': {'danger_level': 'safe'},
        'mcp:s:write': {'danger_level': 'write'},
    }
    return repo, record, {'tools': tools, 'skills': [], 'errors': []}


def controller(environment, factory=None, **kwargs):
    from research_agent.agent.delegation import DelegationController
    repo, record, catalog = environment
    return DelegationController(record, repo, 'worker', lambda: None, Budget(), catalog,
        llm_factory=factory or (lambda: Model()), capability_factory=kwargs.pop('capability_factory', Capabilities), **kwargs)


def spawn(team, operation, **extra):
    return team.call('agent_spawn', {'goal': '阅读论文', 'context': '真实来源 URL',
        'completion_criteria': '返回方法与局限', **extra}, operation_id=operation)['agent']['id']


def test_spawn_runs_two_agents_concurrently_and_is_idempotent(environment):
    entered = [threading.Event(), threading.Event()]
    release = threading.Event()
    models = iter([Model(event, release) for event in entered])
    team = controller(environment, lambda: next(models))
    try:
        first, second = spawn(team, 'one'), spawn(team, 'two')
        assert all(event.wait(5) for event in entered)
        assert spawn(team, 'one') == first
        assert len(team.snapshot()) == 2
        assert team.call('agent_wait', {'timeout_seconds': 0})['pending']
        release.set()
        result = team.call('agent_wait', {'timeout_seconds': 5})
        assert result['pending'] == []
        assert {row['id'] for row in result['agents']} == {first, second}
        assert all(row['status'] == 'completed' for row in result['agents'])
    finally:
        release.set()
        team.close()

def test_child_permissions_cannot_expand_and_context_is_data(environment):
    models = []
    class InspectModel(Model):
        def invoke_structured(self, messages, schema):
            models.append(messages)
            assert '用户私密上下文' in messages[-1].content
            assert '用户私密上下文' not in messages[0].content
            assert 'workspace_propose_write' not in messages[0].content
            assert 'mcp:s:write' not in messages[0].content
            assert 'agent_spawn' not in messages[0].content
            return schema(action='tool', reason='attempt', tool='workspace_propose_write', arguments={})
    team = controller(environment, InspectModel, max_steps=1)
    try:
        spawn(team, 'one', context='用户私密上下文')
        row = team.call('agent_wait', {'timeout_seconds': 5})['agents'][0]
        assert row['status'] == 'blocked'
        assert '未授权' in row['trace'][0]['observation']['error']
        assert models
    finally:
        team.close()


def test_cancel_stops_after_inflight_model_without_publishing(environment):
    entered, release = threading.Event(), threading.Event()
    model = Model(entered, release)
    team = controller(environment, lambda: model)
    try:
        identifier = spawn(team, 'one')
        assert entered.wait(5)
        assert team.call('agent_cancel', {'agent_id': identifier})['agent']['status'] == 'cancelled'
        release.set()
        team.close()
        row = team.snapshot()[0]
        assert row['status'] == 'cancelled'
        assert not row.get('content')
        assert model.calls == 1
    finally:
        release.set()
        team.close()


def test_resume_keeps_completed_results_and_spawn_identity(environment):
    model = Model()
    team = controller(environment, lambda: model)
    identifier = spawn(team, 'one')
    assert team.call('agent_wait', {'timeout_seconds': 5})['pending'] == []
    team.close()
    resumed = controller(environment, lambda: pytest.fail('completed child was rerun'))
    try:
        assert spawn(resumed, 'one') == identifier
        assert resumed.snapshot()[0]['content'] == '完成阅读'
        assert model.calls == 1
    finally:
        resumed.close()


def test_running_child_resumes_saved_decision_without_recalling_model(environment):
    repo, record, _ = environment
    identifier = 'saved-child'
    repo.save_stage(record['run_id'], 'delegation', 'v1', {'agents': {identifier: {
        'id': identifier, 'operation_id': 'one', 'goal': '阅读论文', 'context': '',
        'completion_criteria': '方法与局限', 'mode': 'agent', 'status': 'running',
        'step': 0, 'trace': [], 'content': '', 'documents': []}}}, worker_id='worker')
    repo.save_stage(record['run_id'], f'child:{identifier}:decision:0', 'v1',
        {'action': 'completed', 'reason': 'done', 'content': '恢复成果'}, worker_id='worker')
    team = controller(environment, lambda: Model())
    try:
        assert team.call('agent_wait', {'timeout_seconds': 5})['agents'][0]['content'] == '恢复成果'
    finally:
        team.close()


def test_spawn_limit_invalid_arguments_and_unknown_child(environment):
    team = controller(environment, max_agents=1)
    try:
        spawn(team, 'one')
        assert team.call('agent_spawn', {'goal': '改变目标', 'completion_criteria': '结果'}, operation_id='one')['error']
        assert team.call('agent_spawn', {'goal': '第二个', 'completion_criteria': '结果'}, operation_id='two')['error']
        assert team.call('agent_spawn', {'goal': 'x', 'completion_criteria': 'y', 'tools': ['write']}, operation_id='three')['error']
        assert team.call('agent_status', {'agent_id': 'other-task'})['error']
        assert team.call('agent_wait', {'timeout_seconds': 61})['error']
    finally:
        team.close()


def test_children_share_parent_budget_and_document_limit(environment):
    from research_agent.core.budget import current_budget
    budgets = []
    class BudgetModel(Model):
        def invoke_structured(self, messages, schema):
            budget = current_budget.get()
            budgets.append(budget)
            budget.reserve(1, 1)
            return super().invoke_structured(messages, schema)
    team = controller(environment, BudgetModel)
    team.budget.limits['max_calls'] = 1
    try:
        spawn(team, 'one')
        spawn(team, 'two')
        statuses = {row['status'] for row in team.call('agent_wait', {'timeout_seconds': 5})['agents']}
        assert statuses == {'completed', 'blocked'}
        assert budgets == [team.budget, team.budget]
        team.max_documents = 1
        assert len(team.accept_documents([{'id': 'a', 'url': 'https://a.test'}])) == 1
        assert team.accept_documents([{'id': 'b', 'url': 'https://b.test'}]) == []
        assert len(team.accept_documents([{'id': 'a', 'url': 'https://a.test', 'content': 'full'}])) == 1
    finally:
        team.close()


def test_lost_lease_does_not_commit_child_result(environment):
    entered, release = threading.Event(), threading.Event()
    team = controller(environment, lambda: Model(entered, release))
    try:
        identifier = spawn(team, 'one')
        assert entered.wait(5)
        repo, record, _ = environment
        from sqlalchemy import update
        from research_agent.persistence.repository import runs
        with repo.engine.begin() as conn:
            conn.execute(update(runs).where(runs.c.run_id == record['run_id']).values(worker_id='replacement'))
        release.set()
        team.close()
        saved = repo.get_stage(record['run_id'], 'delegation', 'v1')['agents'][identifier]
        assert saved['status'] == 'running'
        assert not saved['content']
    finally:
        release.set()
        team.close()


def test_research_child_uses_verified_workflow_without_publishing(environment, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from research_agent.research import graph as workflow
    from research_agent.core.schemas import Document, RetrievalResult, SubTask, DraftReport, Citation, Claim, ReviewFeedback
    monkeypatch.setattr(workflow.PlannerAgent, 'plan', lambda *a: [SubTask(id='1', description='q', query='q')])
    monkeypatch.setattr(workflow.RetrieverAgent, 'retrieve', lambda *a: [RetrievalResult(documents=[Document(id='p', url='https://paper.test/p', content='Effect is small.')])])
    monkeypatch.setattr(workflow.ReaderAgent, 'read_documents', lambda self, docs, topic: {d.id: 'Effect is small.' for d in docs})
    def write(self, *args, **kwargs):
        e = kwargs['evidence'][0]
        return DraftReport(content='Effect is small. [1]', citations=[Citation(index=1, source_id='p', evidence_ids=[e.id])],
                           claims=[Claim(id='c', text='Effect is small.', evidence_ids=[e.id])])
    monkeypatch.setattr(workflow.WriterAgent, 'write', write)
    monkeypatch.setattr(workflow.ReviewerAgent, 'review', lambda *a, **k: ReviewFeedback(approved=True, score=1, claim_verdicts={'c': 'supported'}))
    monkeypatch.setattr(workflow, 'graph', workflow.build_graph(MemorySaver()))
    team = controller(environment)
    try:
        spawn(team, 'research', mode='research')
        row = team.call('agent_wait', {'timeout_seconds': 5})['agents'][0]
        assert row['status'] == 'completed'
        assert row['content'] == 'Effect is small. [1]'
        assert row['research_result']['draft']['claims'][0]['verification'] == 'supported'
        repo, record, _ = environment
        assert repo.get_run(record['run_id'])['status'] == 'running'
        assert repo.reports_for_run(record['run_id']) == []
    finally:
        team.close()


def test_research_cannot_use_evidence_outside_shared_source_budget(environment, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from research_agent.research import graph as workflow
    from research_agent.core.schemas import Document, RetrievalResult, SubTask
    monkeypatch.setattr(workflow.PlannerAgent, 'plan', lambda *a: [SubTask(id='1', description='q', query='q')])
    monkeypatch.setattr(workflow.RetrieverAgent, 'retrieve', lambda *a: [RetrievalResult(documents=[Document(id='p', url='https://paper.test/p', content='Effect is small.')])])
    def unauthorized_read(*args):
        raise AssertionError('Reader received evidence beyond the shared document budget')
    monkeypatch.setattr(workflow.ReaderAgent, 'read_documents', unauthorized_read)
    monkeypatch.setattr(workflow, 'graph', workflow.build_graph(MemorySaver()))
    team = controller(environment)
    team.max_documents = 1
    try:
        team.accept_documents([{'id': 'already', 'url': 'https://paper.test/already'}])
        spawn(team, 'research', mode='research')
        row = team.call('agent_wait', {'timeout_seconds': 5})['agents'][0]
        assert row['status'] == 'insufficient_evidence'
        assert row['documents'] == []
    finally:
        team.close()


def test_research_tool_allowlist_blocks_local_pdf_access(environment, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from research_agent.research import graph as workflow
    from research_agent.core.schemas import RetrievalResult, SubTask
    from research_agent.tools import _registry
    calls = []
    monkeypatch.setattr(workflow.PlannerAgent, 'plan', lambda *a: [SubTask(id='1', description='read', query='secret.pdf')])
    monkeypatch.setitem(_registry, 'pdf_parse', {'danger_level': 'safe', 'description': '',
        'fn': lambda query: calls.append(query) or RetrievalResult()})
    monkeypatch.setattr(workflow, 'graph', workflow.build_graph(MemorySaver()))
    team = controller(environment)
    try:
        spawn(team, 'local', mode='research')
        row = team.call('agent_wait', {'timeout_seconds': 5})['agents'][0]
        assert row['status'] == 'insufficient_evidence'
        assert calls == []
        assert any('未授权' in error for error in row['research_result']['errors'])
    finally:
        team.close()


def test_cancelled_queued_child_cannot_overwrite_cancellation(environment, monkeypatch):
    from research_agent.agent.delegation import DelegationController
    checked, release = threading.Event(), threading.Event()
    original = DelegationController._check_child
    def pause_before_transition(self, identifier):
        original(self, identifier)
        if not checked.is_set():
            checked.set()
            assert release.wait(5)
    monkeypatch.setattr(DelegationController, '_check_child', pause_before_transition)
    model = Model()
    team = controller(environment, lambda: model)
    try:
        identifier = spawn(team, 'one')
        assert checked.wait(5)
        team.call('agent_cancel', {'agent_id': identifier})
        release.set()
        team.futures[identifier].result(timeout=5)
        assert model.calls == 0
        assert team.snapshot()[0]['status'] == 'cancelled'
    finally:
        release.set()
        team.close()


def test_cancellation_prevents_research_reader_followup_model_calls(environment, monkeypatch):
    from langgraph.checkpoint.memory import MemorySaver
    from research_agent.research import graph as workflow
    from research_agent.core.schemas import Document, RetrievalResult, SubTask
    from research_agent.core.config import Settings
    import research_agent.research.agents.reader as readers
    calls = []
    team = controller(environment)
    class Transport:
        def invoke(self, messages, **kwargs):
            calls.append(messages)
            team.call('agent_cancel', {})
            return SimpleNamespace(content='摘要', usage_metadata={'total_tokens': 5})
    monkeypatch.setattr(workflow.PlannerAgent, 'plan', lambda *a: [SubTask(id='1', description='q', query='q')])
    monkeypatch.setattr(workflow.RetrieverAgent, 'retrieve', lambda *a: [RetrievalResult(documents=[Document(id='p', content='原文' * 4000)])])
    monkeypatch.setattr(readers, 'get_llm_for', lambda role: ModelGateway(transport=Transport(), settings=Settings(_env_file=None)))
    monkeypatch.setattr(workflow, 'graph', workflow.build_graph(MemorySaver()))
    try:
        identifier = spawn(team, 'read', mode='research')
        team.futures[identifier].result(timeout=5)
        assert team.snapshot()[0]['status'] == 'cancelled'
        assert len(calls) == 1
    finally:
        team.close()


def test_child_does_not_inherit_parent_mcp_write_authorization(environment, monkeypatch):
    import research_agent.agent.capabilities as capabilities
    calls = []
    class MCP:
        def call(self, name, arguments, authorized=False):
            calls.append(authorized)
            return {'documents': [], 'error': '工具已改为敏感操作，未授权' if not authorized else None}
        redact = staticmethod(lambda value: value)
    monkeypatch.setattr(capabilities, 'MCPClient', MCP)
    repo, record, catalog = environment
    record['payload']['authorized_mcp_tools'] = ['mcp:s:formerly_safe']
    catalog['tools']['mcp:s:formerly_safe'] = {'danger_level': 'safe'}
    class Caller(Model):
        def invoke_structured(self, messages, schema):
            return schema(action='tool', reason='read', tool='mcp:s:formerly_safe', arguments={})
    team = controller(environment, Caller, max_steps=1, capability_factory=capabilities.TaskCapabilities)
    try:
        spawn(team, 'revoked')
        row = team.call('agent_wait', {'timeout_seconds': 5})['agents'][0]
        assert calls == [False]
        assert row['trace'][0]['observation']['error']
    finally:
        team.close()


def test_shared_sources_preserve_full_text_and_identity_across_updates(environment):
    team = controller(environment)
    try:
        team.max_documents = 1
        team.accept_documents([{'id': 'paper', 'title': 'Paper', 'url': 'https://arxiv.org/abs/1234', 'abstract': '摘要'}])
        body = team.accept_documents([{'id': 'page', 'url': 'https://arxiv.org/pdf/1234', 'content': '完整原文'}])
        assert body[0]['id'] == 'paper'
        assert body[0]['title'] == 'Paper'
        updated = team.accept_documents([{'id': 'paper', 'url': 'https://arxiv.org/abs/1234', 'abstract': '摘要', 'content': ''}])
        assert updated[0]['content'] == '完整原文'
        assert len(team.documents) == 1
    finally:
        team.close()


def test_failed_spawn_persistence_does_not_leave_a_phantom_child(environment, monkeypatch):
    repo, record, _ = environment
    original = repo.save_stage
    failed = []
    def save(run_id, stage, *args, **kwargs):
        if stage == 'delegation' and not failed:
            failed.append(True)
            raise OSError('temporary failure')
        return original(run_id, stage, *args, **kwargs)
    monkeypatch.setattr(repo, 'save_stage', save)
    team = controller(environment)
    try:
        args = {'goal': '阅读', 'completion_criteria': '来源'}
        assert team.call('agent_spawn', args, operation_id='one')['error']
        assert team.snapshot() == []
        assert team.call('agent_spawn', args, operation_id='one')['agent']['id']
        assert team.call('agent_wait', {'timeout_seconds': 5})['pending'] == []
    finally:
        team.close()


def test_spawn_event_failure_does_not_strand_saved_child(environment, monkeypatch):
    repo, record, _ = environment
    original = repo.record_event
    def event(run_id, worker_id, name, data):
        if name == 'agent_spawned':
            raise OSError('event write failed')
        return original(run_id, worker_id, name, data)
    monkeypatch.setattr(repo, 'record_event', event)
    team = controller(environment)
    try:
        result = team.call('agent_spawn', {'goal': '阅读', 'completion_criteria': '来源'}, operation_id='one')
        assert not result['error']
        result = team.call('agent_wait', {'timeout_seconds': 1})
        assert not result['pending']
        assert result['agents'][0]['status'] == 'completed'
    finally:
        team.close()


@pytest.mark.parametrize('event_name', ['agent_progress', 'agent_finished'])
def test_progress_event_failure_does_not_discard_saved_child_deliverable(environment, monkeypatch, event_name):
    repo, record, _ = environment
    original = repo.record_event
    def event(run_id, worker_id, name, data):
        if name == event_name:
            raise OSError('event write failed')
        return original(run_id, worker_id, name, data)
    monkeypatch.setattr(repo, 'record_event', event)
    team = controller(environment)
    try:
        spawn(team, 'one')
        row = team.call('agent_wait', {'timeout_seconds': 5})['agents'][0]
        assert row['status'] == 'completed'
        assert row['content'] == '完成阅读'
    finally:
        team.close()
    resumed = controller(environment, lambda: pytest.fail('saved result rerun'))
    try:
        assert resumed.snapshot()[0]['status'] == 'completed'
        assert resumed.snapshot()[0]['content'] == '完成阅读'
    finally:
        resumed.close()
