"""Durable repository-backed compatibility helpers; no Redis/in-memory fallback."""
from functools import lru_cache
from pathlib import Path
from research_agent.core.config import get_settings
from research_agent.persistence.repository import Repository

@lru_cache
def get_repository():
    url = get_settings().database_url
    if url.startswith('sqlite:///'):
        Path(url[len('sqlite:///'):]).parent.mkdir(parents=True, exist_ok=True)
    return Repository(url)

def create_task(payload):
    return get_repository().submit(payload['topic'], payload.get('requirements',''))['task_id']

def get_task(task_id):
    return get_repository().get(task_id)
