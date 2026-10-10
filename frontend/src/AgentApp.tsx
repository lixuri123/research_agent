import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ArrowUp, BrainCircuit, ChevronDown, FolderOpen, History, Menu, MessageSquare, Plus, Settings2, Sparkles, UserRound, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from './api'
import type { TaskDetail, TaskSummary, Workspace, ProviderRegistry } from './types'
import { ResearchViews } from './components/ResearchViews'
import { KnowledgeViews } from './components/KnowledgeViews'
import { WorkspacePicker, WorkspacePane } from './components/WorkspaceViews'
import { FileChanges } from './components/FileChanges'
import { ExecutionAccess, ExecutionLog } from './components/ExecutionPanel'
import { ProviderSettings } from './components/ProviderSettings'
import { PersonalProfile } from './components/PersonalProfile'
import { RemoteSettings } from './remote/RemoteSettings'

type Panel = 'tasks' | 'memories' | 'skills' | 'provider' | 'profile' | 'remote' | null
const names: Record<string, string> = { queued: '等待开始', running: '正在处理', waiting_approval: '需要你确认', completed: '已完成', partial: '部分完成', insufficient_evidence: '证据不足', failed: '执行遇到问题', rejected: '已拒绝', cancelled: '已取消' }
const ongoing = new Set(['queued', 'running', 'waiting_approval'])
const answer = (task?: TaskDetail) => task?.result?.content || task?.result?.final_report?.content || task?.result?.draft?.content || task?.result?.error || ''

