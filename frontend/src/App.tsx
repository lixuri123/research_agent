import { useCallback, useEffect, useMemo, useState } from 'react'
import { Activity, BookOpenCheck, BrainCircuit, FilePlus2, FlaskConical, LayoutDashboard, Menu, MessagesSquare, PanelLeftClose, Sparkles } from 'lucide-react'
import { api } from './api'
import type { TaskSummary } from './types'
import { ResearchViews } from './components/ResearchViews'
import { KnowledgeViews } from './components/KnowledgeViews'
import { ProviderSettings } from './components/ProviderSettings'

type Section = 'dashboard' | 'new' | 'tasks' | 'sessions' | 'memories' | 'skills' | 'provider'

const nav = [
  { id: 'dashboard' as const, label: '总览', icon: LayoutDashboard },
  { id: 'new' as const, label: '新建任务', icon: FilePlus2 },
  { id: 'tasks' as const, label: '任务列表', icon: FlaskConical },
  { id: 'sessions' as const, label: '会话记录', icon: MessagesSquare },
  { id: 'memories' as const, label: '记忆库', icon: BrainCircuit },
  { id: 'skills' as const, label: 'Skills', icon: BookOpenCheck },
  { id: 'provider' as const, label: '模型 Provider', icon: BrainCircuit },
]

function statusLabel(status: string) {
  return ({ queued: '排队中', running: '研究中', waiting_approval: '待审批', completed: '已完成', partial: '部分完成', insufficient_evidence: '证据不足', rejected: '已拒绝', failed: '失败', cancelled: '已取消' } as Record<string, string>)[status] || status
}

function Dashboard({ tasks, online, go }: { tasks: TaskSummary[]; online: boolean; go: (section: Section) => void }) {
  const counts = useMemo(() => ({
    active: tasks.filter((t) => ['queued', 'running'].includes(t.status)).length,
    review: tasks.filter((t) => t.status === 'waiting_approval').length,
    complete: tasks.filter((t) => t.status === 'completed').length,
  }), [tasks])

  return <div className="page dashboard-page">
    <header className="page-heading hero-heading">
      <div>
        <p className="eyebrow">RESEARCH OPERATIONS</p>
        <h1>把问题交给系统，<br /><em>把判断留给研究者。</em></h1>
      </div>
      <button className="primary hero-action" onClick={() => go('new')}><Sparkles size={18} />发起新研究</button>
    </header>

    <section className="metric-strip">
      <div><span>服务状态</span><strong className={online ? 'healthy' : 'danger'}>{online ? '在线' : '离线'}</strong></div>
      <div><span>进行中</span><strong>{counts.active.toString().padStart(2, '0')}</strong></div>
      <div><span>待审批</span><strong>{counts.review.toString().padStart(2, '0')}</strong></div>
      <div><span>已完成</span><strong>{counts.complete.toString().padStart(2, '0')}</strong></div>
    </section>

    <section className="dashboard-grid">
      <article className="paper-panel recent-work">
        <div className="section-title"><div><span>01</span><h2>近期研究</h2></div><button className="text-button" onClick={() => go('tasks')}>查看全部 →</button></div>
        {tasks.length === 0 ? <div className="empty-state"><FlaskConical /><p>还没有研究任务</p><small>从一个清晰的问题开始。</small></div> :
          <div className="task-rows">{tasks.slice(0, 6).map((task) => <button key={task.task_id} className="task-row" onClick={() => go('tasks')}>
            <span className={`status-dot ${task.status}`} />
            <span className="task-topic">{task.topic}</span>
            <span className={`status-pill ${task.status}`}>{statusLabel(task.status)}</span>
            <time>{new Date(task.created_at * 1000).toLocaleDateString('zh-CN')}</time>
          </button>)}</div>}
      </article>
      <aside className="paper-panel field-notes">
        <div className="section-title"><div><span>02</span><h2>工作流</h2></div></div>
        <ol>
          <li><b>定义问题</b><small>主题、约束与研究预算</small></li>
          <li><b>多智能体协作</b><small>规划、检索、阅读与写作</small></li>
          <li><b>证据审核</b><small>逐条核对论断与出处</small></li>
          <li><b>人工决策</b><small>审批版本化研究报告</small></li>
        </ol>
      </aside>
    </section>
  </div>
}

export default function App() {
  const [section, setSection] = useState<Section>('dashboard')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [online, setOnline] = useState(false)
  const [tasks, setTasks] = useState<TaskSummary[]>([])
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const notify = useCallback((message: string) => {
    setToast(message)
    window.setTimeout(() => setToast(null), 3600)
  }, [])

  const refreshTasks = useCallback(async () => {
    try { setTasks((await api.tasks()).tasks); setOnline(true) }
    catch (error) { setOnline(false); notify(error instanceof Error ? error.message : '无法连接服务') }
  }, [notify])

  useEffect(() => { api.health().then(() => setOnline(true)).catch(() => setOnline(false)); void refreshTasks() }, [refreshTasks])

  return <div className={`app-shell ${sidebarOpen ? '' : 'sidebar-collapsed'}`}>
    <aside className="sidebar">
      <div className="brand"><div className="brand-mark">砚</div><div><strong>砚台</strong><span>RESEARCH DESK</span></div></div>
      <nav>{nav.map(({ id, label, icon: Icon }) => <button key={id} className={section === id ? 'active' : ''} onClick={() => setSection(id)} title={label}><Icon size={19} /><span>{label}</span>{id === 'tasks' && tasks.filter((t) => t.status === 'waiting_approval').length > 0 && <i>{tasks.filter((t) => t.status === 'waiting_approval').length}</i>}</button>)}</nav>
      <div className="sidebar-foot"><span className={`live-light ${online ? 'on' : ''}`} /><div><b>{online ? '系统在线' : '连接中断'}</b><small>FastAPI · v0.3</small></div></div>
    </aside>
    <main className="main-area">
      <div className="topbar"><button className="icon-button" onClick={() => setSidebarOpen((value) => !value)}>{sidebarOpen ? <PanelLeftClose /> : <Menu />}</button><div className="topbar-context"><Activity size={15} /><span>{nav.find((item) => item.id === section)?.label}</span></div><div className="date-stamp">{new Intl.DateTimeFormat('zh-CN', { dateStyle: 'long' }).format(new Date())}</div></div>
      {section === 'dashboard' && <Dashboard tasks={tasks} online={online} go={setSection} />}
      {(section === 'new' || section === 'tasks') && <ResearchViews mode={section} tasks={tasks} refreshTasks={refreshTasks} notify={notify} selectedTaskId={selectedTaskId} onCreated={(id) => { setSelectedTaskId(id); setSection('tasks') }} />}
      {(section === 'sessions' || section === 'memories' || section === 'skills') && <KnowledgeViews mode={section} notify={notify} />}
      {section === 'provider' && <ProviderSettings />}
    </main>
    {toast && <div className="toast" role="status">{toast}</div>}
  </div>
}
