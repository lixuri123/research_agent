import uuid

import pytest
from langsmith import tracing_context
from langsmith.run_trees import RunTree

from research_agent.core import observability as obs
from research_agent.core.config import Settings


@pytest.fixture
def capture(monkeypatch):
    from langsmith import Client
    monkeypatch.setattr(obs, 'Client', lambda **kw: Client(**kw, auto_batch_tracing=False, info={}))
    sent = []
    monkeypatch.setattr(RunTree, 'post', lambda self, **kw: sent.append(('start', self)))
    monkeypatch.setattr(RunTree, 'patch', lambda self, **kw: sent.append(('end', self)))
    return sent


def settings(monkeypatch, **values):
    config = Settings(_env_file=None, provider_config_path='missing-test-provider.json',
                      **values)
    monkeypatch.setattr(obs, 'get_settings', lambda: config)
    return config


def record():
    return {'task_id': str(uuid.uuid4()), 'run_id': str(uuid.uuid4()),
            'owner_id': 'local', 'payload': {'topic': '你好', 'requirements': '',
                'task_type': 'agent', 'session_id': 'chat-1',
                'model_selection': {'provider_id': 'provider-1', 'model': 'test',
                                    'revision': 3, 'api_key': 'must-not-be-traced'}}}


def test_disabled_overrides_legacy_and_ambient_flags(monkeypatch, capture):
    settings(monkeypatch, langsmith_tracing=False, langsmith_api_key='test-key')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'true')
    monkeypatch.setenv('LANGSMITH_TRACING', 'true')
    with tracing_context(enabled=True):
        with obs.task_trace(record(), 'worker') as root:
            with obs.trace_span('tool', inputs={'query': 'hello'}):
                assert obs.current_run_id() is None
        assert root is None
    assert capture == []


def test_enabled_uses_explicit_configuration_and_safe_metadata(monkeypatch, capture):
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key',
             langsmith_project='my-project', langsmith_endpoint='https://example.test',
             langsmith_workspace_id='workspace-1')
    clients = []
    def client(**kw):
        clients.append(kw)
        return object()
    monkeypatch.setattr(obs, 'Client', client)
    item = record()
    with obs.task_trace(item, 'worker') as root:
        assert root is not None
        assert obs.current_run_id() == str(root.id)
        assert root.metadata['task_id'] == item['task_id']
        assert root.metadata['run_id'] == item['run_id']
        assert root.metadata['session_id'] == 'chat-1'
        assert 'must-not-be-traced' not in str(root.inputs) + str(root.metadata)
        with obs.trace_span('tool', inputs={'query': 'hello'}) as child:
            assert child.parent_run_id == root.id
    assert clients[-1]['api_url'] == 'https://example.test'
    assert clients[-1]['workspace_id'] == 'workspace-1'
    assert root.session_name == 'my-project'
    assert [event for event, _ in capture] == ['start', 'start', 'end', 'end']


def test_missing_key_disables_reporting_with_warning(monkeypatch, capture, caplog):
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='')
    with obs.task_trace(record(), 'worker') as root:
        assert root is None
    assert capture == []
    assert 'LANGSMITH_API_KEY' in caplog.text


@pytest.mark.parametrize('operation', ['post', 'patch'])
def test_reporting_failure_never_reexecutes_business(monkeypatch, capture, operation):
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    def broken(*args, **kwargs):
        raise RuntimeError('reporting unavailable')
    monkeypatch.setattr(RunTree, operation, broken)
    calls = []
    with obs.task_trace(record(), 'worker'):
        calls.append('executed')
    assert calls == ['executed']
    assert obs.current_run_id() is None


def test_business_exception_preserved_even_if_teardown_fails(monkeypatch, capture):
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    def broken(*args, **kwargs):
        raise RuntimeError('reporting unavailable')
    monkeypatch.setattr(RunTree, 'patch', broken)
    original = ValueError('business failed')
    with pytest.raises(ValueError) as caught:
        with obs.task_trace(record(), 'worker'):
            raise original
    assert caught.value is original
    assert obs.current_run_id() is None


