export type TaskStatus = 'queued' | 'running' | 'waiting_approval' | 'completed' | 'partial' | 'insufficient_evidence' | 'rejected' | 'failed' | 'cancelled'

export interface TaskSummary {
  task_id: string
  run_id: string
  status: TaskStatus
  topic: string
  session_id: string | null
  workspace_id?: string | null
  model_selection?: ModelSelection | null
  current_report_version_id: string | null
  created_at: number
}

export interface Citation { index: number; source_id: string; text?: string; evidence_ids?: string[] }
export interface Claim { id: string; text: string; kind: string; verification: string; evidence_ids: string[] }
export interface Evidence { id: string; document_id: string; text: string; scope: string; source_url?: string }
export interface ReportBody { title?: string; content: string; citations?: Citation[]; claims?: Claim[]; validation_errors?: string[] }
export interface ReportVersion { report_version_id: string; report: ReportBody; created_at: number; content_hash: string }
export interface TaskDetail extends Omit<TaskSummary, 'topic' | 'session_id' | 'created_at'> {
  result: null | Record<string, unknown> & { draft?: ReportBody; final_report?: ReportBody; evidence?: Evidence[]; budget_usage?: { calls?: number; tokens?: number }; errors?: string[]; error?: string; plan?: string; content?: string; documents?: { id: string; title: string; url: string; content: string; abstract: string }[]; loaded_skills?: SkillRecord[]; capability_errors?: Record<string, string>[]; trace?: { decision: { tool?: string }; observation?: Record<string, unknown> }[] }
}
export interface EventRecord { event_id: string; event: string; created_at: number; data?: Record<string, unknown> }
export interface SessionMessage { id: number; role: string; content: string; created_at: number; session_id: string }
export interface MemoryRecord { id: string; title: string; content?: string; kind: string; scope: string; status: string; source_type?: string; source_id?: string; project_id?: string | null; session_id?: string | null; updated_at?: number; [key: string]: unknown }

export type JsonValue = string | number | boolean | null | JsonValue[] | { [key: string]: JsonValue }
export interface ProfileDraft {
  path: string[]
  value: JsonValue
  basis: 'user_stated' | 'user_confirmed' | 'inferred'
  source_refs: { type: string; id: string }[]
  scope: 'person' | 'project' | 'session'
  scope_id: string | null
}
export interface ProfileAttribute extends ProfileDraft {
  id: string
  status: 'candidate' | 'active' | 'stale'
  version: number
  created_at: number
  updated_at: number
}
export interface SkillRecord { id?: string; name?: string; title?: string; version?: string | number; path?: string; description?: string; source?: string; content?: string; [key: string]: unknown }

export interface Workspace { id: string; name: string; root: string; agent_read: boolean; created_at: number }
export interface WorkspaceEntry { name: string; path: string; kind: 'directory' | 'file'; text: boolean }
export interface WorkspaceFile { path: string; content: string; version: string; size: number }
export interface FileChange { id: string; workspace_id: string; run_id: string; path: string; reason: string; base_version: string; review_hash: string; status: 'pending' | 'applying' | 'applied' | 'rejected' | 'conflict'; created_at: number }
export interface FileChangeDetail extends FileChange { original_content: string; proposed_content: string; diff: string }

export interface ExecutionPermission { enabled: boolean; agent_read: boolean; backend: { available: boolean; backend: string; enforcement: string; file_read: string; network: string; error: string | null } }
export interface ExecutionRecord { id: string; status: string; arguments: { query: string; max_results: number }; created_at: number; manifest: { path: string; version: string }[]; result: null | { error?: string | null; truncated?: boolean; preparation?: { truncated: boolean; skipped: number }; matches: { path: string; version: string; line: number; text: string }[] } }
export interface ProviderSettings {
  base_url: string
  model: string
  has_api_key: boolean
  planner_model: string
  reviewer_model: string
  writer_model: string
  summary_model: string
}

export type ProviderDraft = Omit<ProviderSettings, 'has_api_key'> & { api_key: string }

export interface ProviderEntry extends ProviderSettings { id: string; revision: string; name: string; models: string[] }
export interface ProviderRegistry { providers: ProviderEntry[]; active_provider_id: string | null }
export type MultiProviderDraft = ProviderDraft & { name: string; models: string[] }
export interface ModelSelection { provider_id: string; model: string }
export type ContextOptions = {
  mode?: 'auto' | 'independent' | 'followup' | 'task' | 'multi_task'
  task_ids?: string[]
  profile_ids?: string[]
  turn_ids?: string[]
}
