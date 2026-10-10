"""Independent durable research worker. Run with python -m research_agent.worker."""
from __future__ import annotations
import argparse
import logging
import threading
import time
import uuid
from langgraph.types import Command
from research_agent.research import graph as workflow
from research_agent.core.budget import Budget, budget_scope
from research_agent.core.config import get_settings, model_settings_scope
from research_agent.models.provider_registry import selection_overrides
from research_agent.persistence.queue import get_repository
from research_agent.core.observability import task_trace, finish_trace, flush_traces

log = logging.getLogger(__name__)

class Worker:
    def __init__(self, repository=None, graph=None):
        self.repository = repository or get_repository()
        self.graph = graph or workflow.graph
        self.worker_id = uuid.uuid4().hex

    def run_once(self):
        repo = self.repository
        lease = get_settings().worker_lease_seconds
        record = repo.claim(self.worker_id, lease)
        if not record:
            return False
        run_id = record['run_id']
        stopped, lost = threading.Event(), threading.Event()
        def heartbeat():
            while not stopped.wait(max(0.05, lease / 3)):
                try:
                    repo.heartbeat(run_id, self.worker_id, lease)
                except Exception:
                    lost.set()
                    return
        def check_lease():
            row = repo.get_run(run_id)
            if lost.is_set() or row['worker_id'] != self.worker_id or row['status'] != 'running' or row['lease_expires_at'] <= time.time():
                raise ValueError('执行租约失效，停止提交产物')
        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            # Start renewal before any tracing initialization or network activity.
            with task_trace(record, self.worker_id) as trace:
                if trace is not None:
                    try:
                        repo.record_event(run_id, self.worker_id, 'langsmith_trace_started',
                                          {'trace_id': str(trace.id), 'project': trace.session_name})
                    except Exception as exc:
                        log.warning('追踪关联事件保存失败（%s）', type(exc).__name__)
                result = self._execute_claimed(record, check_lease)
                if trace is not None:
                    try:
                        row = repo.get_run(run_id)
                        output = row.get('result') or {}
                        finish_trace(trace, {'status': row['status'],
                                             'content': output.get('content') or output.get('error')
                                             or (output.get('final_report') or {}).get('content', '')})
                    except Exception as exc:
                        log.warning('追踪结果读取失败（%s）', type(exc).__name__)
                return result
        finally:
            stopped.set()
            thread.join(timeout=2)

    def _execute_claimed(self, record, check_lease):
        repo = self.repository
        run_id = record['run_id']
        repo_token = workflow.runtime_repository.set(repo)
        worker_token = workflow.runtime_worker_id.set(self.worker_id)
        lease_token = workflow.runtime_lease_check.set(check_lease)
        config = {'configurable': {'thread_id': run_id}, 'recursion_limit': 1000}
        model_scope = None
        try:
            overrides = selection_overrides(record['payload'].get('model_selection'))
            model_scope = model_settings_scope(overrides)
            model_scope.__enter__()
            if record['payload'].get('task_type') == 'agent':
                from research_agent.agent.runner import execute_task
                execute_task(record, repo, self.worker_id, check_lease)
                return True
            snap = self.graph.get_state(config)
            payload = record['payload']
            requested = payload.get('budget') or {}
            initial = workflow.initial_state(payload['topic'], payload.get('requirements',''),
                payload.get('session_id'), run_id=run_id, require_approval=requested.get('require_approval'))
            initial['task_id'] = record['task_id']
            initial['owner_id'] = record['owner_id']
            # Deadline is tied to acceptance, not reset by worker recreation.
            initial['budget'].update({k:v for k,v in requested.items() if k != 'require_approval'})
            initial['budget']['deadline'] = record['created_at'] + requested.get('deadline_seconds', get_settings().task_deadline_seconds)
            usage = repo.get_stage(run_id, 'budget', 'v1') or {'calls':0, 'tokens':0}
            def save_budget(value):
                check_lease()
                repo.save_stage(run_id, 'budget', 'v1', value, worker_id=self.worker_id)
            budget = Budget(initial['budget'], usage=usage, save=save_budget)
            with budget_scope(budget):
                if snap.values:
                    if any(getattr(t, 'interrupts', ()) for t in snap.tasks):
                        approval = record.get('approval')
                        if not approval or not approval['approved']:
                            raise ValueError('中断执行缺少有效版本审批')
                        result = self.graph.invoke(Command(resume={'approved':True}), config)
                    elif snap.next:
                        result = self.graph.invoke(None, config)
                    else:
                        result = snap.values
                else:
                    if record.get('approval'):
                        raise ValueError('审批恢复所需检查点缺失')
                    result = self.graph.invoke(initial, config)
            check_lease()
            waiting = any(getattr(t, 'interrupts', ()) for t in self.graph.get_state(config).tasks)
            result = workflow.serialize(result)
            result['budget_usage'] = budget.usage
            status = 'waiting_approval' if waiting else result.get('status', 'failed')
            result['status'] = status
            draft = result.get('draft')
            if draft and not record.get('approval'):
                repo.save_report(run_id, draft, worker_id=self.worker_id)
            if status == 'completed' and record.get('approval'):
                approved = repo.get_report(record['approval']['report_version_id'], owner_id=record['owner_id'])
                if not approved or approved['report'] != draft:
                    raise ValueError('完成报告与批准版本不一致')
            repo.finish(run_id, self.worker_id, status, result)
            if status == 'completed':
                # Completion is committed before derived memory; failure cannot undo it.
                try:
                    repo.save_stage(run_id, 'verified_conclusion', 'v1', {
                        'report_version_id':repo.get_run(run_id)['current_report_version_id'],
                        'evidence_ids':[e['id'] for e in result.get('evidence', [])],
                        'owner_id':record['owner_id']})
                except Exception:
                    log.exception('已发布报告的结论索引写入失败，可独立重建')
        except Exception as exc:
            log.exception('研究执行失败 run_id=%s', run_id)
            try:
                check_lease()
                repo.finish(run_id, self.worker_id, 'failed', {'error':str(exc), 'final_report':None})
            except Exception:
                log.warning('无法提交失败状态，租约到期后由后续 Worker 恢复 run_id=%s', run_id)
        finally:
            if model_scope is not None:
                model_scope.__exit__(None, None, None)
            workflow.runtime_repository.reset(repo_token)
            workflow.runtime_worker_id.reset(worker_token)
            workflow.runtime_lease_check.reset(lease_token)
        return True


def main():
    parser = argparse.ArgumentParser(description='持久化科研任务 Worker')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    worker = Worker()
    try:
        if args.once:
            worker.run_once()
            return
        while True:
            if not worker.run_once():
                time.sleep(get_settings().worker_poll_seconds)
    finally:
        flush_traces()

if __name__ == '__main__':
    main()
