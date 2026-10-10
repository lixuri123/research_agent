import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

import pytest
from fastapi.testclient import TestClient

from research_agent.api import app
from research_agent.core.config import get_settings


@pytest.fixture
def setup(tmp_path, monkeypatch):
    path = tmp_path / 'provider.json'
    monkeypatch.setenv('PROVIDER_CONFIG_PATH', str(path))
    monkeypatch.setenv('LLM_API_KEY', 'environment-key')
    monkeypatch.setenv('LLM_MODEL', 'environment-model')
    get_settings.cache_clear()
    yield TestClient(app), path
    get_settings.cache_clear()


def entry(name='Service B', **changes):
    values = dict(name=name, base_url='https://b.example/v1', api_key='secret-b',
                  model='b-one', models=['b-one', 'b-two'])
    return {**values, **changes}


def test_multi_crud_and_isolated_keys(setup):
    client, path = setup
    result = client.post('/settings/providers', json=entry())
    assert result.status_code == 200
    second = result.json()
    assert 'secret-b' not in result.text
    providers = client.get('/settings/providers').json()['providers']
    assert len(providers) == 2
    identifier = second['id']
    response = client.put(f'/settings/providers/{identifier}', json=entry(api_key='', models=['b-one', 'b-three']))
    assert response.status_code == 200
    assert response.json()['revision'] != second['revision']
    content = json.loads(path.read_text(encoding='utf-8'))
    assert next(p for p in content['providers'] if p['id'] == identifier)['api_key'] == 'secret-b'
    assert client.put('/settings/providers/default/selection', json={'provider_id': identifier, 'model': 'b-three'}).status_code == 200
    assert get_settings().llm_model == 'b-three'
    assert get_settings().openai_api_key == 'secret-b'
    assert client.delete(f'/settings/providers/{identifier}').status_code == 200
    assert all(p['id'] != identifier for p in client.get('/settings/providers').json()['providers'])


def test_migrates_legacy_without_losing_roles_or_key(setup):
    client, path = setup
    path.write_text(json.dumps(dict(base_url='https://old.example/v1', api_key='old-secret',
                                  model='old-model', planner_model='old-planner')))
    response = client.get('/settings/providers')
    assert response.status_code == 200
    old = response.json()['providers'][0]
    assert old['models'] == ['old-model', 'old-planner']
    assert 'old-secret' not in response.text
    client.post('/settings/providers', json=entry())
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['version'] == 2
    assert saved['providers'][0]['api_key'] == 'old-secret'


def test_revision_survives_changes_deletion_and_context_propagates(setup):
    from research_agent.models.provider_registry import pin_selection, selection_overrides
    from research_agent.core.config import model_settings_scope
    client, _ = setup
    saved = client.post('/settings/providers', json=entry()).json()
    pinned = pin_selection({'provider_id': saved['id'], 'model': 'b-two'})
    assert 'secret-b' not in json.dumps(pinned)
    client.put(f"/settings/providers/{saved['id']}", json=entry(base_url='https://changed.example/v1', api_key='new-secret'))
    client.delete(f"/settings/providers/{saved['id']}")
    overrides = selection_overrides(pinned)
    with model_settings_scope(overrides):
        assert get_settings().openai_base_url == 'https://b.example/v1'
        assert get_settings().llm_model == 'b-two'
        assert get_settings().openai_api_key == 'secret-b'
        with ThreadPoolExecutor(1) as pool:
            assert pool.submit(copy_context().run, lambda: get_settings().llm_model).result() == 'b-two'
    assert get_settings().llm_model != 'b-two'
    code = ('import json,sys; from research_agent.models.provider_registry import selection_overrides; '
            'from research_agent.core.config import model_settings_scope,get_settings; '
            '\nwith model_settings_scope(selection_overrides(json.loads(sys.argv[1]))): print(get_settings().llm_model)')
    assert subprocess.check_output([sys.executable, '-c', code, json.dumps(pinned)], env=dict(os.environ), text=True).strip() == 'b-two'


@pytest.mark.parametrize('path', ['/settings/providers', '/settings/providers/test'])
def test_invalid_input_redaction(setup, path):
    client, _ = setup
    response = client.post(path, json=[{'api_key': 'secret-b'}])
    assert response.status_code == 422
    assert 'secret-b' not in response.text


def test_unlisted_model_cannot_be_selected(setup):
    from research_agent.models.provider_registry import pin_selection
    client, _ = setup
    saved = client.post('/settings/providers', json=entry()).json()
    with pytest.raises(ValueError):
        pin_selection({'provider_id': saved['id'], 'model': 'other'})


