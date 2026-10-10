import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_agent.persistence.repository import Repository
from research_agent.workspace.service import WorkspaceService


@pytest.fixture
def execution(tmp_path):
    from research_agent.execution.service import ExecutionService
    root = tmp_path / 'project'
    root.mkdir()
    (root / 'notes.md').write_text('论文 hello\n', encoding='utf-8')
    (root / '.env').write_text('DO_NOT_COPY=secret', encoding='utf-8')
    repo = Repository('sqlite:///' + str(tmp_path / 'db.sqlite'))
    spaces = WorkspaceService(repo.engine, [tmp_path])
    space = spaces.bind('project', str(root), True)
    repo.submit('search', task_type='agent', workspace_id=space['id'])
    run = repo.claim('worker')
    settings = SimpleNamespace(execution_enabled=True, execution_root=str(tmp_path / 'execution'),
        execution_timeout_seconds=15, execution_output_bytes=16384,
        execution_helper_path='', execution_rg_path='')

    class Provider:
        calls = 0
        available = True

        def status(self):
            return {'available': self.available, 'backend': 'test', 'enforcement': 'partial',
                    'file_read': 'not_isolated', 'network': 'not_isolated', 'error': None}

        def search(self, inputs, query, limit, check_lease):
            self.calls += 1
            check_lease()
            assert [p.name for p in inputs.iterdir()] == ['00000.txt']
            assert '论文' in (inputs / '00000.txt').read_text(encoding='utf-8')
            return {'status': 'succeeded', 'exit_code': 0, 'truncated': False, 'error': None,
                'stdout': json.dumps({'type': 'match', 'data': {'path': {'text': './00000.txt'},
                    'lines': {'text': '论文 hello\n'}, 'line_number': 1, 'submatches': []}}), 'stderr': ''}

    provider = Provider()
    service = ExecutionService(spaces, settings, provider)
    return root, repo, space, run, provider, service


def call(execution, arguments=None, operation='step1'):
    _, _, space, run, _, service = execution
    return service.search(space['id'], run['run_id'], 'worker', operation,
                          arguments or {'query': 'hello', 'max_results': 5})


def test_execution_requires_independent_permission_and_strict_parameters(execution):
    *_, provider, service = execution
    space = execution[2]
    with pytest.raises(PermissionError):
        call(execution)
    service.set_permission(space['id'], True)
    with pytest.raises(ValueError):
        call(execution, {'query': 'hello', 'command': 'powershell'})
    with pytest.raises(ValueError):
        call(execution, {'query': 'hello', 'max_results': True})
    assert provider.calls == 0


def test_search_snapshot_replay_and_revocation(execution):
    root, _, space, run, provider, service = execution
    service.set_permission(space['id'], True)
    first = call(execution)
    assert first['matches'][0]['path'] == 'notes.md'
    assert first['manifest'][0]['version'] == service.workspaces.read(space['id'], 'notes.md')['version']
    assert call(execution)['id'] == first['id']
    assert provider.calls == 1
    assert (root / 'notes.md').read_text(encoding='utf-8') == '论文 hello\n'
    assert service.list(space['id'], run['run_id'])[0]['status'] == 'succeeded'
    with pytest.raises(ValueError):
        call(execution, {'query': 'changed', 'max_results': 5})
    service.set_permission(space['id'], False)
    with pytest.raises(PermissionError):
        call(execution)


def test_unavailable_backend_and_dead_lease_never_spawn(execution):
    _, repo, space, run, provider, service = execution
    service.set_permission(space['id'], True)
    provider.available = False
    result = call(execution)
    assert result['status'] == 'unavailable'
    assert provider.calls == 0
    repo.finish(run['run_id'], 'worker', 'failed', {'error': 'stop'})
    with pytest.raises(ValueError):
        call(execution, operation='step2')


def test_saturated_result_count_is_reported_as_truncated(execution):
    _, _, space, _, provider, service = execution
    service.set_permission(space['id'], True)
    original = provider.search

    def saturated(*args):
        value = original(*args)
        value['stdout'] = '\n'.join([value['stdout']] * 5)
        return value

    provider.search = saturated
    assert call(execution)['truncated'] is True