def test_initialization_failure_does_not_expose_credentials(monkeypatch, capture, caplog):
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='secret-key')
    def broken(**kw):
        raise RuntimeError('credential=secret-key')
    monkeypatch.setattr(obs, 'Client', broken)
    with obs.task_trace(record(), 'worker') as root:
        assert root is None
    assert 'secret-key' not in caplog.text
    assert capture == []


@pytest.fixture
def native_model(monkeypatch):
    from langchain_openai import ChatOpenAI
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langsmith import Client
    from research_agent.models import gateway as llm
    config = Settings(_env_file=None, openai_api_key='local-test', llm_max_attempts=1,
                      provider_config_path='missing-test-provider.json')
    monkeypatch.setattr(llm, 'get_settings', lambda: config)
    generations, calls = [], []
    def generate(self, messages, **kw):
        content = generations.pop(0)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=content,
            usage_metadata={'input_tokens': 12, 'output_tokens': 8, 'total_tokens': 20}))])
    monkeypatch.setattr(ChatOpenAI, '_generate', generate)
    monkeypatch.setattr(Client, 'create_run', lambda self, *a, **kw: calls.append(kw))
    monkeypatch.setattr(Client, 'update_run', lambda self, *a, **kw: None)
    return generations, calls


def test_worker_hello_has_attempt_root_and_one_native_model_call(
        monkeypatch, capture, native_model, tmp_path):
    from research_agent.persistence.repository import Repository
    from research_agent.worker import Worker
    from research_agent.agent import capabilities as capabilities
    from research_agent.memory import agent_context
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    class Memory:
        def __init__(self, *a, **kw): pass
        def search(self, query): return {'memories': [], 'omitted_count': 0}
        def validate(self, items): return []
        reference = staticmethod(lambda item: item)
    class Capabilities:
        def __init__(self, *a, **kw): self.loaded = {}
        def catalog(self): return {'tools': {}, 'skills': [], 'errors': []}
        def instructions(self): return ''
        def restore(self, items): pass
    monkeypatch.setattr(agent_context, 'AgentMemory', Memory)
    monkeypatch.setattr(capabilities, 'TaskCapabilities', Capabilities)
    generations, calls = native_model
    generations.extend(['你好！'])
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    item = repo.submit('你好', task_type='agent', session_id='chat-1')
    assert Worker(repo).run_once()
    row = repo.get_run(item['run_id'])
    assert row['status'] == 'completed'
    starts = [run for event, run in capture if event == 'start']
    root = next(run for run in starts if run.name == 'agent_task')
    assert root.outputs == {'status': 'completed', 'content': '你好！'}
    assert root.metadata['run_id'] == item['run_id']
    assert [run.name for run in starts if run.parent_run_id == root.id] == ['model_text']
    model_runs = [run for run in starts if run.run_type == 'llm']
    assert len(model_runs) == 1
    assert all(run.metadata['usage_metadata']['total_tokens'] == 20 for run in model_runs)
    assert {str(run.parent_run_id) for run in model_runs} == {
        str(run.id) for run in starts if run.name in {'model_text', 'model_structured'}}
    assert 'local-test' not in str(root.inputs) + str(root.metadata)
    events = repo.events(item['run_id'])
    linked = next(event for event in events if event['event'] == 'langsmith_trace_started')
    assert linked['data']['trace_id'] == str(root.id)


def test_format_failure_has_validation_error_and_repair_metadata(monkeypatch, capture, native_model):
    from pydantic import BaseModel
    from research_agent.models.gateway import ModelGateway
    class Answer(BaseModel):
        count: int
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    native_model[0].extend(['{"count":1}<｜DSML｜function_calls>', '{"count":2}'])
    with obs.task_trace(record(), 'worker'):
        assert ModelGateway().invoke_structured([], Answer).count == 2
    starts = [run for event, run in capture if event == 'start']
    validations = [run for run in starts if run.name == 'output_validation']
    assert len(validations) == 2
    assert validations[0].error and 'JSONDecodeError' in validations[0].error
    assert not validations[1].error
    model = next(run for run in starts if run.name == 'model_structured')
    assert model.metadata['output_format_repairs'] == 1
    assert len([run for run in starts if run.run_type == 'llm']) == 2


