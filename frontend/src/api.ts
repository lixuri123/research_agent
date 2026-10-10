import type { EventRecord, MemoryRecord, ReportVersion, SessionMessage, SkillRecord, TaskDetail, TaskSummary, Workspace, WorkspaceEntry, WorkspaceFile, FileChange, FileChangeDetail, ExecutionPermission, ExecutionRecord, ProviderSettings, ProviderDraft } from './types'
import type { ProviderEntry, ProviderRegistry, MultiProviderDraft, ModelSelection } from './types'
import type { ProfileAttribute, ProfileDraft } from './types'
import type { ContextOptions } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`
    try {
      const body = await response.json()
      if (typeof body.detail === 'string') message = body.detail
      else if (Array.isArray(body.detail)) message = body.detail.map((item: { msg?: string }) => item.msg || '输入内容无效').join('；')
    } catch { /* response is not JSON */ }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export const api = {
  profile: () => request<{ person_id: string; attributes: ProfileAttribute[] }>('/profile'),
  createProfileAttribute: (body: ProfileDraft) => request<ProfileAttribute>('/profile/attributes', { method: 'POST', body: JSON.stringify(body) }),
  updateProfileAttribute: (id: string, body: ProfileDraft, version: number) => request<ProfileAttribute>(`/profile/attributes/${id}`, { method: 'PUT', body: JSON.stringify({ ...body, expected_version: version }) }),
  confirmProfileAttribute: (id: string, version: number) => request<ProfileAttribute>(`/profile/attributes/${id}/confirm`, { method: 'POST', body: JSON.stringify({ expected_version: version }) }),
  invalidateProfileAttribute: (id: string, version: number) => request<ProfileAttribute>(`/profile/attributes/${id}/invalidate`, { method: 'POST', body: JSON.stringify({ expected_version: version }) }),
  deleteProfileAttribute: (id: string, version: number) => request(`/profile/attributes/${id}?expected_version=${version}`, { method: 'DELETE' }),
  profileHistory: (id: string) => request<{ versions: ProfileAttribute[] }>(`/profile/attributes/${id}/history`),
  providers: () => request<ProviderRegistry>('/settings/providers'),
  createProvider: (body: MultiProviderDraft) => request<ProviderEntry>('/settings/providers', { method: 'POST', body: JSON.stringify(body) }),
  updateProvider: (id: string, body: MultiProviderDraft) => request<ProviderEntry>(`/settings/providers/${encodeURIComponent(id)}`, { method: 'PUT', body: JSON.stringify(body) }),
  deleteProvider: (id: string) => request<{ ok: boolean }>(`/settings/providers/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  defaultProvider: (body: ModelSelection) => request<ProviderRegistry>('/settings/providers/default/selection', { method: 'PUT', body: JSON.stringify(body) }),
  testProviderDraft: (body: MultiProviderDraft, id?: string) => request<{ ok: boolean; message: string }>('/settings/providers/test', { method: 'POST', body: JSON.stringify({ ...body, ...(id ? { provider_id: id } : {}) }) }),
  provider: () => request<ProviderSettings>('/settings/provider'),
  saveProvider: (body: ProviderDraft) => request<ProviderSettings>('/settings/provider', { method: 'PUT', body: JSON.stringify(body) }),
  testProvider: (body: ProviderDraft) => request<{ ok: boolean; message: string }>('/settings/provider/test', { method: 'POST', body: JSON.stringify(body) }),
  fileChanges: (workspace: string, run: string) => request<{ changes: FileChange[] }>(`/workspaces/${encodeURIComponent(workspace)}/changes?${new URLSearchParams({ run_id: run })}`),
  fileChange: (workspace: string, id: string) => request<FileChangeDetail>(`/workspaces/${encodeURIComponent(workspace)}/changes/${encodeURIComponent(id)}`),
  decideFileChange: (workspace: string, detail: FileChangeDetail, approved: boolean) => request<FileChange>(`/workspaces/${encodeURIComponent(workspace)}/changes/${encodeURIComponent(detail.id)}/decision`, { method: 'POST', body: JSON.stringify({ approved, review_hash: detail.review_hash }) }),
  workspaces: () => request<{ workspaces: Workspace[]; allowed_roots: string[] }>('/workspaces'),
  executionPermission: (workspace: string) => request<ExecutionPermission>(`/workspaces/${encodeURIComponent(workspace)}/execution-permission`),
  setExecutionPermission: (workspace: string, enabled: boolean) => request<ExecutionPermission>(`/workspaces/${encodeURIComponent(workspace)}/execution-permission`, { method: 'PUT', body: JSON.stringify({ enabled, accept_partial: enabled }) }),
  executions: (workspace: string, run: string) => request<{ executions: ExecutionRecord[] }>(`/workspaces/${encodeURIComponent(workspace)}/executions?${new URLSearchParams({ run_id: run })}`),
  bindWorkspace: (body: { name: string; root: string; agent_read: boolean }) => request<Workspace>('/workspaces', { method: 'POST', body: JSON.stringify(body) }),
  workspaceEntries: (id: string, path = '') => request<{ path: string; entries: WorkspaceEntry[]; truncated: boolean }>(`/workspaces/${encodeURIComponent(id)}/entries?${new URLSearchParams({ path })}`),
  workspaceFile: (id: string, path: string) => request<WorkspaceFile>(`/workspaces/${encodeURIComponent(id)}/file?${new URLSearchParams({ path })}`),
  saveWorkspaceFile: (id: string, file: WorkspaceFile, content: string) => request<WorkspaceFile>(`/workspaces/${encodeURIComponent(id)}/file`, { method: 'PUT', body: JSON.stringify({ path: file.path, version: file.version, content }) }),
  health: () => request<{ status: string }>('/health'),
  tasks: (workspaceId?: string) => request<{ tasks: TaskSummary[] }>(`/tasks?${new URLSearchParams({ limit: '100', ...(workspaceId !== undefined ? { workspace_id: workspaceId } : {}) })}`),
  task: (id: string) => request<TaskDetail>(`/tasks/${id}`),
  events: (id: string) => request<{ events: EventRecord[] }>(`/tasks/${id}/events`),
  createTask: (body: Record<string, unknown> & { context?: ContextOptions }, key = crypto.randomUUID()) => request<{ task_id: string; run_id: string }>('/tasks', { method: 'POST', body: JSON.stringify(body), headers: { 'Idempotency-Key': key } }),
  report: (id: string) => request<ReportVersion>(`/reports/${id}`),
  createResearch: (body: Record<string, unknown>, key = crypto.randomUUID()) => request<{ task_id: string; run_id: string }>('/research', { method: 'POST', body: JSON.stringify(body), headers: { 'Idempotency-Key': key } }),
  approve: (taskId: string, reportId: string, approved: boolean) => request(`/research/${taskId}/approve`, { method: 'POST', body: JSON.stringify({ approved, report_version_id: reportId }) }),
  session: (id: string) => request<{ messages: SessionMessage[] }>(`/sessions/${encodeURIComponent(id)}/messages`),
  appendMessage: (id: string, role: string, content: string) => request<SessionMessage>(`/sessions/${encodeURIComponent(id)}/messages`, { method: 'POST', body: JSON.stringify({ role, content }) }),
  memories: (status: string) => request<{ memories: MemoryRecord[] }>(`/memories?status=${encodeURIComponent(status)}`),
  searchMemories: (query: string) => request<{ memories: MemoryRecord[] }>(`/memories/search?query=${encodeURIComponent(query)}&top_k=20`),
  createMemory: (body: Record<string, unknown>) => request<MemoryRecord>('/memories', { method: 'POST', body: JSON.stringify(body) }),
  updateMemory: (id: string, body: Record<string, unknown>) => request<MemoryRecord>(`/memories/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
  activateMemory: (id: string) => request<MemoryRecord>(`/memories/${id}/activate`, { method: 'POST' }),
  invalidateMemory: (id: string) => request(`/memories/${id}/invalidate`, { method: 'POST' }),
  deleteMemory: (id: string) => request(`/memories/${id}`, { method: 'DELETE' }),
  publishSkill: (id: string, evaluationId: string, reviewedBy: string) => request(`/memories/${id}/publish-skill`, { method: 'POST', body: JSON.stringify({ evaluation_id: evaluationId, reviewed_by: reviewedBy }) }),
  skills: (projectId?: string, sessionId?: string) => request<{ skills: SkillRecord[]; errors?: string[] }>(`/skills?${new URLSearchParams({ ...(projectId ? { project_id: projectId } : {}), ...(sessionId ? { session_id: sessionId } : {}) })}`),
  readSkill: (id: string) => request<SkillRecord & { content: string }>(`/skills/${encodeURIComponent(id)}`),
  mcpTools: () => request<{ tools: Record<string, { description: string; danger_level: string; allowed: boolean; input_schema: Record<string, unknown> }>; errors: { server: string; error: string }[] }>('/mcp/tools'),
}