def test_capability_rechecks_permission_at_call(execution, monkeypatch):
    from research_agent.agent.capabilities import TaskCapabilities
    from research_agent.integrations.mcp_client import MCPClient
    _, _, space, run, _, service = execution
    monkeypatch.setattr(MCPClient, 'discover', lambda self: {'tools': {}, 'errors': []})
    monkeypatch.setattr(TaskCapabilities, 'workspaces', lambda self: service.workspaces)
    monkeypatch.setattr(TaskCapabilities, 'executions', lambda self: service)
    caps = TaskCapabilities({'workspace_id': space['id']}, run_id=run['run_id'], worker_id='worker')
    assert 'workspace_search_text' not in caps.catalog()['tools']
    service.set_permission(space['id'], True)
    assert 'workspace_search_text' in caps.catalog()['tools']
    service.set_permission(space['id'], False)
    result = caps.call('workspace_search_text', {'query': 'hello'}, operation_id='step1')
    assert result['error'] and not result.get('execution')


def test_provider_missing_helper_is_fail_closed(tmp_path):
    from research_agent.execution.providers.windows_acl import WindowsACLProvider
    settings = SimpleNamespace(execution_helper_path=str(tmp_path / 'absent.exe'),
        execution_rg_path=str(tmp_path / 'rg.exe'), execution_timeout_seconds=15,
        execution_output_bytes=16384)
    provider = WindowsACLProvider(settings)
    assert not provider.status()['available']
    with pytest.raises(RuntimeError):
        provider.search(tmp_path, 'hello', 5, lambda: None)


def test_provider_rejects_programs_in_execution_storage(tmp_path):
    from research_agent.execution.providers.windows_acl import WindowsACLProvider
    cache = tmp_path / 'execution'
    cache.mkdir()
    (cache / 'helper.exe').write_bytes(b'not a program')
    (cache / 'rg.exe').write_bytes(b'not a program')
    settings = SimpleNamespace(execution_helper_path=str(cache / 'helper.exe'),
        execution_rg_path=str(cache / 'rg.exe'), execution_root=str(cache),
        execution_timeout_seconds=15, execution_output_bytes=16384)
    provider = WindowsACLProvider(settings)
    with pytest.raises(RuntimeError, match='执行内部目录'):
        provider._paths()


def test_execution_permission_api_requires_explicit_partial_consent(execution, monkeypatch):
    from fastapi.testclient import TestClient
    from research_agent import api
    _, repo, space, _, _, service = execution
    monkeypatch.setattr(api, 'get_repository', lambda: repo)
    monkeypatch.setattr(api, 'get_workspaces', lambda: service.workspaces)
    monkeypatch.setattr(api, 'get_executions', lambda: service)
    with TestClient(api.app) as client:
        url = f"/workspaces/{space['id']}/execution-permission"
        assert client.put(url, json={'enabled': True}).status_code == 422
        enabled = client.put(url, json={'enabled': True, 'accept_partial': True})
        assert enabled.status_code == 200 and enabled.json()['enabled']
        assert client.put(url, json={'enabled': False}).json()['enabled'] is False


@pytest.mark.skipif(os.name != 'nt' or not Path('native/windows-sandbox/bin/research-sandbox.exe').is_file(),
                    reason='requires built Windows helper')
def test_real_python_adapter_search_and_lease_cancellation(tmp_path):
    from research_agent.execution.providers.windows_acl import WindowsACLProvider
    inputs = tmp_path / 'inputs'
    inputs.mkdir()
    (inputs / '00000.txt').write_text('论文 --中文\n', encoding='utf-8')
    settings = SimpleNamespace(execution_helper_path='', execution_rg_path='',
        execution_timeout_seconds=15, execution_output_bytes=16384)
    provider = WindowsACLProvider(settings, tmp_path / 'unrelated-business')
    assert provider.status()['available']
    result = provider.search(inputs, '--中文', 5, lambda: None)
    assert result['status'] == 'succeeded', result
    matches = [json.loads(line) for line in result['stdout'].splitlines() if json.loads(line)['type'] == 'match']
    assert matches and '--中文' in matches[0]['data']['lines']['text']

    checks = 0

    def lost():
        nonlocal checks
        checks += 1
        if checks >= 2:  # First passes before spawn; second cancels a started helper.
            raise ValueError('lease lost')

    with pytest.raises(ValueError, match='lease lost'):
        provider.search(inputs, '中文', 5, lost)
    assert checks >= 2