def test_capability_tool_is_nested_without_serializing_self(monkeypatch, capture):
    from research_agent.agent.capabilities import TaskCapabilities
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    cap = TaskCapabilities.__new__(TaskCapabilities)
    cap.allowed = set()
    from types import SimpleNamespace
    cap.mcp = SimpleNamespace(redact=lambda text: text)
    with obs.task_trace(record(), 'worker') as root:
        result = cap.call('not_allowed', {'query': 'hello'})
    child = next(run for event, run in capture if event == 'start' and run.name == 'agent_tool')
    assert child.parent_run_id == root.id
    assert child.inputs['tool'] == 'not_allowed'
    assert 'self' not in child.inputs
    assert child.outputs['result'] == result
    assert result['error']


def test_child_thread_retains_parent_and_records_final_status(monkeypatch, capture, tmp_path):
    from research_agent.agent.delegation import DelegationController
    from research_agent.persistence.repository import Repository
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit('你好', task_type='agent')
    item = repo.claim('worker')
    from research_agent.core.budget import Budget
    controller = DelegationController(item, repo, 'worker', lambda: None,
        Budget({'max_calls': 10, 'max_tokens': 10000}), {'tools': {}, 'skills': []})
    identifier = 'child-1'
    controller.agents[identifier] = {'id': identifier, 'mode': 'agent', 'goal': '回应问候',
        'status': 'queued', 'completion_criteria': '给出回复'}
    monkeypatch.setattr(controller, '_agent', lambda *a: {'status': 'completed', 'content': '你好'})
    try:
        with obs.task_trace(item, 'worker') as root:
            controller._start(identifier)
            controller.futures[identifier].result()
    finally:
        controller.close()
    child = next(run for event, run in capture if event == 'start' and run.name == 'child_agent')
    assert child.parent_run_id == root.id
    assert child.inputs['agent_id'] == identifier
    assert child.outputs['status'] == 'completed'


def test_research_attempts_have_distinct_traces_and_same_business_run(monkeypatch, capture, tmp_path):
    from research_agent.persistence.repository import Repository, runs
    from research_agent.worker import Worker
    from sqlalchemy import update
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    item = repo.submit('topic')
    from langgraph.graph import StateGraph
    from langgraph.checkpoint.memory import MemorySaver
    builder = StateGraph(dict)
    builder.add_node('research_node', lambda state: {'status': 'completed'})
    builder.set_entry_point('research_node')
    builder.set_finish_point('research_node')
    worker = Worker(repo, builder.compile(checkpointer=MemorySaver()))
    assert worker.run_once()
    with repo.engine.begin() as conn:
        conn.execute(update(runs).where(runs.c.run_id == item['run_id']).values(status='queued'))
    assert worker.run_once()
    roots = [run for event, run in capture if event == 'start' and run.name == 'research_task']
    assert len(roots) == 2
    assert roots[0].id != roots[1].id
    assert {root.metadata['run_id'] for root in roots} == {item['run_id']}
    assert all(root.outputs['status'] == 'completed' for root in roots)
    assert any(run.parent_run_id == roots[0].id and run.name == 'LangGraph'
               for event, run in capture if event == 'start')


def test_delegation_operations_are_traced(monkeypatch, capture):
    from research_agent.agent.delegation import DelegationController
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    controller = DelegationController.__new__(DelegationController)
    from types import SimpleNamespace
    controller.check_lease = lambda: None
    controller.budget = SimpleNamespace(check=lambda: None)
    with obs.task_trace(record(), 'worker') as root:
        result = controller.call('invalid', {})
    child = next(run for event, run in capture if event == 'start' and run.name == 'agent_delegation')
    assert child.parent_run_id == root.id
    assert child.inputs['tool'] == 'invalid'
    assert child.outputs['result'] == result


