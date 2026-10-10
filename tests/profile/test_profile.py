"""Open attributes persist independently from memories and execution context."""
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine


def modules():
    from research_agent.profile.schemas import AttributeDraft
    from research_agent.profile.store import ProfileStore, ProfileConflict, ProfileNotFound
    return AttributeDraft, ProfileStore, ProfileConflict, ProfileNotFound


def draft(**changes):
    AttributeDraft, *_ = modules()
    return AttributeDraft(**{
        'path': ['背景', '自定义属性'], 'value': {'levels': [1, '新内容', True]},
        'source_refs': [{'type': 'user_edit', 'id': 'test'}], **changes,
    })


def test_open_values_survive_reopen_and_are_not_memories(tmp_path):
    _, Store, *_ = modules()
    url = 'sqlite:///' + str(tmp_path / 'profile.db')
    store = Store(create_engine(url))
    saved = store.create(draft())
    assert saved['status'] == 'candidate' and saved['version'] == 1
    store.engine.dispose()
    reopened = Store(create_engine(url))
    assert reopened.list_attributes()[0]['value'] == {'levels': [1, '新内容', True]}
    assert reopened.list_attributes()[0]['path'] == ['背景', '自定义属性']
    for value in ['text', 42, 0.5, False, None, ['a', 2]]:
        assert reopened.create(draft(value=value))['value'] == value
    from sqlalchemy import inspect
    assert 'memory_entries' not in inspect(reopened.engine).get_table_names()


def test_confirmation_revisions_conflicts_and_delete(tmp_path):
    _, Store, Conflict, NotFound = modules()
    store = Store(create_engine('sqlite:///' + str(tmp_path / 'profile.db')))
    saved = store.create(draft(basis='inferred'))
    active = store.change_status(saved['id'], 1, 'active')
    assert active['basis'] == 'user_confirmed' and active['version'] == 2
    with pytest.raises(Conflict):
        store.update(saved['id'], 1, draft(value='stale edit'))
    updated = store.update(saved['id'], 2, draft(value='corrected'))
    assert updated['status'] == 'candidate' and updated['version'] == 3
    assert [r['status'] for r in store.history(saved['id'])] == ['candidate', 'active', 'candidate']
    stale = store.change_status(saved['id'], 3, 'stale')
    assert stale['version'] == 4
    store.delete(saved['id'], 4)
    assert store.list_attributes() == []
    with pytest.raises(NotFound):
        store.history(saved['id'])


def test_nested_text_and_literal_path_separators_are_preserved(tmp_path):
    _, Store, *_ = modules()
    store = Store(create_engine('sqlite:///' + str(tmp_path / 'profile.db')))
    value = {'explanation': '  leading and trailing  ', 'list': ['  indented  ']}
    saved = store.create(draft(path=['研究/开发', '偏好'], value=value))
    assert saved['value'] == value
    updated = store.update(saved['id'], 1, draft(path=saved['path'], value=value))
    assert updated['path'] == ['研究/开发', '偏好']
    assert store.get(saved['id'])['value'] == value


def test_owner_isolation_and_competing_updates(tmp_path):
    _, Store, Conflict, NotFound = modules()
    engine = create_engine('sqlite:///' + str(tmp_path / 'profile.db'))
    local, other = Store(engine), Store(engine, owner_id='other')
    saved = local.create(draft())
    assert other.list_attributes() == []
    for action in [lambda: other.get(saved['id']), lambda: other.delete(saved['id'], 1),
                   lambda: other.change_status(saved['id'], 1, 'active')]:
        with pytest.raises(NotFound):
            action()

    def edit(value):
        try:
            return local.update(saved['id'], 1, draft(value=value))['version']
        except Conflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, ['first', 'second']))
    assert sorted(map(str, results)) == ['2', 'conflict']
    assert len(local.history(saved['id'])) == 2


def test_api_lifecycle_validation_and_expected_version(tmp_path, monkeypatch):
    _, Store, *_ = modules()
    from research_agent.api import app
    from research_agent import api as task_api
    from research_agent.api_routes import profile
    store = Store(create_engine('sqlite:///' + str(tmp_path / 'profile.db')))
    from research_agent.persistence.repository import Repository
    repository = Repository('sqlite:///' + str(tmp_path / 'profile.db'))
    monkeypatch.setattr(task_api, 'get_repository', lambda: repository)
    monkeypatch.setattr(profile, 'get_profile_store', lambda repository=None: store)
    with TestClient(app) as client:
        assert client.get('/profile').json() == {'person_id': 'local', 'attributes': []}
        invalid = client.post('/profile/attributes', json={'path': [], 'value': 'x', 'source_refs': []})
        assert invalid.status_code == 422
        payload = draft(value='中文').model_dump()
        created = client.post('/profile/attributes', json=payload)
        assert created.status_code == 201
        attr = created.json()
        assert client.post(f'/profile/attributes/{attr["id"]}/confirm', json={'expected_version': 1}).status_code == 200
        assert client.put(f'/profile/attributes/{attr["id"]}', json={**payload, 'expected_version': 1}).status_code == 409
        assert client.get(f'/profile/attributes/{attr["id"]}/history').status_code == 200
        assert client.delete(f'/profile/attributes/{attr["id"]}?expected_version=1').status_code == 409
        assert client.delete(f'/profile/attributes/{attr["id"]}?expected_version=2').status_code == 200
        assert client.get('/profile').json()['attributes'] == []


@pytest.mark.parametrize('changes', [
    {'path': [' ']}, {'path': ['ok'] * 13}, {'value': float('nan')},
    {'value': 'x' * 40000}, {'scope': 'project', 'scope_id': None},
    {'scope': 'person', 'scope_id': 'unrelated'}, {'source_refs': []},
])
def test_invalid_attributes_are_rejected(changes):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        draft(**changes)
