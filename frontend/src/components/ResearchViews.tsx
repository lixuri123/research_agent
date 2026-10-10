import { useEffect, useState } from 'react'
import { AlertTriangle, ArrowRight, BookOpen, Check, Clock3, ExternalLink, FileSearch, LoaderCircle, RotateCw, Send, ShieldCheck, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import type { EventRecord, ReportBody, ReportVersion, SkillRecord, TaskDetail, TaskSummary } from '../types'

const terminal = new Set(['completed', 'partial', 'insufficient_evidence', 'rejected', 'failed', 'cancelled'])
const labels: Record<string, string> = { queued: '排队中', running: '执行中', waiting_approval: '待审批', completed: '已完成', partial: '部分完成', insufficient_evidence: '证据不足', rejected: '已拒绝', failed: '失败', cancelled: '已取消' }

function NewResearch({ afterCreate, notify }: { afterCreate: (id: string) => Promise<void>; notify: (message: string) => void }) {
  const [taskMode, setTaskMode] = useState<'agent' | 'research'>('agent')
  const [topic, setTopic] = useState('')
  const [requirements, setRequirements] = useState('')
  const [sessionId, setSessionId] = useState('')
  const [projectId, setProjectId] = useState('')
  const [skills, setSkills] = useState<SkillRecord[]>([])
  const [skillIds, setSkillIds] = useState<string[]>([])
  const [mcpTools, setMcpTools] = useState<Record<string, { description: string; danger_level: string; allowed: boolean }>>({})
  const [authorizedTools, setAuthorizedTools] = useState<string[]>([])
  const [discovering, setDiscovering] = useState(false)
  const [approval, setApproval] = useState(true)
  const [advanced, setAdvanced] = useState(false)
  const [budget, setBudget] = useState({ max_calls: 100, max_tokens: 200000, max_documents: 20, deadline_seconds: 900 })
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let active = true
    api.skills(projectId.trim(), sessionId.trim()).then((data) => {
      if (!active) return
      setSkills(data.skills)
      setSkillIds((ids) => ids.filter((id) => data.skills.some((skill) => skill.id === id)))
    }).catch((error) => { if (active) notify(error instanceof Error ? error.message : 'Skill 读取失败') })
    return () => { active = false }
  }, [projectId, sessionId, notify])

  async function discoverTools() {
    setDiscovering(true)
    try {
      const data = await api.mcpTools()
      setMcpTools(data.tools)
      setAuthorizedTools((tools) => tools.filter((name) => data.tools[name]?.allowed))
      if (data.errors.length) notify(data.errors.map((error) => `${error.server}: ${error.error}`).join('；'))
      else if (!Object.keys(data.tools).length) notify('没有可用 MCP 工具，请检查 config/mcp.json 的 enabled 与 allowed_tools')
    } catch (error) { notify(error instanceof Error ? error.message : 'MCP 发现失败') }
    finally { setDiscovering(false) }
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!topic.trim()) return
    setSubmitting(true)
    try {
      const result = taskMode === 'agent'
        ? await api.createTask({ task: topic.trim(), requirements: requirements.trim(), project_id: projectId.trim() || null, session_id: sessionId.trim() || null, skill_ids: skillIds, authorized_mcp_tools: authorizedTools, budget: { ...budget, require_approval: false } })
        : await api.createResearch({ topic: topic.trim(), requirements: requirements.trim(), session_id: sessionId.trim() || null, budget: { ...budget, require_approval: approval } })
      notify(`任务已提交：${result.task_id.slice(0, 8)}`)
      setTopic(''); setRequirements(''); await afterCreate(result.task_id)
    } catch (error) { notify(error instanceof Error ? error.message : '提交失败') }
    finally { setSubmitting(false) }
  }

  return <div className="page new-research-page">
    <header className="page-heading"><div><p className="eyebrow">NEW TASK</p><h1>交给 Agent 一个任务</h1><p>写下目标与交付要求，Agent 会选择搜索、网页或 PDF 阅读工具完成任务。</p></div></header>
    <form className="research-form paper-panel" onSubmit={submit}>
      <label className="field"><span>执行模式</span><select value={taskMode} onChange={(e) => setTaskMode(e.target.value as 'agent' | 'research')}><option value="agent">普通 Agent 任务 · 自主选择工具</option><option value="research">研究报告 · 原有证据审核流程</option></select></label>
      <label className="field major-field"><span>任务目标 <b>必填</b></span><textarea autoFocus value={topic} onChange={(e) => setTopic(e.target.value)} placeholder="例如：找 5 篇与 RAG 可靠性相关的论文，阅读可访问的正文，整理方法、局限和来源链接" rows={3} maxLength={4000} /><small>{topic.length} / 4000</small></label>
      <div className="rule" />
      <label className="field"><span>补充要求</span><textarea value={requirements} onChange={(e) => setRequirements(e.target.value)} placeholder="限定年份、来源类型、比较维度或输出结构……" rows={5} maxLength={10000} /></label>
      {taskMode === 'agent' && <section className="capability-panel">
        <div className="form-grid"><label className="field"><span>项目 ID</span><input value={projectId} onChange={(e) => setProjectId(e.target.value)} placeholder="可选，匹配项目范围 Skill" /></label><label className="field"><span>会话 ID</span><input value={sessionId} onChange={(e) => setSessionId(e.target.value)} placeholder="可选，匹配会话范围 Skill" /></label></div>
        <h3>任务 Skill</h3><p className="muted">未指定时 Agent 会按目录自行选择；勾选后在任务开始时加载完整指令。</p>
        {skills.length ? skills.map((skill) => <label className="capability-option" key={skill.id}><input type="checkbox" checked={skillIds.includes(skill.id!)} onChange={(e) => setSkillIds((ids) => e.target.checked ? [...ids, skill.id!] : ids.filter((id) => id !== skill.id))} /><span><b>{skill.name || skill.title}</b><small>{skill.description}</small></span></label>) : <p className="muted">当前范围暂无 Skill，可在配置的 skills 目录放入 SKILL.md 或发布程序记忆。</p>}
        <div className="form-actions"><h3>MCP 工具</h3><button type="button" className="secondary" disabled={discovering} onClick={() => void discoverTools()}>{discovering ? <LoaderCircle className="spin" size={16} /> : <RotateCw size={16} />}发现工具</button></div>
        <p className="muted">已配置的只读工具自动可用。勾选仅授权当前任务调用对应写入或未知权限工具，不会解除配置白名单。</p>
        {Object.entries(mcpTools).map(([name, tool]) => !tool.allowed ? <div className="capability-option muted" key={name}><span><b>{name} · 未加入配置白名单</b><small>{tool.description}</small></span></div> : tool.danger_level === 'safe' ? <div className="capability-option" key={name}><ShieldCheck size={16} /><span><b>{name} · 只读</b><small>{tool.description}</small></span></div> : <label className="capability-option" key={name}><input type="checkbox" checked={authorizedTools.includes(name)} onChange={(e) => setAuthorizedTools((tools) => e.target.checked ? [...tools, name] : tools.filter((item) => item !== name))} /><span><b>授权 {name}</b><small>{tool.description}</small></span></label>)}
      </section>}
      {taskMode === 'research' && <div className="form-grid"><label className="field"><span>会话 ID</span><input value={sessionId} onChange={(e) => setSessionId(e.target.value)} placeholder="可选，关联任务记录；不自动召回历史" /></label><label className="toggle-field"><input type="checkbox" checked={approval} onChange={(e) => setApproval(e.target.checked)} /><span className="toggle" /><span><b>完成前人工审批</b><small>报告通过你的确认后再发布</small></span></label></div>}
      <button type="button" className="text-button advanced-toggle" onClick={() => setAdvanced((value) => !value)}>{advanced ? '收起' : '展开'}研究预算</button>
      {advanced && <div className="budget-grid">{[
        ['max_calls', '模型调用', 1, 1000], ['max_tokens', 'Token 上限', 100, 2000000], ['max_documents', '文献数量', 1, 100], ['deadline_seconds', '时限（秒）', 1, 86400],
      ].map(([key, label, min, max]) => <label className="field" key={key}><span>{label}</span><input type="number" min={Number(min)} max={Number(max)} value={budget[key as keyof typeof budget]} onChange={(e) => setBudget({ ...budget, [key]: Number(e.target.value) })} /></label>)}</div>}
      <div className="form-actions"><p><ShieldCheck size={16} /> 保留执行记录、来源与交付结果</p><button className="primary" disabled={submitting || !topic.trim()}>{submitting ? <LoaderCircle className="spin" /> : <Send />}提交任务</button></div>
    </form>
  </div>
}

