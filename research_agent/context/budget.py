"""Conservative UTF-8 estimates shared with the model gateway (not exact tokens)."""
from contextvars import ContextVar
from contextlib import contextmanager

current_input_limit = ContextVar('context_input_limit', default=None)


@contextmanager
def input_limit_scope(limit):
    token = current_input_limit.set(limit)
    try:
        yield
    finally:
        current_input_limit.reset(token)


class ContextCapacityError(ValueError):
    pass


def estimate_text(text):
    return len(str(text).encode('utf-8'))


def estimate_messages(messages):
    return sum(estimate_text(message.content) + 16 for message in messages) + 16


def excerpt(text, max_bytes):
    encoded = text.encode('utf-8')
    if len(encoded) <= max_bytes:
        return text, False
    marker = '\n[truncated: 摘录]'
    room = max_bytes - estimate_text(marker)
    if room <= 0:
        return '', True
    return encoded[:room].decode('utf-8', errors='ignore') + marker, True
