"""Explicit persistent checkpoint lifecycle; connection failures fail closed."""
from __future__ import annotations
import atexit
from contextlib import ExitStack
from pathlib import Path
from research_agent.core.config import get_settings

_stack = ExitStack()
_checkpointer = None
atexit.register(_stack.close)

def get_checkpointer(url=None):
    global _checkpointer
    if url is None and _checkpointer is not None:
        return _checkpointer
    target = url or get_settings().checkpoint_url
    if target.startswith('sqlite:///'):
        from langgraph.checkpoint.sqlite import SqliteSaver
        path = target[len('sqlite:///'):]
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        saver = _stack.enter_context(SqliteSaver.from_conn_string(path))
    elif target.startswith(('postgresql://', 'postgresql+psycopg://')):
        from langgraph.checkpoint.postgres import PostgresSaver
        saver = _stack.enter_context(PostgresSaver.from_conn_string(target.replace('postgresql+psycopg://', 'postgresql://', 1)))
    else:
        raise ValueError('CHECKPOINT_URL must use sqlite:/// or postgresql://')
    saver.setup()
    if url is None:
        _checkpointer = saver
    return saver
