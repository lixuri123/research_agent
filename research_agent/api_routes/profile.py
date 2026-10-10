"""Local personal profile management; no Agent-context integration."""
from fastapi import APIRouter, HTTPException, Query

from research_agent.profile.schemas import AttributeDraft, AttributeUpdate, VersionRequest
from research_agent.profile.store import ProfileStore, ProfileConflict, ProfileNotFound

router = APIRouter(prefix='/profile', tags=['personal-profile'])


def get_profile_store(repository=None):
    from research_agent.persistence.queue import get_repository
    return ProfileStore((repository or get_repository()).engine)


def action(fn):
    try:
        return fn(get_profile_store())
    except ProfileNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ProfileConflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get('')
def profile():
    return action(lambda store: {'person_id': store.owner_id, 'attributes': store.list_attributes()})


@router.post('/attributes', status_code=201)
def create_attribute(req: AttributeDraft):
    return action(lambda store: store.create(req))


@router.put('/attributes/{attribute_id}')
def update_attribute(attribute_id: str, req: AttributeUpdate):
    draft = AttributeDraft.model_validate(req.model_dump(exclude={'expected_version'}))
    return action(lambda store: store.update(attribute_id, req.expected_version, draft))


@router.post('/attributes/{attribute_id}/confirm')
def confirm_attribute(attribute_id: str, req: VersionRequest):
    return action(lambda store: store.change_status(attribute_id, req.expected_version, 'active'))


@router.post('/attributes/{attribute_id}/invalidate')
def invalidate_attribute(attribute_id: str, req: VersionRequest):
    return action(lambda store: store.change_status(attribute_id, req.expected_version, 'stale'))


@router.get('/attributes/{attribute_id}/history')
def attribute_history(attribute_id: str):
    return action(lambda store: {'versions': store.history(attribute_id)})


@router.delete('/attributes/{attribute_id}')
def delete_attribute(attribute_id: str, expected_version: int = Query(ge=1)):
    def remove(store):
        store.delete(attribute_id, expected_version)
        return {'status': 'deleted'}
    return action(remove)
