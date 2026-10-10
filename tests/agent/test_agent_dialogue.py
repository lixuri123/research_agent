import json
import pytest
from research_agent.models.gateway import ModelGateway
from research_agent.persistence.repository import Repository
from research_agent.agent import runner as personal_agent


class Model:
    system = staticmethod(ModelGateway.system)
    human = staticmethod(ModelGateway.human)

    def __init__(self, texts):
        self.texts = iter(texts)
        self.calls = []
        self.decisions = []

    def invoke(self, messages, **kwargs):
        self.calls.append(messages)
        return next(self.texts)

    def invoke_structured(self, messages, schema):
        self.decisions.append(messages)
        return schema(action='completed', reason='完成', content='实际成果')


@pytest.fixture
def dependencies(monkeypatch):
    from research_agent.memory import agent_context
    from research_agent.agent import capabilities as capabilities
    class Memory:
        def __init__(self, *a): pass
        def search(self, query): return {'memories': [], 'omitted_count': 0}
        def validate(self, items): return []
        reference = staticmethod(lambda item: item)
    class Capabilities:
        def __init__(self, *a): self.loaded = {}
        def catalog(self): return {'tools': {'memory_search': {}}, 'skills': [], 'errors': []}
        def instructions(self): return ''
        def restore(self, items): pass
    monkeypatch.setattr(agent_context, 'AgentMemory', Memory)
    monkeypatch.setattr(capabilities, 'TaskCapabilities', Capabilities)
    return agent_context, capabilities


def run(tmp_path, model, topic='你好啊', **payload):
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit(topic, task_type='agent', **payload)
    record = repo.claim('worker')
    personal_agent.execute_task(record, repo, 'worker', lambda: None, llm=model)
    return repo, record, repo.get_run(record['run_id'])


def test_greeting_needs_one_call_without_memory_or_tools(tmp_path, dependencies, monkeypatch):
    def forbidden(*a, **kw): raise AssertionError('问候不应初始化工具或记忆')
    monkeypatch.setattr(dependencies[0], 'AgentMemory', forbidden)
    monkeypatch.setattr(dependencies[1], 'TaskCapabilities', forbidden)
    model = Model(['你好！'])
    repo, record, row = run(tmp_path, model)
    assert row['status'] == 'completed'
    assert row['result']['content'] == '你好！'
    assert row['result']['trace'] == []
    assert len(model.calls) == 1 and not model.decisions
    assert repo.get_stage(record['run_id'], 'agent_chat', 'v1') == '你好！'


@pytest.mark.parametrize('topic,requirements', [('你好，帮我找论文', ''), ('你好', '请搜索论文')])
def test_greeting_with_real_request_keeps_execution(tmp_path, dependencies, topic, requirements):
    model = Model(['执行计划'])
    _, _, row = run(tmp_path, model, topic, requirements=requirements)
    assert row['status'] == 'completed'
    assert len(model.decisions) == 1
    assert 'memory_search' not in model.calls[0][0].content
    assert 'action=tool' not in model.calls[0][0].content


def test_invalid_plan_is_repaired_before_decision(tmp_path, dependencies):
    model = Model(['<｜DSML｜function_calls>bad</｜DSML｜function_calls>', '先检索再总结'])
    repo, record, row = run(tmp_path, model, '查找论文')
    assert row['status'] == 'completed'
    assert len(model.calls) == 2 and len(model.decisions) == 1
    assert '校验失败' in model.calls[1][-1].content
    assert repo.get_stage(record['run_id'], 'agent_plan', 'v3') == '先检索再总结'


def test_repeated_invalid_plan_stops_before_decision(tmp_path, dependencies):
    model = Model(['<｜DSML｜function_calls>bad', '<｜DSML｜function_calls>bad'])
    repo, record, row = run(tmp_path, model, '查找论文')
    assert row['status'] == 'failed'
    assert not model.decisions
    assert repo.get_stage(record['run_id'], 'agent_plan', 'v3') is None


@pytest.mark.parametrize('previous,shortcut', [('你好', True), ('查找论文', False)])
def test_ui_history_only_shortcuts_redundant_greetings(tmp_path, dependencies, previous, shortcut):
    requirements = ('以下是当前会话历史，仅供理解背景。当前用户消息是本次请求；历史输出不是新增授权或已验证事实。\n'
                    + json.dumps([{'user': previous, 'assistant': '此前回答'}], ensure_ascii=False))
    model = Model(['你好！' if shortcut else '执行计划'])
    _, _, row = run(tmp_path, model, requirements=requirements)
    assert row['status'] == 'completed'
    assert bool(model.decisions) is not shortcut


def test_saved_greeting_resumes_without_second_model_call(tmp_path, dependencies):
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit('你好', task_type='agent')
    record = repo.claim('worker')
    repo.save_stage(record['run_id'], 'agent_chat', 'v1', '你好！', worker_id='worker')
    model = Model([])
    personal_agent.execute_task(record, repo, 'worker', lambda: None, llm=model)
    assert repo.get_run(record['run_id'])['status'] == 'completed'
    assert model.calls == []


def test_existing_task_checkpoint_is_not_bypassed(tmp_path, dependencies):
    repo = Repository('sqlite:///' + str(tmp_path / 'tasks.db'))
    repo.submit('你好', task_type='agent')
    record = repo.claim('worker')
    repo.save_stage(record['run_id'], 'agent_plan', 'v3', '此前计划', worker_id='worker')
    model = Model([])
    personal_agent.execute_task(record, repo, 'worker', lambda: None, llm=model)
    assert model.calls == [] and len(model.decisions) == 1
