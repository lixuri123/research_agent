"""Task API; execution lives in the independently deployed Worker."""
from __future__ import annotations
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from research_agent.persistence.queue import get_repository
from research_agent.memory.base import MemoryManager, get_memory_manager
from research_agent.workspace.service import get_workspaces as workspace_service, WorkspaceConflict
from research_agent.models.provider_registry import ModelSelection
from research_agent.models.provider_settings import ProviderConfigError
from research_agent.context.schemas import ContextOptions


def task_model_selection(selection):
    return selection.model_dump() if selection is not None else None


def get_workspaces():
    return workspace_service(get_repository())


def get_file_changes():
    from research_agent.workspace.file_changes import FileChangeService
    return FileChangeService(get_workspaces())


def get_executions():
    from research_agent.execution.service import ExecutionService
    from research_agent.core.config import get_settings
    return ExecutionService(get_workspaces(), get_settings())


@asynccontextmanager
async def lifespan(app):
    # Initialize once before concurrent initial UI requests can race schema creation.
    repository = get_repository()
    get_workspaces()
    get_file_changes()
    get_executions()
    profile_routes.get_profile_store(repository)
    yield

class BudgetRequest(BaseModel):
    max_calls: int = Field(default=100, ge=1, le=1000)
    max_tokens: int = Field(default=200000, ge=100, le=2000000)
    max_documents: int = Field(default=20, ge=1, le=100)
    deadline_seconds: int = Field(default=900, ge=1, le=86400)
    require_approval: bool | None = None

class ResearchRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=4000)
    requirements: str = Field(default='', max_length=10000)
    session_id: str | None = None
    workspace_id: str | None = None
    budget: BudgetRequest | None = None
    model_selection: ModelSelection | None = None

class ApproveRequest(BaseModel):
    approved: bool
    report_version_id: str


class SessionMessageRequest(BaseModel):
    role: str
    content: str = Field(min_length=1)


class MemoryCreateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1)
    kind: str = 'semantic'
    scope: str = 'owner'
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    project_id: str | None = None
    session_id: str | None = None


class MemoryUpdateRequest(BaseModel):
    content: str = Field(min_length=1)
    source_type: str = Field(min_length=1)
    source_id: str = Field(min_length=1)


class SkillPublishRequest(BaseModel):
    evaluation_id: str = Field(min_length=1)
    reviewed_by: str = Field(min_length=1)

app = FastAPI(title='科研助手', version='0.3.0', lifespan=lifespan)


class AgentTaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=4000)
    requirements: str = Field(default='', max_length=10000)
    budget: BudgetRequest | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    session_id: str | None = None
    skill_ids: list[str] = Field(default_factory=list, max_length=20)
    authorized_mcp_tools: list[str] = Field(default_factory=list, max_length=100)
    model_selection: ModelSelection | None = None
    context: ContextOptions = Field(default_factory=ContextOptions)


@app.post('/tasks')
def submit_agent_task(req: AgentTaskRequest, idempotency_key: str | None = Header(default=None)):
    if not req.task.strip():
        raise HTTPException(422, '任务不能为空')
    if req.budget and req.budget.require_approval:
        raise HTTPException(422, '普通任务暂不支持报告审批，请使用研究模式')
    validate_workspace_session(req.session_id, req.workspace_id)
    if req.workspace_id:
        req.project_id = req.workspace_id
    if req.skill_ids:
        from research_agent.integrations.skills import SkillCatalog
        catalog = SkillCatalog(project_id=req.project_id, session_id=req.session_id)
        try:
            for skill_id in req.skill_ids:
                catalog.read(skill_id)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    if req.authorized_mcp_tools:
        from research_agent.integrations.mcp_client import MCPClient
        try:
            client = MCPClient()
            for tool in req.authorized_mcp_tools:
                parts = tool.split(':', 2)
                if len(parts) != 3 or parts[0] != 'mcp':
                    raise ValueError('授权工具名须为 mcp:server:tool')
                config = client.servers.get(parts[1])
                if config is None or not config.enabled or parts[2] not in config.allowed_tools:
                    raise ValueError('授权工具不在已启用 Server 的 allowed_tools 中')
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    selection = task_model_selection(req.model_selection)
    from research_agent.context.sources import ContextReferenceError
    repository = get_repository()
    try:
        record = repository.submit(req.task.strip(), req.requirements,
            task_type='agent', idempotency_key=idempotency_key,
            project_id=req.project_id, session_id=req.session_id, skill_ids=req.skill_ids,
            authorized_mcp_tools=req.authorized_mcp_tools,
            workspace_id=req.workspace_id,
            context=req.context.model_dump(mode='json'),
            model_selection=selection,
            budget=req.budget.model_dump(exclude_none=True) if req.budget else None)
    except ContextReferenceError as exc:
        raise HTTPException(404, str(exc)) from None
    except ProviderConfigError as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'task_id': record['task_id'], 'run_id': record['run_id']}