export default function AgentApp() {
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [details, setDetails] = useState<Record<string, TaskDetail>>({})
  const [session, setSession] = useState<string>(() => crypto.randomUUID())
  const [input, setInput] = useState('')
  const [online, setOnline] = useState<boolean | null>(null)
  const [sending, setSending] = useState(false)
  const [sidebar, setSidebar] = useState(false)
  const [panel, setPanel] = useState<Panel>(null)
  const [accountOpen, setAccountOpen] = useState(false)
  const accountRef = useRef<HTMLDivElement>(null)
  const accountButtonRef = useRef<HTMLButtonElement>(null)
  const [providerCatalog, setProviderCatalog] = useState<ProviderRegistry | null>(null)
  const [providerLoading, setProviderLoading] = useState(true)
  const [providerError, setProviderError] = useState('')
  const [modelChoice, setModelChoice] = useState(() => localStorage.getItem('agent-model-selection') || '')
  const [selectedTask, setSelectedTask] = useState<string | null>(null)
  const [toast, setToast] = useState('')
  const [options, setOptions] = useState(false)
  const [research, setResearch] = useState(false)
  const [approval, setApproval] = useState(true)
  const [workspaces, setWorkspaces] = useState<Workspace[]>([])
  const [workspaceRoots, setWorkspaceRoots] = useState<string[]>([])
  const [workspaceId, setWorkspaceId] = useState(() => localStorage.getItem('agent-workspace') || '')
  const [filesOpen, setFilesOpen] = useState(false)
  const [fileDirty, setFileDirty] = useState(false)
  const [fileSaving, setFileSaving] = useState(false)
  const workspace = workspaces.find((item) => item.id === workspaceId)
  const workspaceTasks = useMemo(() => tasks.filter((task) => (task.workspace_id || '') === workspaceId), [tasks, workspaceId])
  const conversationRef = useRef<HTMLDivElement>(null)
  const followLatest = useRef(true)
  const refreshSequence = useRef(0)
  const notify = useCallback((message: string) => setToast(message), [])
  const refreshProviders = useCallback(async () => {
    setProviderLoading(true)
    try {
      const values = await api.providers()
      setProviderCatalog(values); setProviderError('')
      const choices = values.providers.flatMap((provider) => provider.models.map((model) => JSON.stringify({ provider_id: provider.id, model })))
      const active = values.providers.find((provider) => provider.id === values.active_provider_id) || values.providers[0]
      setModelChoice((current) => choices.includes(current) ? current : active ? JSON.stringify({ provider_id: active.id, model: active.model }) : '')
    } catch (error) { setProviderError(error instanceof Error ? error.message : '无法读取模型列表') }
    finally { setProviderLoading(false) }
  }, [])
  useEffect(() => { if (panel === null) void refreshProviders() }, [panel, refreshProviders])
  useEffect(() => { if (modelChoice) localStorage.setItem('agent-model-selection', modelChoice) }, [modelChoice])
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(''), 5000); return () => window.clearTimeout(timer) }, [toast])
  const refreshTasks = useCallback(async () => {
    const sequence = ++refreshSequence.current
    try { const result = await api.tasks(workspaceId); if (sequence === refreshSequence.current) { setTasks(result.tasks); setOnline(true) } }
    catch (error) { if (sequence === refreshSequence.current) setOnline(false); throw error }
  }, [workspaceId])
  useEffect(() => { void refreshTasks().catch(() => notify('暂时无法连接服务，请确认 API 已启动')) }, [refreshTasks, notify])
  useEffect(() => {
    let cancelled = false
    void api.workspaces().then((result) => {
      if (cancelled) return
      setWorkspaces(result.workspaces); setWorkspaceRoots(result.allowed_roots)
      setWorkspaceId((previous) => result.workspaces.some((item) => item.id === previous) ? previous : '')
    }).catch(() => notify('工作空间加载失败，请重启新版 API 后刷新页面'))
    return () => { cancelled = true }
  }, [notify])
  useEffect(() => { localStorage.setItem('agent-workspace', workspaceId) }, [workspaceId])
  const conversations = useMemo(() => {
    const groups = new Map<string, { id: string; title: string; active: boolean }>()
    for (const task of [...workspaceTasks].reverse()) {
      const id = task.session_id || task.task_id
      const old = groups.get(id)
      groups.set(id, { id, title: old?.title || task.topic, active: !!old?.active || ongoing.has(task.status) })
    }
    return [...groups.values()].reverse()
  }, [workspaceTasks])
  const thread = useMemo(() => workspaceTasks.filter((task) => (task.session_id || task.task_id) === session).sort((a, b) => a.created_at - b.created_at), [workspaceTasks, session])
  const signature = thread.map((task) => `${task.task_id}:${task.status}`).join('|')
  const busy = thread.some((task) => ongoing.has(task.status))
  const latestAnswer = answer(details[thread[thread.length - 1]?.task_id])
  useEffect(() => {
    followLatest.current = true
    const container = conversationRef.current
    if (container) container.scrollTop = thread.length ? container.scrollHeight : 0
  }, [session, thread.length])
  useEffect(() => {
    const container = conversationRef.current
    if (container && thread.length && followLatest.current) container.scrollTop = container.scrollHeight
  }, [latestAnswer])
  useEffect(() => {
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        if (accountOpen) { setAccountOpen(false); accountButtonRef.current?.focus(); return }
        setPanel(null); setOptions(false); setSidebar(false)
      }
    }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [accountOpen])
  useEffect(() => {
    if (!accountOpen) return
    accountRef.current?.querySelector<HTMLButtonElement>('.agent-account-menu button')?.focus()
    const close = (event: PointerEvent) => {
      if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false)
    }
    const blur = (event: FocusEvent) => {
      if (!accountRef.current?.contains(event.target as Node)) setAccountOpen(false)
    }
    document.addEventListener('pointerdown', close)
    document.addEventListener('focusin', blur)
    return () => { document.removeEventListener('pointerdown', close); document.removeEventListener('focusin', blur) }
  }, [accountOpen])
  useEffect(() => {
    let cancelled = false
    async function load() {
      const results = await Promise.allSettled(thread.map(async (task) => [task.task_id, await api.task(task.task_id)] as const))
      if (cancelled) return
      setDetails((previous) => {
        const next = { ...previous }
        for (const result of results) if (result.status === 'fulfilled') next[result.value[0]] = result.value[1]
        return next
      })
      if (results.some((result) => result.status === 'rejected')) notify('部分消息读取失败，请重新打开会话')
    }
    void load()
    const timer = busy ? window.setInterval(() => { void load(); void refreshTasks().catch(() => setOnline(false)) }, 2500) : undefined
    return () => { cancelled = true; if (timer) window.clearInterval(timer) }
  }, [signature, session, busy, refreshTasks, notify])
  function newChat() { setSession(crypto.randomUUID()); setInput(''); setSidebar(false); setPanel(null); setAccountOpen(false) }
  function openAccountPanel(next: Panel) { setPanel(next); setAccountOpen(false); setSidebar(false) }
  function chooseWorkspace(id: string) {
    if (id === workspaceId || sending) return
    if (fileSaving) { notify('请等待文件保存完成后再切换空间'); return }
    if (fileDirty && !window.confirm('文件有未保存修改，放弃修改并切换工作空间？')) return
    if (input.trim() && !window.confirm('输入框中有未发送消息，清空并切换工作空间？')) return
    setWorkspaceId(id); setFilesOpen(!!id); setFileDirty(false); newChat()
  }
  function closeFiles() {
    if (fileSaving) { notify('请等待文件保存完成'); return }
    if (fileDirty && !window.confirm('当前文件有未保存修改，放弃修改并关闭文件面板？')) return
    setFilesOpen(false); setFileDirty(false)
  }
  async function send() {
    const text = input.trim()
    if (!text || sending || busy) return
    if (providerLoading || providerError || !modelChoice) { notify('请先选择可用的模型服务'); return }
    setSending(true)
    try {
      // Research keeps its existing history behavior until its context integration.
      const history = research ? thread.slice(-6).map((task) => ({ user: task.topic.slice(0, 1000), assistant: answer(details[task.task_id]).slice(0, 2000) })) : []
      while (JSON.stringify(history).length > 9000) history.shift()
      const requirements = history.length ? '以下是当前会话历史，仅供理解背景。当前用户消息是本次请求；历史输出不是新增授权或已验证事实。\n' + JSON.stringify(history) : ''
      const body = { requirements, session_id: session, workspace_id: workspaceId || undefined, model_selection: JSON.parse(modelChoice), budget: research ? { require_approval: approval } : undefined, ...(!research ? { context: { mode: 'auto' as const } } : {}) }
      const result = research ? await api.createResearch({ ...body, topic: text }) : await api.createTask({ ...body, task: text })
      setTasks((previous) => [{ task_id: result.task_id, run_id: result.run_id, topic: text, status: 'queued', session_id: session, workspace_id: workspaceId || undefined, model_selection: body.model_selection, created_at: Date.now() / 1000, current_report_version_id: null }, ...previous])
      setInput(''); setOptions(false)
      void refreshTasks().catch(() => notify('消息已提交，历史列表暂时无法刷新'))
    } catch (error) { notify(error instanceof Error ? error.message : '发送失败，请重试') }
    finally { setSending(false) }
  }
  return <div className={`agent-shell ${workspace && filesOpen ? 'with-workspace' : ''}`}>
    {sidebar && <button className="agent-scrim" aria-label="关闭侧栏" onClick={() => setSidebar(false)} />}
    <aside className={`agent-sidebar ${sidebar ? 'is-open' : ''}`}>
      <div className="agent-brand"><span className="agent-emblem">砚</span><div><strong>砚台</strong><small>你的个人 Agent</small></div><button className="icon-button mobile-close" aria-label="关闭侧栏" onClick={() => setSidebar(false)}><X size={18} /></button></div>
      <WorkspacePicker workspaces={workspaces} active={workspaceId} roots={workspaceRoots} onSelect={chooseWorkspace} onBound={(bound) => { setWorkspaces((previous) => [bound, ...previous.filter((item) => item.id !== bound.id)]); chooseWorkspace(bound.id) }} notify={notify} />
      {workspace && <ExecutionAccess key={workspace.id} workspace={workspace.id} />}
      <button className="new-chat" onClick={newChat}><Plus size={18} />新的对话<span>↗</span></button>
      <div className="history-heading"><span>{workspace ? '空间内的对话' : '最近的对话'}</span><button className="icon-button" aria-label="刷新历史" onClick={() => void refreshTasks().catch(() => notify('历史刷新失败'))}><History size={14} /></button></div>
      <nav className="conversation-list" aria-label="会话历史">{conversations.map((item) => <button key={item.id} className={session === item.id ? 'selected' : ''} onClick={() => { setSession(item.id); setSidebar(false); setPanel(null) }}><MessageSquare size={15} /><span>{item.title}</span>{item.active && <i className="conversation-active" />}</button>)}{!conversations.length && <p>从一次交流开始。<br />你的对话会保留在这里。</p>}</nav>
      <div className="agent-account" ref={accountRef}>
        {accountOpen && <nav className="agent-account-menu" id="account-settings" aria-label="个人设置"><p>个人设置</p><button onClick={() => openAccountPanel('remote')}><Settings2 size={17} />手机控制</button><button onClick={() => openAccountPanel('profile')}><UserRound size={17} />关于我</button><button onClick={() => openAccountPanel('tasks')}><History size={17} />活动与成果</button><button onClick={() => openAccountPanel('memories')}><BrainCircuit size={17} />记忆</button><button onClick={() => openAccountPanel('skills')}><Sparkles size={17} />能力</button><button onClick={() => openAccountPanel('provider')}><Settings2 size={17} />模型 Provider</button></nav>}
        <button className="agent-identity" ref={accountButtonRef} aria-label="头像设置" aria-expanded={accountOpen} aria-controls="account-settings" onClick={() => setAccountOpen(!accountOpen)}><span className="agent-user-avatar"><UserRound size={20} /><i className={`live-light ${online ? 'on' : ''}`} /></span><span className="agent-identity-copy"><b>个人空间</b><small>{online === null ? '正在连接' : online ? '已连接' : '连接中断'}</small></span><Settings2 size={16} /></button>
      </div>
    </aside>
    <main className="agent-main">
      <header className="agent-topbar"><button className="icon-button mobile-menu" aria-label="打开会话列表" onClick={() => setSidebar(true)}><Menu size={20} /></button><span>{workspace?.name || '砚台'} <ChevronDown size={14} /></span><div className="agent-header-actions">{workspace && <button className={`agent-top-action ${filesOpen ? 'active' : ''}`} onClick={() => filesOpen ? closeFiles() : setFilesOpen(true)}><FolderOpen size={16} />文件</button>}<button className="agent-top-action" onClick={newChat}><Plus size={16} />新对话</button></div></header>
      {workspace && <div className="workspace-context-bar"><FolderOpen size={13} /><span title={workspace.root}>{workspace.root}</span><small>{research ? '研究模式暂不读取本地文件' : workspace.agent_read ? 'Agent 可读' : '手动浏览'}</small></div>}
      <div ref={conversationRef} onScroll={(event) => { const container = event.currentTarget; followLatest.current = container.scrollHeight - container.scrollTop - container.clientHeight < 100 }} className={`agent-conversation ${thread.length ? 'has-messages' : ''}`}>
        {!thread.length ? <section className="agent-welcome"><div className="welcome-symbol">砚</div><p className="agent-kicker">留一点空间，给正在发生的想法</p><h1>今天，我们从哪里开始？</h1><p className="welcome-copy">一个问题，一份材料，或一件想推进的事。<br />你可以随时补充背景、调整方向。</p><div className="conversation-starters">{['一起梳理我现在的研究想法', '帮我读懂一篇论文', '回顾这一周的进展', '讨论下一步的学习方向'].map((text) => <button key={text} onClick={() => setInput(text)}>{text}<span>↗</span></button>)}</div></section> : <div className="agent-message-list">{thread.map((task) => {
          const detail = details[task.task_id], content = answer(detail), status = detail?.status || task.status
          return <section className="agent-turn" key={task.task_id}><div className="user-message">{task.topic}</div><div className="assistant-message"><div className="assistant-avatar">砚</div><div className="assistant-content">{content ? <article className="markdown agent-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></article> : <div className="agent-progress" role="status"><span className={ongoing.has(status) ? 'thinking-pulse' : ''} />{names[status] || status}{status === 'queued' && <small>后台开始后会在这里更新</small>}</div>}
          {status === 'waiting_approval' && <div className="inline-approval"><span>这份报告需要你的确认。</span><button onClick={() => { setSelectedTask(task.task_id); setPanel('tasks') }}>查看并审批 →</button></div>}
          {task.workspace_id && <FileChanges workspace={task.workspace_id} run={task.run_id} ongoing={ongoing.has(status)} blocked={fileDirty || fileSaving} />}
          {task.workspace_id && <ExecutionLog workspace={task.workspace_id} run={task.run_id} ongoing={ongoing.has(status)} />}
          {!!detail?.result?.plan && <details className="agent-process"><summary>查看处理过程</summary><p>{detail.result.plan}</p>{detail.result.trace?.map((step, index) => <div key={index}>{index + 1}. {step.decision.tool || '整理结果'}</div>)}</details>}
          {!!detail?.result?.documents?.length && <details className="agent-sources"><summary>{detail.result.documents.length} 个参考来源</summary>{detail.result.documents.map((source) => <a key={source.id} href={source.url} target="_blank" rel="noreferrer">{source.title || source.url}</a>)}</details>}
          <button className="message-detail" onClick={() => { setSelectedTask(task.task_id); setPanel('tasks') }}>{names[status] || status} · 查看详情</button></div></div></section>
        })}</div>}
      </div>
      <div className="agent-composer-wrap"><div className="composer-model-choice"><label htmlFor="conversation-model">使用模型</label><select id="conversation-model" value={modelChoice} disabled={providerLoading || !!providerError || sending} onChange={(event) => setModelChoice(event.target.value)}><option value="" disabled>{providerLoading ? '读取模型列表…' : providerError ? '模型列表不可用' : '请先配置模型服务'}</option>{providerCatalog?.providers.map((provider) => <optgroup key={provider.id} label={provider.name}>{provider.models.map((model) => <option key={model} value={JSON.stringify({ provider_id: provider.id, model })}>{provider.name} / {model}</option>)}</optgroup>)}</select>{providerError && <><span className="model-error">加载失败</span><button onClick={() => void refreshProviders()}>重试</button></>}</div><div className="agent-composer"><label className="sr-only" htmlFor="agent-input">发送消息给砚台</label><textarea id="agent-input" value={input} maxLength={4000} onChange={(event) => setInput(event.target.value)} placeholder="和砚台聊聊，或交代一件事…" rows={3} onKeyDown={(event) => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send() } }} /><div className="composer-toolbar"><button className={`composer-option ${research ? 'enabled' : ''}`} onClick={() => setOptions(!options)}><Settings2 size={16} />{research ? '深度研究' : 'Agent'}<ChevronDown size={12} /></button><span>{busy ? '正在处理当前消息' : 'Enter 发送 · Shift + Enter 换行'}</span><button className="send-message" aria-label="发送消息" disabled={!input.trim() || sending || busy || providerLoading || !!providerError || !modelChoice} onClick={() => void send()}><ArrowUp size={20} /></button></div>{options && <div className="composer-options"><label><input type="checkbox" checked={research} onChange={(event) => setResearch(event.target.checked)} />深度研究 <small>适用于需要引用与证据核验的报告</small></label>{research && <label><input type="checkbox" checked={approval} onChange={(event) => setApproval(event.target.checked)} />发布前由我确认</label>}</div>}</div><p className="composer-note">重要结论请核对来源与实际结果。</p></div>
    </main>
    {workspace && filesOpen && <WorkspacePane key={workspace.id} workspace={workspace} notify={notify} onClose={closeFiles} onDirty={setFileDirty} onSaving={setFileSaving} />}
    {panel && <div className="agent-panel-backdrop" onClick={() => setPanel(null)}><section className="agent-utility-panel" role="dialog" aria-modal="true" aria-label="辅助面板" onClick={(event) => event.stopPropagation()}><header><span>{panel === 'remote' ? '手机控制' : panel === 'tasks' ? '活动与成果' : panel === 'memories' ? '记忆' : panel === 'provider' ? '模型 Provider' : panel === 'profile' ? '关于我' : '能力'}</span><button className="icon-button" aria-label="关闭面板" onClick={() => setPanel(null)}><X /></button></header>{panel === 'remote' ? <RemoteSettings /> : panel === 'tasks' ? <ResearchViews mode="tasks" tasks={workspaceTasks} refreshTasks={refreshTasks} notify={notify} selectedTaskId={selectedTask} onCreated={() => {}} /> : panel === 'provider' ? <ProviderSettings /> : panel === 'profile' ? <PersonalProfile notify={notify} /> : <KnowledgeViews mode={panel} notify={notify} />}</section></div>}
    {toast && <div className="toast" role="alert" onClick={() => setToast('')}>{toast}</div>}
  </div>
}

