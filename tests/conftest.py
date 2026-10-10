"""Tests never upload user data using credentials from the local .env."""
import pytest
from pathlib import Path


def pytest_configure(config):
    """Create the ignored parent before pytest initializes its temp factory."""
    if config.option.basetemp == '.tmp/pytest':
        target = Path(config.rootpath) / '.tmp' / 'pytest'
        target.parent.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(target)


@pytest.fixture(autouse=True)
def offline_tracing(monkeypatch):
    from langsmith import tracing_context
    from research_agent.core import observability as observability
    from research_agent.core.config import Settings
    settings = Settings(_env_file=None, langsmith_tracing=False)
    monkeypatch.setattr(observability, 'get_settings', lambda: settings)
    monkeypatch.setenv('LANGSMITH_TRACING', 'false')
    monkeypatch.setenv('LANGCHAIN_TRACING_V2', 'false')
    with tracing_context(enabled=False, parent=False):
        yield