function TaskInspector({ summary, notify, refresh }: { summary: TaskSummary; notify: (message: string) => void; refresh: () => Promise<void> }) {
  const [task, setTask] = useState<TaskDetail | null>(null)
  const [events, setEvents] = useState<EventRecord[]>([])
  const [report, setReport] = useState<ReportVersion | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let active = true
    let timer: number | undefined
    let reportId: string | null = null
    async function load() {
      try {
        const [detail, timeline] = await Promise.all([api.task(summary.task_id), api.events(summary.task_id)])
        if (!active) return
        setTask(detail); setEvents(timeline.events)
        if (detail.current_report_version_id && detail.current_report_version_id !== reportId) {
          const version = await api.report(detail.current_report_version_id)
          if (!active) return
          reportId = detail.current_report_version_id
          setReport(version)
        }
        if (terminal.has(detail.status) || detail.status === 'waiting_approval') {
          if (detail.status !== summary.status) void refresh()
        } else if (active) timer = window.setTimeout(load, 2000)
      } catch (error) { if (active) { notify(error instanceof Error ? error.message : '任务读取失败'); timer = window.setTimeout(load, 4000) } }
      finally { if (active) setLoading(false) }
    }
    void load()
    return () => { active = false; if (timer !== undefined) window.clearTimeout(timer) }
  }, [summary.task_id, summary.status, notify, refresh])

  async function decide(approved: boolean) {
    if (!task?.current_report_version_id) return
    if (!approved && !window.confirm('确认拒绝当前报告？该版本不会发布。')) return
    try { await api.approve(task.task_id, task.current_report_version_id, approved); notify(approved ? '报告已批准，Worker 将继续完成发布' : '报告已拒绝'); await refresh() }
    catch (error) { notify(error instanceof Error ? error.message : '审批失败') }
  }

  if (loading) return <div className="detail-loading"><LoaderCircle className="spin" />正在读取研究现场…</div>
  const body: ReportBody | undefined = report?.report || task?.result?.final_report || task?.result?.draft || (task?.result?.content ? { content: task.result.content } : undefined)
  const evidence = task?.result?.evidence || []
  const sources = task?.result?.documents || []
  const errors = [task?.result?.error, ...(task?.result?.errors || [])].filter((item): item is string => Boolean(item))
  const capabilityErrors = task?.result?.capability_errors || []
  return <div className="task-inspector">
    <header className="inspector-head"><div><span className={`status-pill ${task?.status}`}>{labels[task?.status || summary.status]}</span><h2>{summary.topic}</h2><code>{summary.task_id}</code></div>{task?.status === 'waiting_approval' && <div className="approval-actions"><button className="danger-button" onClick={() => void decide(false)}><X />拒绝</button><button className="primary" onClick={() => void decide(true)}><Check />批准报告</button></div>}</header>
    <div className="inspector-grid">
      <section className="report-paper">
        {!!errors.length && <div role="alert" className="task-errors"><h3>执行问题</h3>{errors.map((error, index) => <p key={index}>{error}</p>)}</div>}
        {body ? <><div className="report-meta"><BookOpen /><span>研究报告</span>{report && <small>版本 {report.report_version_id.slice(0, 8)}</small>}</div><article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{body.content}</ReactMarkdown></article>
          {!!body.claims?.length && <div className="claims"><h3>论断核验</h3>{body.claims.map((claim) => <div key={claim.id}><span className={`verify ${claim.verification}`}>{claim.verification}</span><p>{claim.text}</p><small>{claim.kind} · {claim.evidence_ids.length} 条证据</small></div>)}</div>}
          {!!body.citations?.length && <div className="citations"><h3>引用</h3>{body.citations.map((citation) => <p key={`${citation.index}-${citation.source_id}`}><b>[{citation.index}]</b> {citation.text || citation.source_id}</p>)}</div>}
        </> : <div className="empty-state tall"><FileSearch /><p>报告尚未生成</p><small>研究智能体正在规划、检索并核验证据。</small></div>}
      </section>
      <aside className="evidence-rail">
        {!!capabilityErrors.length && <section><h3>能力接入问题</h3>{capabilityErrors.map((error, index) => <p className="muted" key={index}>{Object.values(error).join(' · ')}</p>)}</section>}
        {!!task?.result?.loaded_skills?.length && <section><h3>已加载 Skill</h3>{task.result.loaded_skills.map((skill) => <article className="evidence-card" key={skill.id}><p>{skill.name || skill.title}</p><code>{String(skill.version).slice(0, 12)}</code></article>)}</section>}
        {!!task?.result?.trace?.length && <section><h3>工具返回</h3>{task.result.trace.filter((step) => step.decision.tool?.startsWith('mcp:')).map((step, index) => <details className="evidence-card" key={index}><summary>{step.decision.tool}</summary><pre className="tool-output">{JSON.stringify(step.observation, null, 2)}</pre></details>)}</section>}
        {task?.result?.plan && <section><h3>执行计划</h3><p>{task.result.plan}</p></section>}
        <section><h3>执行记录 <span>{events.length}</span></h3><div className="timeline">{events.map((event) => <div key={event.event_id}><i /><div><b>{event.event.replaceAll('_', ' ')}</b><time>{new Date(event.created_at * 1000).toLocaleString('zh-CN')}</time>{event.data && <small>{[event.data.stage, event.data.tool, event.data.query, event.data.reason, event.data.error, event.data.plan].filter(Boolean).map(String).join(' · ')}</small>}</div></div>)}</div></section>
        {!!sources.length && <section><h3>检索来源 <span>{sources.length}</span></h3>{sources.map((source) => <article className="evidence-card" key={source.id}><span>{source.content ? '已获取正文' : '搜索结果 / 摘要'}</span><p>{source.title}</p><a href={source.url} target="_blank" rel="noreferrer">查看来源 <ExternalLink size={13} /></a></article>)}</section>}
        <section><h3>证据片段 <span>{evidence.length}</span></h3>{evidence.length ? evidence.map((item) => <article className="evidence-card" key={item.id}><div><span>{item.scope === 'full_text' ? '全文' : '摘要'}</span><code>{item.id.slice(0, 8)}</code></div><p>{item.text}</p>{item.source_url && <a href={item.source_url} target="_blank" rel="noreferrer">查看来源 <ExternalLink size={13} /></a>}</article>) : <p className="muted">暂无可展示证据</p>}</section>
      </aside>
    </div>
  </div>
}