@app.post('/sessions/{session_id}/messages')
def append_session_message(session_id: str, req: SessionMessageRequest):
    try:
        return get_memory_manager().append_message(req.role, req.content, session_id=session_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/sessions/{session_id}/messages')
def session_messages(session_id: str, limit: int | None = None):
    try:
        manager = get_memory_manager()
        rows = manager.recent(limit, session_id=session_id) if limit is not None else manager.history(session_id=session_id)
        return {'messages': rows}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/memories')
def create_memory(req: MemoryCreateRequest):
    try:
        manager = get_memory_manager()
        return manager.remember(req.content, title=req.title, kind=req.kind, scope=req.scope,
            source_type=req.source_type, source_id=req.source_id,
            project_id=req.project_id, session_id=req.session_id)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/memories')
def list_memories(status: str = 'candidate'):
    try:
        return {'memories': get_memory_manager().list_entries(status)}
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/memories/search')
def search_memories(query: str, project_id: str | None = None, session_id: str | None = None,
                    top_k: int = 5):
    if top_k < 1 or top_k > 100:
        raise HTTPException(422, 'top_k 必须在 1 到 100 之间')
    base = get_memory_manager()
    manager = MemoryManager(store=base.store, owner_id=base.owner_id,
                            project_id=project_id, session_id=session_id)
    return {'memories': manager.recall_details(query, top_k)}


@app.post('/memories/{memory_id}/activate')
def activate_memory(memory_id: str):
    try:
        return get_memory_manager().activate(memory_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.put('/memories/{memory_id}')
def update_memory(memory_id: str, req: MemoryUpdateRequest):
    try:
        return get_memory_manager().update(memory_id, req.content,
            source_type=req.source_type, source_id=req.source_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.post('/memories/{memory_id}/invalidate')
def invalidate_memory(memory_id: str):
    try:
        get_memory_manager().invalidate(memory_id)
        return {'status': 'stale'}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post('/memories/{memory_id}/publish-skill')
def publish_skill(memory_id: str, req: SkillPublishRequest):
    try:
        return get_memory_manager().publish_procedure(memory_id,
            evaluation_id=req.evaluation_id, reviewed_by=req.reviewed_by)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@app.get('/skills')
def list_skills(project_id: str | None = None, session_id: str | None = None):
    from research_agent.integrations.skills import SkillCatalog
    catalog = SkillCatalog(project_id=project_id, session_id=session_id)
    return {'skills': catalog.list(), 'errors': catalog.errors}


@app.get('/skills/{skill_id}')
def read_skill(skill_id: str, project_id: str | None = None, session_id: str | None = None):
    from research_agent.integrations.skills import SkillCatalog
    try:
        return SkillCatalog(project_id=project_id, session_id=session_id).read(skill_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/skills/{skill_id}/reference')
def read_skill_reference(skill_id: str, path: str, project_id: str | None = None, session_id: str | None = None):
    from research_agent.integrations.skills import SkillCatalog
    try:
        return SkillCatalog(project_id=project_id, session_id=session_id).reference(skill_id, path)
    except (ValueError, OSError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc


class MCPToolCallRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)
    arguments: dict = Field(default_factory=dict)
    authorized: bool = False


def mcp_client():
    from research_agent.integrations.mcp_client import MCPClient
    try:
        return MCPClient()
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/mcp/servers')
def list_mcp_servers():
    return {'servers': mcp_client().configured()}


@app.get('/mcp/tools')
def discover_mcp_tools(server: str | None = None):
    try:
        return mcp_client().discover(server)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post('/mcp/servers/{server}/connect')
def connect_mcp_server(server: str):
    result = discover_mcp_tools(server)
    return {'server': server, 'connected': not result['errors'], **result}


@app.post('/mcp/tools/call')
def call_mcp_tool(req: MCPToolCallRequest):
    return mcp_client().call(req.name, req.arguments, authorized=req.authorized)


@app.delete('/memories/{memory_id}')
def delete_memory(memory_id: str):
    try:
        get_memory_manager().forget(memory_id)
        return {'deleted': True}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get('/tasks')
@app.get('/research')
def list_research(limit: int = 50, offset: int = 0, workspace_id: str | None = None):
    if limit < 1 or limit > 100:
        raise HTTPException(422, 'limit 必须在 1 到 100 之间')
    if offset < 0:
        raise HTTPException(422, 'offset 不能小于 0')
    tasks = []
    for row in get_repository().list_runs(limit=limit, offset=offset, workspace_id=workspace_id):
        payload = row.get('payload') or {}
        tasks.append({
            'task_id': row['task_id'],
            'run_id': row['run_id'],
            'status': row['status'],
            'topic': payload.get('topic', ''),
            'session_id': payload.get('session_id'),
            **({'model_selection': payload['model_selection']} if payload.get('model_selection') else {}),
            'current_report_version_id': row.get('current_report_version_id'),
            'created_at': row['created_at'],
            **({'workspace_id': payload['workspace_id']} if payload.get('workspace_id') else {}),
        })
    return {'tasks': tasks}

@app.post('/research')
def submit_research(req: ResearchRequest, idempotency_key: str | None = Header(default=None)):
    if not req.topic.strip():
        raise HTTPException(422, '研究主题不能为空')
    validate_workspace_session(req.session_id, req.workspace_id)
    selection = task_model_selection(req.model_selection)
    try:
        record = get_repository().submit(req.topic, req.requirements, session_id=req.session_id,
            workspace_id=req.workspace_id,
            model_selection=selection,
            idempotency_key=idempotency_key, budget=req.budget.model_dump(exclude_none=True) if req.budget else None)
    except ProviderConfigError as exc:
        raise HTTPException(422, str(exc)) from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'task_id':record['task_id'], 'run_id':record['run_id']}

@app.get('/tasks/{task_id}')
@app.get('/research/{task_id}')
def get_research(task_id: str):
    record = get_repository().get(task_id)
    if record is None:
        raise HTTPException(404, '任务不存在')
    return {k:record[k] for k in ('task_id','run_id','status','result','current_report_version_id')}

@app.post('/research/{task_id}/approve')
def approve_research(task_id: str, req: ApproveRequest):
    repo = get_repository()
    record = repo.get(task_id)
    if record is None:
        raise HTTPException(404, '任务不存在')
    if record['current_report_version_id'] != req.report_version_id:
        raise HTTPException(409, '审批版本不匹配')
    try:
        row = repo.approve(req.report_version_id, req.approved)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {'task_id':task_id, 'run_id':row['run_id'], 'status':row['status'], 'approved':req.approved}

@app.get('/reports/{report_version_id}')
def get_report(report_version_id: str):
    report = get_repository().get_report(report_version_id)
    if report is None:
        raise HTTPException(404, '报告不存在')
    return report

@app.get('/tasks/{task_id}/events')
@app.get('/research/{task_id}/events')
def get_events(task_id: str):
    repo = get_repository()
    record = repo.get(task_id)
    if record is None:
        raise HTTPException(404, '任务不存在')
    return {'events':repo.events(record['run_id'])}

@app.get('/health')
def health():
    # A health probe also verifies access to the authoritative schema.
    repo = get_repository()
    from sqlalchemy import text
    with repo.engine.connect() as conn:
        conn.execute(text('SELECT 1'))
    return {'status':'ok'}


def validate_workspace_session(session_id, workspace_id):
    try:
        get_workspaces().associate(session_id, workspace_id)
    except WorkspaceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


class WorkspaceRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    root: str = Field(min_length=1, max_length=4096)
    agent_read: bool = False


class WorkspaceSaveRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    content: str = Field(max_length=128 * 1024)
    version: str = Field(min_length=1, max_length=64)


def workspace_action(action):
    try:
        return action()
    except WorkspaceConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get('/workspaces')
def list_workspaces():
    service = get_workspaces()
    return {'workspaces': service.list(), 'allowed_roots': [str(root) for root in service.allowed_roots]}


@app.post('/workspaces')
def bind_workspace(req: WorkspaceRequest):
    return workspace_action(lambda: get_workspaces().bind(req.name, req.root, req.agent_read))


@app.get('/workspaces/{workspace_id}/entries')
def workspace_entries(workspace_id: str, path: str = ''):
    return workspace_action(lambda: get_workspaces().entries(workspace_id, path))


@app.get('/workspaces/{workspace_id}/file')
def workspace_file(workspace_id: str, path: str):
    return workspace_action(lambda: get_workspaces().read(workspace_id, path))


@app.put('/workspaces/{workspace_id}/file')
def save_workspace_file(workspace_id: str, req: WorkspaceSaveRequest):
    return workspace_action(lambda: get_workspaces().save(workspace_id, req.path, req.content, req.version))


class FileChangeDecision(BaseModel):
    approved: bool
    review_hash: str = Field(min_length=64, max_length=64)


@app.get('/workspaces/{workspace_id}/changes')
def list_file_changes(workspace_id: str, run_id: str | None = None):
    return workspace_action(lambda: {'changes': get_file_changes().list(workspace_id, run_id)})


@app.get('/workspaces/{workspace_id}/changes/{change_id}')
def file_change_detail(workspace_id: str, change_id: str):
    return workspace_action(lambda: get_file_changes().get(workspace_id, change_id))


@app.post('/workspaces/{workspace_id}/changes/{change_id}/decision')
def decide_file_change(workspace_id: str, change_id: str, req: FileChangeDecision):
    return workspace_action(lambda: get_file_changes().decide(workspace_id, change_id, req.approved, req.review_hash))


class ExecutionPermissionRequest(BaseModel):
    enabled: bool
    accept_partial: bool = False


@app.get('/workspaces/{workspace_id}/execution-permission')
def execution_permission(workspace_id: str):
    return workspace_action(lambda: get_executions().permission(workspace_id))


@app.put('/workspaces/{workspace_id}/execution-permission')
def set_execution_permission(workspace_id: str, req: ExecutionPermissionRequest):
    if req.enabled and not req.accept_partial:
        raise HTTPException(422, '须明确接受 Windows 部分写限制，不隔离读取及网络')
    return workspace_action(lambda: get_executions().set_permission(workspace_id, req.enabled))


@app.get('/workspaces/{workspace_id}/executions')
def list_executions(workspace_id: str, run_id: str):
    return workspace_action(lambda: {'executions': get_executions().list(workspace_id, run_id)})


from research_agent.api_routes.providers import router as provider_router, providers_router
app.include_router(provider_router)
app.include_router(providers_router)
from research_agent.api_routes import profile as profile_routes
app.include_router(profile_routes.router)

from research_agent.remote.local_routes import router as remote_local_router
app.include_router(remote_local_router)

frontend_dist = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
if frontend_dist.is_dir():
    app.mount('/', StaticFiles(directory=frontend_dist, html=True), name='workbench')

