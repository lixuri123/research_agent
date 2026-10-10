"""Explicit, best-effort LangSmith tracing for durable Worker attempts."""
from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache, wraps
import logging
import sys

from langsmith import Client, trace, traceable, tracing_context
from langsmith.run_helpers import get_current_run_tree, get_tracing_context
from langsmith.utils import tracing_is_enabled

from research_agent.core.config import get_settings

log = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def _client(endpoint, key, workspace, factory):
    return factory(api_url=endpoint.rstrip('/'), api_key=key,
                   workspace_id=workspace or None)


def setup_langsmith():
    """Return the configured client, or None. Never mutate process environment."""
    settings = get_settings()
    if not settings.langsmith_tracing:
        return None
    if not settings.langsmith_api_key.strip():
        log.warning('LangSmith 已启用但缺少 LANGSMITH_API_KEY，继续执行且不上传追踪')
        return None
    try:
        return _client(settings.langsmith_endpoint, settings.langsmith_api_key,
                       settings.langsmith_workspace_id, Client)
    except Exception as exc:
        log.warning('LangSmith 初始化失败（%s），继续执行且不上传追踪', type(exc).__name__)
        return None


def is_tracing_enabled():
    return tracing_is_enabled() is True


def current_run_id():
    if not is_tracing_enabled():
        return None
    run = get_current_run_tree()
    return str(run.id) if run else None


def finish_trace(run, outputs):
    if run is not None:
        try:
            error = outputs.get('content', 'Task failed') if outputs.get('status') in {'failed', 'blocked'} else None
            run.end(outputs=outputs, error=error)
        except Exception as exc:
            log.warning('LangSmith 结果记录失败（%s）', type(exc).__name__)


def flush_traces():
    """Give queued traces a bounded chance to upload on orderly Worker exit."""
    client = setup_langsmith()
    if client is not None:
        try:
            client.flush(timeout=5)
        except Exception as exc:
            log.warning('LangSmith 退出刷新失败（%s）', type(exc).__name__)


@contextmanager
def trace_span(name, *, inputs=None, run_type='chain', metadata=None):
    """Execute once, preserving business exceptions even on SDK failure."""
    if not is_tracing_enabled():
        yield None
        return
    # Restore the context even if the SDK fails before resetting it on patch.
    with tracing_context(**get_tracing_context()):
        try:
            manager = trace(name, run_type=run_type, inputs=inputs or {}, metadata=metadata)
            run = manager.__enter__()
        except Exception as exc:
            log.warning('LangSmith 追踪创建失败（%s）', type(exc).__name__)
            with tracing_context(enabled=False, parent=False):
                yield None
            return
        exception = (None, None, None)
        try:
            yield run
        except BaseException:
            exception = sys.exc_info()
            raise
        finally:
            try:
                if run.end_time is None and exception[0] is None:
                    run.end()
                manager.__exit__(*exception)
            except Exception as exc:
                log.warning('LangSmith 追踪提交失败（%s）', type(exc).__name__)


@contextmanager
def task_trace(record, worker_id):
    """One root per Worker attempt; attempts share the business run_id."""
    with tracing_context(enabled=False, parent=False):
        client = setup_langsmith()
        if client is None:
            yield None
            return
        settings = get_settings()
        payload = record['payload']
        kind = payload.get('task_type', 'research')
        metadata = {key: record[key] for key in ('task_id', 'run_id')}
        metadata.update(worker_id=worker_id, task_type=kind)
        metadata.update({key: payload[key] for key in ('session_id', 'workspace_id')
                         if payload.get(key)})
        selection = payload.get('model_selection') or {}
        metadata.update({key: selection[key] for key in ('provider_id', 'model', 'revision')
                         if key in selection})
        with tracing_context(enabled=True, parent=False, client=client,
                             project_name=settings.langsmith_project, metadata=metadata,
                             tags=['research-agent', kind]):
            with trace_span(f'{kind}_task', inputs={
                'topic': payload['topic'], 'requirements': payload.get('requirements', '')}) as run:
                yield run


def observed(name, *, inputs, run_type='chain', outputs=None):
    """Instrument functions without serializing self, repositories or clients."""
    def decorate(function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if not is_tracing_enabled():
                return function(*args, **kwargs)
            try:
                selected = inputs(*args, **kwargs)
            except Exception:
                selected = {}
            with trace_span(name, inputs=selected, run_type=run_type) as run:
                result = function(*args, **kwargs)
                try:
                    selected_output = outputs(result, *args, **kwargs) if outputs else {'result': result}
                    finish_trace(run, selected_output)
                except Exception as exc:
                    log.warning('LangSmith 输出选择失败（%s）', type(exc).__name__)
                return result
        return wrapped
    return decorate


__all__ = ['setup_langsmith', 'is_tracing_enabled', 'current_run_id', 'traceable',
           'task_trace', 'trace_span', 'finish_trace', 'flush_traces', 'observed']