export function ResearchViews({ mode, tasks, refreshTasks, notify, selectedTaskId, onCreated }: { mode: 'new' | 'tasks'; tasks: TaskSummary[]; refreshTasks: () => Promise<void>; notify: (message: string) => void; selectedTaskId: string | null; onCreated: (id: string) => void }) {
  const [selectedId, setSelectedId] = useState<string | null>(tasks[0]?.task_id || null)
  const selected = tasks.find((task) => task.task_id === selectedId) || tasks[0]
  useEffect(() => { if (!selectedId && tasks[0]) setSelectedId(tasks[0].task_id) }, [tasks, selectedId])
  useEffect(() => { if (selectedTaskId) setSelectedId(selectedTaskId) }, [selectedTaskId])
  if (mode === 'new') return <NewResearch afterCreate={async (id) => { await refreshTasks(); onCreated(id) }} notify={notify} />
  return <div className="page tasks-page">
    <header className="page-heading compact"><div><p className="eyebrow">RESEARCH LEDGER</p><h1>研究任务</h1></div><button className="secondary" onClick={() => void refreshTasks()}><RotateCw size={16} />刷新</button></header>
    <div className="tasks-workspace">
      <aside className="task-list"><div className="list-caption"><span>{tasks.length} 项研究</span><small>按创建时间排序</small></div>{tasks.length ? tasks.map((task) => <button key={task.task_id} className={selected?.task_id === task.task_id ? 'selected' : ''} onClick={() => setSelectedId(task.task_id)}><div><span className={`status-dot ${task.status}`} /><strong>{task.topic}</strong></div><small><Clock3 size={12} />{new Date(task.created_at * 1000).toLocaleString('zh-CN')}<ArrowRight size={14} /></small></button>) : <div className="empty-state"><AlertTriangle /><p>暂无任务</p></div>}</aside>
      <section className="task-detail">{selected ? <TaskInspector key={selected.task_id} summary={selected} notify={notify} refresh={refreshTasks} /> : <div className="empty-state tall"><FileSearch /><p>选择一个研究任务</p></div>}</section>
    </div>
  </div>
}
