"""Ordinary Agent context lifecycle: persist choice once, validate before each call."""
import re
from research_agent.context.schemas import ContextRequest, ContextOptions, RouteDecision, SourceSnapshot, ContextBudget, ContextIndex
from research_agent.context.router import decide_route
from research_agent.context.selector import select_sources
from research_agent.context.sources import ContextSources
from research_agent.context.assembler import assemble_context


class ContextSession:
    def __init__(self, record, repo, worker_id, check_lease, settings):
        payload = record['payload']
        self.request = ContextRequest(question=payload['topic'], requirements=payload.get('requirements',''),
            owner_id=record['owner_id'], session_id=payload.get('session_id'), workspace_id=payload.get('workspace_id'),
            project_id=payload.get('project_id'),
            submitted_at=record['created_at'], run_id=record['run_id'], context=ContextOptions.model_validate(payload.get('context') or {}))
        self.sources = ContextSources(repo,self.request)
        self.repo, self.worker_id, self.check_lease = repo, worker_id, check_lease
        self.budget = ContextBudget(input_limit=settings.context_input_limit, output_reserve=settings.max_output_tokens)
        self.manifest = {}
        run_id = record['run_id']
        saved = repo.get_stage(run_id, 'agent_context_sources', 'v1')
        if saved is not None:
            self.decision = RouteDecision.model_validate(saved['route'])
            self.snapshot = SourceSnapshot.model_validate(saved)
        else:
            try:
                index = self.sources.index()
                self.decision = decide_route(self.request,index)
                self.snapshot = select_sources(self.request,self.decision,self.sources)
            except Exception as exc:
                fallback = getattr(self,'decision',None) or decide_route(self.request,ContextIndex())
                dependent_action = re.match(r'\s*(请)?(继续|推进|回顾|汇报)', self.request.question)
                if fallback.route != 'independent' or fallback.needs_clarification or dependent_action:
                    self.decision = RouteDecision(route=fallback.route if fallback.route != 'independent' else 'task', rule_id='context-router/v1/source-unavailable',
                        needs_clarification=True, reason_codes=('context_unavailable',),
                        clarification='暂时无法读取所指背景，请明确任务或重述必要内容后再继续。')
                else:
                    self.decision = fallback
                self.snapshot = SourceSnapshot(warnings=['context_unavailable:' + type(exc).__name__])
            check_lease()
            # One atomic stage carries both the decision and source snapshot; recovery
            # never recomputes routing if the process exits between these two writes.
            repo.save_stage(run_id, 'agent_context_sources', 'v1',
                {**self.snapshot.model_dump(mode='json'),'route':self.decision.model_dump(mode='json')},worker_id=worker_id)
        if repo.get_stage(run_id,'agent_context_route','v1') is None:
            check_lease()
            repo.save_stage(run_id,'agent_context_route','v1',self.decision.model_dump(mode='json'),worker_id=worker_id)
            repo.record_event(run_id,worker_id,'context_selected',{'route':self.decision.model_dump(mode='json'),
                'sources':[{'kind':b.kind,'id':b.id,'version':b.version} for b in self.snapshot.blocks],
                'omitted':self.snapshot.omitted,'warnings':self.snapshot.warnings})
        self.manifest = {'route':self.decision.model_dump(mode='json'),'selected':[],
            'omitted':self.snapshot.omitted,'warnings':self.snapshot.warnings}

    def assemble(self, runtime):
        valid = self.sources.revalidate(self.snapshot)
        if self.decision.task_ids and not set(self.decision.task_ids).issubset({b.id for b in valid.blocks if b.kind == 'task'}):
            raise ValueError('所关联任务已无法读取，本次停止续接，请明确任务或重述必要内容')
        if self.decision.turn_ids and not set(self.decision.turn_ids).issubset({b.id for b in valid.blocks if b.kind in {'history','task'}}):
            raise ValueError('所引用的对话已无法读取，请重述必要内容')
        result = assemble_context(self.request,self.decision,valid,runtime,self.budget)
        changed = [item for item in result.manifest['omitted'] if item['reason'] == 'source_changed']
        previous = self.manifest.get('omitted',[])
        new = [item for item in changed if item not in previous]
        if new:
            self.check_lease()
            self.repo.record_event(self.request.run_id,self.worker_id,'context_sources_removed',{'sources':new})
        self.manifest = result.manifest
        self.check_lease()
        self.repo.save_stage(self.request.run_id,'agent_context_manifest','v1',self.manifest,worker_id=self.worker_id)
        return result.messages