@pytest.mark.parametrize('route,field', [('/tasks', 'task'), ('/research', 'topic')])
def test_task_api_pins_model_and_worker_uses_old_revision(setup, monkeypatch, route, field):
    from types import SimpleNamespace
    from research_agent import api
    from research_agent.agent import runner as personal_agent
    from research_agent.persistence.repository import Repository
    from research_agent.worker import Worker
    client, path = setup
    repo = Repository('sqlite:///' + str(path.parent / 'tasks.db'))
    monkeypatch.setattr(api, 'get_repository', lambda: repo)
    service = client.post('/settings/providers', json=entry()).json()
    selection = {'provider_id': service['id'], 'model': 'b-two'}
    response = client.post(route, json={field: 'test task', 'model_selection': selection})
    assert response.status_code == 200
    record = repo.get(response.json()['task_id'])
    assert record['payload']['model_selection']['model'] == 'b-two'
    assert 'secret-b' not in json.dumps(record)
    assert client.post(route, json={field: 'invalid', 'model_selection': {**selection, 'model': 'unknown'}}).status_code == 422
    client.delete(f"/settings/providers/{service['id']}")
    observed = []
    def observe():
        settings = get_settings()
        observed.append((settings.llm_model, settings.openai_base_url, settings.openai_api_key))
    def execute(record, repo, worker_id, check_lease):
        observe()
        repo.finish(record['run_id'], worker_id, 'completed', {'content': 'test completed'})
    monkeypatch.setattr(personal_agent, 'execute_task', execute)
    class FakeGraph:
        def get_state(self, config):
            return SimpleNamespace(values={}, tasks=())
        def invoke(self, initial, config):
            observe()
            return {'status': 'partial'}
    assert Worker(repo, FakeGraph()).run_once()
    assert observed == [('b-two', 'https://b.example/v1', 'secret-b')]
    assert get_settings().llm_model != 'b-two'


def test_pinned_model_propagates_into_real_langgraph_node(setup):
    from langgraph.graph import StateGraph
    from langgraph.checkpoint.memory import MemorySaver
    from research_agent.models.provider_registry import pin_selection
    from research_agent.persistence.repository import Repository
    from research_agent.worker import Worker
    client, path = setup
    provider = client.post('/settings/providers', json=entry()).json()
    selection = pin_selection({'provider_id': provider['id'], 'model': 'b-two'})
    repo = Repository('sqlite:///' + str(path.parent / 'graph.db'))
    repo.submit('model propagation', model_selection=selection)
    client.delete(f"/settings/providers/{provider['id']}")
    observed = []
    def node(state):
        observed.append(get_settings().llm_model)
        return {'status': 'partial'}
    graph = StateGraph(dict)
    graph.add_node('model', node)
    graph.set_entry_point('model')
    graph.set_finish_point('model')
    assert Worker(repo, graph.compile(checkpointer=MemorySaver())).run_once()
    assert observed == ['b-two']


def test_probe_does_not_borrow_another_provider_key(setup, monkeypatch):
    from research_agent.api_routes import providers as provider_api
    client, _ = setup
    service = client.post('/settings/providers', json=entry()).json()
    seen = []
    class Fake:
        def __init__(self, **kwargs):
            seen.append(kwargs['api_key'])
            self.chat = self.completions = self
        def create(self, **kwargs):
            raise RuntimeError('no remote call')
        def close(self):
            pass
    monkeypatch.setattr(provider_api, 'OpenAI', Fake)
    client.post('/settings/providers/test', json=entry(name='new', api_key=''))
    client.post('/settings/providers/test', json={**entry(api_key=''), 'provider_id': service['id']})
    assert seen == ['local-no-key', 'secret-b']


@pytest.mark.parametrize('route,field', [('/tasks', 'task'), ('/research', 'topic')])
def test_submission_retry_preserves_original_task_after_provider_changes(setup, monkeypatch, route, field):
    from research_agent import api
    from research_agent.persistence.repository import Repository
    client, path = setup
    repo = Repository('sqlite:///' + str(path.parent / 'retry.db'))
    monkeypatch.setattr(api, 'get_repository', lambda: repo)
    service = client.post('/settings/providers', json=entry()).json()
    body = {field: 'retry test', 'model_selection': {'provider_id': service['id'], 'model': 'b-two'}}
    headers = {'Idempotency-Key': 'retry-key'}
    first = client.post(route, json=body, headers=headers)
    assert first.status_code == 200
    client.put(f"/settings/providers/{service['id']}", json=entry(api_key='changed-secret'))
    repeated = client.post(route, json=body, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    client.delete(f"/settings/providers/{service['id']}")
    repeated = client.post(route, json=body, headers=headers)
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert client.post(route, json={**body, field: 'changed task'}, headers=headers).status_code == 409


def test_legacy_write_restores_empty_registry_without_erasing_snapshots(setup):
    client, path = setup
    values = client.get('/settings/providers').json()['providers']
    for provider in values:
        assert client.delete(f"/settings/providers/{provider['id']}").status_code == 200
    snapshots = json.loads(path.read_text(encoding='utf-8'))['snapshots']
    response = client.put('/settings/provider', json={'base_url': 'https://restored.example/v1', 'model': 'restored', 'api_key': 'restored-key'})
    assert response.status_code == 200
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert all(saved['snapshots'][key] == value for key, value in snapshots.items())
    assert saved['providers'][0]['model'] == 'restored'


def test_migrated_provider_can_be_edited_without_default_route_collision(setup):
    client, _ = setup
    provider = client.get('/settings/providers').json()['providers'][0]
    assert client.put(f"/settings/providers/{provider['id']}", json=entry()).status_code == 200