@pytest.mark.parametrize('enabled,broken', [(False, False), (True, True)])
def test_worker_completes_without_reporting_when_disabled_or_unavailable(
        monkeypatch, capture, tmp_path, enabled, broken):
    from research_agent.persistence.repository import Repository
    from research_agent.worker import Worker
    from research_agent.agent import runner as personal_agent
    settings(monkeypatch, langsmith_tracing=enabled, langsmith_api_key='test-key')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'true')
    if broken:
        def fail(*a, **kw): raise RuntimeError('offline')
        monkeypatch.setattr(RunTree, 'post', fail)
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    item = repo.submit('你好', task_type='agent')
    seen = []
    def execute(record, repo, worker_id, check, **kw):
        seen.append(obs.current_run_id())
        with obs.trace_span('operation'):
            repo.finish(record['run_id'], worker_id, 'completed', {'content': '你好'})
    monkeypatch.setattr(personal_agent, 'execute_task', execute)
    assert Worker(repo).run_once()
    assert repo.get_run(item['run_id'])['status'] == 'completed'
    assert seen == [None]
    assert not any(event['event'] == 'langsmith_trace_started' for event in repo.events(item['run_id']))
    assert capture == []


def test_worker_failure_is_visible_in_root_without_reexecution(monkeypatch, capture, tmp_path):
    from research_agent.persistence.repository import Repository
    from research_agent.worker import Worker
    from research_agent.agent import runner as personal_agent
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    item = repo.submit('你好', task_type='agent')
    called = []
    def fail(*a, **kw):
        called.append(True)
        raise ValueError('business failed')
    monkeypatch.setattr(personal_agent, 'execute_task', fail)
    assert Worker(repo).run_once()
    assert called == [True]
    assert repo.get_run(item['run_id'])['status'] == 'failed'
    root = next(run for event, run in capture if event == 'start' and run.name == 'agent_task')
    assert root.outputs == {'status': 'failed', 'content': 'business failed'}
    assert root.error == 'business failed'


def test_once_worker_flushes_traces_on_exit(monkeypatch):
    from types import SimpleNamespace
    from research_agent import worker
    calls = []
    monkeypatch.setattr(worker, 'Worker', lambda: SimpleNamespace(run_once=lambda: calls.append('execute')))
    monkeypatch.setattr(worker, 'flush_traces', lambda: calls.append('flush'), raising=False)
    monkeypatch.setattr('sys.argv', ['worker', '--once'])
    worker.main()
    assert calls == ['execute', 'flush']


def test_flush_is_bounded_and_best_effort(monkeypatch):
    from types import SimpleNamespace
    settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    calls = []
    def flush(timeout):
        calls.append(timeout)
        raise RuntimeError('offline')
    monkeypatch.setattr(obs, 'Client', lambda **kw: SimpleNamespace(flush=flush))
    obs.flush_traces()
    assert calls == [5]


def test_lease_heartbeat_runs_during_slow_trace_initialization(monkeypatch, capture, tmp_path):
    import time
    from research_agent import worker
    from research_agent.agent import runner as personal_agent
    from research_agent.persistence.repository import Repository
    config = settings(monkeypatch, langsmith_tracing=True, langsmith_api_key='test-key')
    config.worker_lease_seconds = 0.5
    monkeypatch.setattr(worker, 'get_settings', lambda: config)
    original_post = RunTree.post
    def slow_post(self, **kwargs):
        time.sleep(0.8)
        return original_post(self, **kwargs)
    monkeypatch.setattr(RunTree, 'post', slow_post)
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    item = repo.submit('你好', task_type='agent')
    def execute(record, repo, worker_id, check, **kw):
        check()
        repo.finish(record['run_id'], worker_id, 'completed', {'content': '你好'})
    monkeypatch.setattr(personal_agent, 'execute_task', execute)
    assert worker.Worker(repo).run_once()
    assert repo.get_run(item['run_id'])['status'] == 'completed'
