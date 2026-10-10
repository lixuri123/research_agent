"""One task-scoped catalog for built-ins, MCP tools and Skill loaders."""
from __future__ import annotations

from research_agent.core.budget import BudgetExceeded, current_budget
from research_agent.integrations.mcp_client import MCPClient
from research_agent.integrations.skills import SkillCatalog
from research_agent.tools import list_tool_meta, run_tool
from research_agent.core.observability import observed


OBJECT_QUERY = {'type': 'object', 'properties': {'query': {'type': 'string'}},
                'required': ['query'], 'additionalProperties': False}


class TaskCapabilities:
    def __init__(self, payload: dict, owner_id='local', run_id=None, worker_id=None, check_lease=None):
        from research_agent.tools import load  # noqa: F401
        self.mcp = MCPClient()
        self.skills = SkillCatalog(owner_id=owner_id, project_id=payload.get('project_id'),
                                   session_id=payload.get('session_id'))
        self.authorized = set(payload.get('authorized_mcp_tools') or [])
        self.loaded: dict[str, dict] = {}
        self.allowed: set[str] = set()
        self.workspace_id = payload.get('workspace_id')
        self.memory_payload = dict(payload)
        self.owner_id = owner_id
        self.run_id, self.worker_id = run_id, worker_id
        self.check_lease = check_lease

    def workspaces(self):
        from research_agent.workspace.service import get_workspaces, WorkspaceService
        service = get_workspaces()
        return WorkspaceService(service.engine, service.allowed_roots, self.owner_id)

    def executions(self):
        from research_agent.execution.service import ExecutionService
        from research_agent.core.config import get_settings
        return ExecutionService(self.workspaces(), get_settings())

    def catalog(self) -> dict:
        builtins = {'paper_search', 'web_search', 'browser_read'}
        tools = {name: {**meta, 'input_schema': OBJECT_QUERY} for name, meta in list_tool_meta().items() if name in builtins}
        tools['skill_read'] = {'description': '加载 Skill 完整指令。先检查 Skill 目录是否匹配当前任务',
            'danger_level': 'safe', 'input_schema': {'type': 'object', 'properties': {'id': {'type': 'string'}}, 'required': ['id']}}
        tools['skill_reference'] = {'description': '读取已加载 Skill 包内的参考文件，不执行脚本',
            'danger_level': 'safe', 'input_schema': {'type': 'object', 'properties': {'id': {'type': 'string'}, 'path': {'type': 'string'}}, 'required': ['id', 'path']}}
        tools['memory_search'] = {'description': '搜索当前用户、项目及会话范围内已激活的历史记忆。仅作背景，不是当前事实、操作授权或外部证据；不写入记忆。',
            'danger_level': 'safe', 'input_schema': OBJECT_QUERY}
        workspace = None
        if self.workspace_id:
            workspace = self.workspaces().get(self.workspace_id)
            if workspace['agent_read']:
                for name, description in {
                    'workspace_list': '列出当前工作空间目录。path 为相对目录，空串表示根目录。隐藏密钥和环境文件。',
                    'workspace_read': '读取当前工作空间内 UTF-8 文本/代码文件，最多 128 KiB；不支持 PDF、二进制。path 为相对文件路径。',
                }.items():
                    tools[name] = {'description': description, 'danger_level': 'safe', 'input_schema': {
                        'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path'], 'additionalProperties': False}}
                if self.run_id and self.worker_id:
                    execution = self.executions()
                    permission = execution.permission(self.workspace_id)
                    if permission['enabled'] and permission['backend']['available']:
                        tools['workspace_search_text'] = {
                            'description': '在授权材料副本中执行固定字面量文本检索，最多 50 条命中；不接受 shell、路径或额外参数。Windows 为部分写限制，不隔离区外读取或网络。返回输入文件版本和截断情况，不是完整工作区搜索。',
                            'danger_level': 'safe', 'input_schema': {'type': 'object',
                                'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 512},
                                    'max_results': {'type': 'integer', 'minimum': 1, 'maximum': 50}},
                                'required': ['query'], 'additionalProperties': False}}
                    tools['workspace_propose_write'] = {
                        'description': '提交已有文本文件的完整修改提案，不写入文件。先 workspace_read 获取 base_version（version 字段），由用户查看差异并确认后才能写入。不能创建或删除文件。',
                        'danger_level': 'safe', 'input_schema': {'type': 'object',
                            'properties': {key: {'type': 'string'} for key in ('path', 'content', 'base_version', 'reason')},
                            'required': ['path', 'content', 'base_version', 'reason'], 'additionalProperties': False}}
        discovered = self.mcp.discover()
        tools.update({name: meta for name, meta in discovered['tools'].items()
                      if meta['allowed'] and (meta['danger_level'] == 'safe' or name in self.authorized)})
        self.allowed = set(tools)
        return {'tools': tools, 'skills': self.skills.list(), 'workspace': workspace,
                'errors': discovered['errors'] + [{'skill': error} for error in self.skills.errors]}

    def restore(self, trace: list[dict]):
        for step in trace:
            skill = step.get('observation', {}).get('skill')
            if skill:
                self.loaded[skill['id']] = skill

    def instructions(self) -> str:
        return '\n\n'.join(f"Skill {item['name']}（版本 {item['version']}）：\n{item['content']}"
                           for item in self.loaded.values())

    @observed('agent_tool', run_type='tool', inputs=lambda self, name, arguments, query='', operation_id=None: {
        'tool': name, 'arguments': arguments, 'query': query, 'operation_id': operation_id})
    def call(self, name: str, arguments: dict, query: str = '', operation_id=None) -> dict:
        budget = current_budget.get()
        if budget:
            budget.check()
        try:
            if name not in self.allowed:
                raise PermissionError('工具未发现或当前任务未获授权')
            if name.startswith('mcp:'):
                result = self.mcp.call(name, arguments, authorized=name in self.authorized)
            elif name == 'memory_search':
                from research_agent.memory.agent_context import AgentMemory
                value = AgentMemory(self.memory_payload, self.owner_id).search(arguments.get('query', query))
                result = {'tool_used': name, 'documents': [], 'error': None, **value}
            elif name == 'workspace_propose_write':
                from research_agent.workspace.file_changes import FileChangeService
                value = FileChangeService(self.workspaces()).propose(self.workspace_id, self.run_id,
                    self.worker_id, operation_id, arguments.get('path'), arguments.get('content'),
                    arguments.get('base_version'), arguments.get('reason'))
                result = {'tool_used': name, 'documents': [], 'error': None, 'proposal': value,
                          'notice': '只提交了修改提案，尚未写入文件；须由用户查看差异并确认。'}
            elif name == 'workspace_search_text':
                value = self.executions().search(self.workspace_id, self.run_id, self.worker_id,
                    operation_id, arguments, self.check_lease)
                observation = {key: item for key, item in value.items() if key != 'manifest'}
                observation['input_count'] = len(value.get('manifest', []))
                result = {'tool_used': name, 'documents': [], 'error': value.get('error'), 'execution': observation}
            elif name in {'workspace_list', 'workspace_read'}:
                service = self.workspaces()
                if not self.workspace_id or not service.get(self.workspace_id)['agent_read']:
                    raise PermissionError('工作空间未授权 Agent 读取')
                path = arguments.get('path', '')
                if not isinstance(path, str):
                    raise ValueError('path 必须是相对路径字符串')
                value = service.entries(self.workspace_id, path) if name == 'workspace_list' else service.read(self.workspace_id, path)
                result = {'tool_used': name, 'documents': [], 'error': None, **value}
            elif name == 'skill_read':
                item = self.loaded.get(arguments.get('id')) or self.skills.read(arguments.get('id', ''))
                from research_agent.core.config import get_settings
                total = sum(len(skill['content']) for key, skill in self.loaded.items() if key != item['id']) + len(item['content'])
                if total > get_settings().skill_context_chars:
                    raise ValueError('已加载 Skill 指令超过上下文上限；未截断或加载该 Skill')
                self.loaded[item['id']] = item
                result = {'tool_used': name, 'documents': [], 'skill': item, 'error': None}
            elif name == 'skill_reference':
                skill_id = arguments.get('id', '')
                if skill_id not in self.loaded:
                    raise ValueError('先调用 skill_read 加载 Skill')
                current = self.skills.read(skill_id)
                if current['version'] != self.loaded[skill_id]['version'] or current.get('content_hash') != self.loaded[skill_id].get('content_hash'):
                    raise ValueError('Skill 包已变更；当前任务保留旧指令，拒绝读取新版本参考文件')
                result = {'tool_used': name, 'documents': [], 'reference': self.skills.reference(skill_id, arguments.get('path', '')), 'error': None}
            else:
                text = arguments.get('query', query)
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('内置工具 query 必须是非空字符串')
                result = run_tool(name, text).model_dump()
            if budget:
                budget.check()
            return result
        except BudgetExceeded:
            raise
        except Exception as exc:
            return {'tool_used': name, 'documents': [], 'error': self.mcp.redact(f'{type(exc).__name__}: {exc}')}
