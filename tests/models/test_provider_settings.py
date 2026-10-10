import json
import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient

from research_agent.api import app
from research_agent.core.config import get_settings


@pytest.fixture
def provider(tmp_path, monkeypatch):
    path = tmp_path / 'provider.json'
    monkeypatch.setenv('PROVIDER_CONFIG_PATH', str(path))
    get_settings.cache_clear()
    yield TestClient(app), path
    get_settings.cache_clear()


def payload(**changes):
    return dict(base_url='https://example.com/v1', model='test-model',
                planner_model='', reviewer_model='', writer_model='', summary_model='', **changes)


def test_save_redacts_and_preserves_key_and_updates_runtime(provider):
    client, path = provider
    result = client.put('/settings/provider', json=payload(api_key='test-private-key'))
    assert result.status_code == 200
    assert 'test-private-key' not in result.text
    assert result.json()['has_api_key'] is True
    assert get_settings().llm_model == 'test-model'
    assert get_settings().openai_api_key == 'test-private-key'
    assert client.put('/settings/provider', json=payload(api_key='')).status_code == 200
    assert json.loads(path.read_text())['api_key'] == 'test-private-key'
    assert 'test-private-key' not in client.get('/settings/provider').text
    output = subprocess.check_output(
        [sys.executable, '-c', 'from research_agent.core.config import get_settings; print(get_settings().llm_model)'],
        env=dict(os.environ), text=True)
    assert output.strip() == 'test-model'


@pytest.mark.parametrize('url', ['file:///tmp/model', 'https://user:password@example.com/v1', 'https://example.com/v1?key=private'])
def test_rejects_invalid_url_without_writing(provider, url):
    client, path = provider
    body = payload()
    body['base_url'] = url
    assert client.put('/settings/provider', json=body).status_code == 422
    assert not path.exists()


def test_test_connection_uses_draft_and_does_not_save(provider, monkeypatch):
    from research_agent.api_routes import providers as provider_api
    client, path = provider
    calls = []
    class FakeClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)
            self.chat = self.completions = self
        def create(self, **kwargs):
            calls.append(kwargs)
            return type('Reply', (), {'choices': [type('Choice', (), {'message': type('Message', (), {'content': 'OK'})()})()]})()
        def close(self):
            pass
    monkeypatch.setattr(provider_api, 'OpenAI', FakeClient)
    result = client.post('/settings/provider/test', json=payload(api_key='test-private-key'))
    assert result.status_code == 200
    assert result.json()['ok'] is True
    assert calls[0]['max_retries'] == 0
    assert calls[1]['model'] == 'test-model'
    assert not path.exists()


def test_connection_failure_does_not_expose_remote_error(provider, monkeypatch):
    from research_agent.api_routes import providers as provider_api
    client, _ = provider
    def fail(**kwargs):
        raise RuntimeError('remote body test-private-key')
    monkeypatch.setattr(provider_api, 'OpenAI', fail)
    result = client.post('/settings/provider/test', json=payload(api_key='test-private-key'))
    assert result.status_code == 200
    assert result.json()['ok'] is False
    assert 'test-private-key' not in result.text


def test_invalid_input_redacts_key_and_existing_config_survives(provider):
    client, path = provider
    assert client.put('/settings/provider', json=payload(api_key='test-private-key')).status_code == 200
    previous = path.read_bytes()
    body = payload(api_key=['test-private-key'])
    result = client.put('/settings/provider', json=body)
    assert result.status_code == 422
    assert 'test-private-key' not in result.text
    assert path.read_bytes() == previous


def test_environment_fallback_and_corrupt_config_fails_closed(provider):
    client, path = provider
    assert client.get('/settings/provider').status_code == 200
    path.write_text('{invalid test-private-key', encoding='utf-8')
    result = client.get('/settings/provider')
    assert result.status_code == 503
    assert 'test-private-key' not in result.text


def test_model_gateway_observes_saved_config(provider, monkeypatch):
    from research_agent.models import gateway as llm
    client, _ = provider
    seen = []
    monkeypatch.setattr(llm, 'ChatOpenAI', lambda **kwargs: seen.append(kwargs))
    client.put('/settings/provider', json=payload(api_key='test-private-key'))
    llm.ModelGateway()
    assert seen[-1]['base_url'] == 'https://example.com/v1'
    assert seen[-1]['model'] == 'test-model'
    assert seen[-1]['api_key'] == 'test-private-key'


def test_local_provider_without_key_can_create_gateway(provider, monkeypatch):
    from research_agent.models import gateway as llm
    from research_agent.core.config import Settings
    seen = []
    monkeypatch.setattr(llm, 'ChatOpenAI', lambda **kwargs: seen.append(kwargs))
    llm.ModelGateway(settings=Settings(_env_file=None, openai_api_key=''))
    assert seen[-1]['api_key'] == 'local-no-key'


@pytest.mark.parametrize('method,path', [('put', '/settings/provider'), ('post', '/settings/provider/test')])
@pytest.mark.parametrize('body', [[{'api_key': 'test-private-key'}], 'test-private-key'])
def test_invalid_top_level_input_never_echoes_credentials(provider, method, path, body):
    client, config_path = provider
    response = getattr(client, method)(path, json=body)
    assert response.status_code == 422
    assert 'test-private-key' not in response.text
    assert not config_path.exists()
