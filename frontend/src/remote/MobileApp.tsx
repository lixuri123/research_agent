import { useEffect, useMemo, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { FileChange, FileChangeDetail, ReportVersion, SessionMessage, TaskDetail, TaskSummary } from '../types'
import { RemoteError, RemoteTransport, remoteHTTP } from './transport'
import type { ConnectionState } from './transport'
import './remote.css'

// Consume the fragment before React mounts or any asynchronous request begins.
const invite = new URLSearchParams(location.hash.slice(1)).get('invite')
if (location.hash) history.replaceState(null, '', location.pathname + location.search)
type Operation = { host: string; device: string; method: string; payload: Record<string, unknown>; key: string }
type Models = { providers: { id: string; name: string; models: string[]; model: string }[] }
const labels: Record<string, string> = { queued: '等待开始', running: '正在处理', waiting_approval: '等待确认', completed: '已完成', failed: '执行遇到问题', partial: '部分完成', rejected: '已拒绝' }
let pairing: Promise<unknown> | null = null
export default function MobileApp() {
  const [connection, setConnection] = useState<ConnectionState>('connecting')
  const transport = useMemo(() => new RemoteTransport(setConnection), [])
  const [error, setError] = useState(''), [code, setCode] = useState(''), [name, setName] = useState('我的手机')
  const [personalAccess, setPersonalAccess] = useState(false), [scopesReady, setScopesReady] = useState(false)
  const [spaces, setSpaces] = useState<{id: string; name: string}[]>([]), [space, setSpace] = useState('')
  const [sessions, setSessions] = useState<{session_id: string}[]>([]), [session, setSession] = useState<string>(() => crypto.randomUUID())
  const [models, setModels] = useState<Models>({providers: []}), [model, setModel] = useState('')
  const [tasks, setTasks] = useState<TaskSummary[]>([]), [selected, setSelected] = useState<TaskSummary | null>(null)
  const [detail, setDetail] = useState<TaskDetail | null>(null), [messages, setMessages] = useState<SessionMessage[]>([])
  const [report, setReport] = useState<ReportVersion | null>(null), [changes, setChanges] = useState<FileChange[]>([]), [change, setChange] = useState<FileChangeDetail | null>(null)
  const [draft, setDraft] = useState(() => localStorage.getItem('remote-draft') || ''), [research, setResearch] = useState(false)
  const [operation, setOperation] = useState<Operation | null>(() => { try { return JSON.parse(localStorage.getItem('remote-operation') || 'null') } catch { return null } })
  const [sending, setSending] = useState(false), [events, setEvents] = useState<Record<string, unknown>[]>([])
  const online = connection === 'online'
  useEffect(() => { localStorage.setItem('remote-draft', draft) }, [draft])
  useEffect(() => { if (operation) localStorage.setItem('remote-operation', JSON.stringify(operation)); else localStorage.removeItem('remote-operation') }, [operation])
  useEffect(() => { if (!invite) transport.start(); return () => transport.stop() }, [transport])
  async function pair() {
    if (!invite || pairing) return
    setError(''); pairing = remoteHTTP<{device_id: string; pending_secret: string; code: string; expires_at?: number}>('pair/redeem', {token: invite, name})
    try {
      const pending = await pairing as {device_id: string; pending_secret: string; code: string; expires_at?: number}; setCode(pending.code)
      const deadline = pending.expires_at ? pending.expires_at * 1000 : Date.now() + 300000
      while (Date.now() < deadline) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        try { await remoteHTTP('pair/finish', {device_id: pending.device_id, pending_secret: pending.pending_secret}); setCode(''); transport.start(); return }
        catch (e) { if (!(e instanceof RemoteError) || e.code !== 'conflict') throw e }
      }
      throw new Error('邀请已过期，请在电脑重新添加手机')
    } catch (e) { setError(e instanceof Error ? e.message : '配对失败') }
  }
  async function refresh() {
    try {
      const result = await transport.call<{tasks: TaskSummary[]}>('task.list', {workspace_id: space, limit: 100}); setTasks(result.tasks)
      const list = await transport.call<{sessions: {session_id: string}[]}>('session.list', {workspace_id: space, limit: 100}); setSessions(list.sessions)
    } catch (e) { setError(e instanceof Error ? e.message : '读取失败') }
  }
  useEffect(() => { if (!online) return; void transport.call<{workspaces: {id: string; name: string}[]; personal_access: boolean}>('workspace.list', {}).then(x => {setSpaces(x.workspaces);setPersonalAccess(x.personal_access);setSpace(current => (current === '' && x.personal_access) || x.workspaces.some(w => w.id === current) ? current : x.workspaces[0]?.id || '');setScopesReady(true)}).catch(e => setError(e.message)); void transport.call<Models>('model.list', {}).then(x => { setModels(x); setModel(current => current || (x.providers[0] ? JSON.stringify({provider_id: x.providers[0].id, model: x.providers[0].model}) : '')) }).catch(e => setError(e.message)) }, [online, transport])
  useEffect(() => { if (online && scopesReady && (space || personalAccess)) void refresh() }, [online, space, scopesReady, personalAccess])
  useEffect(() => {
    if (!selected) return
    let cancelled = false
    const load = async () => {
      try { const x = await transport.call<TaskDetail>('task.read', {task_id: selected.task_id}); if (cancelled) return; setDetail(x)
        const version = x.current_report_version_id
        if (version) { const r = await transport.call<ReportVersion>('report.read', {report_version_id: version}); if (!cancelled) setReport(r) } else setReport(null)
        if (selected.workspace_id) { const c = await transport.call<{changes: FileChange[]}>('file_change.list', {workspace_id: selected.workspace_id, run_id: selected.run_id}); if (!cancelled) setChanges(c.changes) }
      } catch (e) { if (!cancelled) { setReport(null); setError(e instanceof Error ? e.message : '内容无法完整读取，请回电脑查看') } }
    }
    void load(); const unsubscribe = transport.subscribe(selected.task_id, event => { setEvents(old => [...old.slice(-99), event.payload]); void load(); void refresh() })
    const timer = window.setInterval(() => void load(), 10000)
    return () => { cancelled = true; unsubscribe(); clearInterval(timer) }
  }, [selected?.task_id, transport])
  async function submit(retry = false) {
    if (!online || sending) return
    const op = retry ? operation : { host: transport.session!.host_id, device: transport.session!.device_id, method: research ? 'research.create' : 'task.create', payload: { [research ? 'topic' : 'task']: draft.trim(), session_id: session, workspace_id: space, model_selection: JSON.parse(model), ...(research ? {budget: {require_approval: true}} : {}) }, key: crypto.randomUUID() }
    if (!op || (op.host !== transport.session?.host_id || op.device !== transport.session?.device_id)) { setError('待确认提交属于另一台电脑，请重新配对原电脑确认'); return }
    setOperation(op); setSending(true); setError('')
    try { await transport.call(op.method, op.payload, op.key); setOperation(null); if (['task.create','research.create'].includes(op.method)) setDraft(''); else {setChange(null);setReport(null);setSelected(null)}; await refresh() }
    catch (e) { setError(e instanceof Error ? e.message : '提交状态待确认'); if (e instanceof RemoteError && !['unknown','offline','internal_error'].includes(e.code)) setOperation(null) }
    finally { setSending(false) }
  }
  async function decide(method: string, payload: Record<string, unknown>) { const op = {host:transport.session!.host_id,device:transport.session!.device_id,method,payload,key:crypto.randomUUID()}; setOperation(op); setSending(true); setError(''); try { await transport.call(method, payload, op.key); setOperation(null);setChange(null); setReport(null); await refresh(); setSelected(null) } catch (e) { setError(e instanceof Error ? e.message : '审批状态待确认，请刷新核对'); if(e instanceof RemoteError&&!['unknown','offline','internal_error'].includes(e.code))setOperation(null);setChange(null); setReport(null) } finally { setSending(false) } }
  const content = detail?.result?.content || detail?.result?.final_report?.content || detail?.result?.draft?.content || detail?.result?.error || ''
  return <main className="remote-mobile"><header className="remote-heading"><span className="remote-seal">砚</span><div><h1>砚台 · 手机</h1><small>{transport.session?.name || '随身连接你的电脑'}</small></div><span className={`remote-state ${online ? 'on' : ''}`}>{({online:'电脑在线',offline:'电脑离线',connecting:'正在连接',unpaired:'未配对'})[connection]}</span></header>
    {error && <div role="alert" className="remote-error">{error}<button onClick={() => setError('')} aria-label="关闭提示">×</button></div>}
    {invite && !transport.session ? <section className="remote-card"><p className="remote-eyebrow">建立连接</p><h2>{code ? '请在电脑确认' : '给这台手机一个名字'}</h2>{code ? <><p>核对电脑上的设备名称与核对码，再选择授权空间。</p><strong className="remote-code">{code}</strong><p>等待电脑批准，保持此页面打开。</p></> : <><input aria-label="设备名称" maxLength={80} value={name} onChange={e => setName(e.target.value)} /><button disabled={!name.trim() || !!pairing} onClick={() => void pair()}>开始配对</button></>}</section> : connection === 'unpaired' ? <section className="remote-card"><h2>从电脑邀请开始</h2><p>在电脑头像菜单打开「手机控制」，添加手机，然后扫描二维码。设备已解绑或浏览器凭证丢失时也需要重新配对。</p><button onClick={() => transport.start()}>重新连接</button></section> : <>
    <section className="remote-selectors"><label>空间<select value={space} onChange={e => {setSpace(e.target.value); setSession(crypto.randomUUID()); setSelected(null);setMessages([]);setChanges([]);setChange(null);setDetail(null);setReport(null)}} disabled={!!operation}>{personalAccess && <option value="">个人对话</option>}{spaces.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label><label>会话<select value={session} disabled={!!operation} onChange={e => {setSession(e.target.value); void transport.call<{messages: SessionMessage[]}>('session.read', {session_id: e.target.value}).then(x => setMessages(x.messages)).catch(x => setError(x.message))}}><option value={session}>{sessions.some(s => s.session_id === session) ? '当前会话' : '新的对话'}</option>{sessions.filter(s => s.session_id !== session).map(s => <option key={s.session_id} value={s.session_id}>{tasks.find(t => t.session_id === s.session_id)?.topic || s.session_id}</option>)}</select></label><button disabled={!!operation} onClick={() => {setSession(crypto.randomUUID()); setSelected(null); setMessages([])}}>＋ 新对话</button></section>
    {!online && <p className="remote-notice">电脑离线时任务继续运行；连接恢复后会补读进度。新任务暂时无法提交。</p>}
    {operation && <section className="remote-card remote-unknown"><h2>提交状态待确认</h2><p>已保留本次内容与原始提交编号。恢复连接后确认，不会重复创建任务。</p><p>{String(operation.payload.task || operation.payload.topic || (operation.method === 'report.decide' ? '报告审批：' + operation.payload.report_version_id : '文件审批：' + operation.payload.change_id))}</p><button disabled={!online || sending} onClick={() => void submit(true)}>用原提交编号确认 / 重试</button><button disabled={sending} onClick={() => {if(window.confirm('此操作可能已经被电脑接受。请先在电脑核对任务或审批状态，以免重新提交造成重复。确认放弃本地待确认记录？')){setOperation(null);setError('')}}}>放弃待确认记录</button></section>}
    <section className="remote-card remote-compose"><label>使用模型<select value={model} disabled={!!operation} onChange={e => setModel(e.target.value)}><option value="">请选择电脑已配置模型</option>{models.providers.flatMap(p => p.models.map(m => <option key={p.id+m} value={JSON.stringify({provider_id:p.id,model:m})}>{p.name} / {m}</option>))}</select></label><textarea aria-label="任务内容" rows={4} maxLength={4000} placeholder="一个问题，或一件想推进的事…" value={draft} disabled={!!operation} onChange={e => setDraft(e.target.value)} /><footer><label><input type="checkbox" checked={research} disabled={!!operation} onChange={e => setResearch(e.target.checked)} />深度研究 · 发布前确认</label><button disabled={!online || sending || !!operation || !draft.trim() || !model || !scopesReady || (!space && !personalAccess)} onClick={() => void submit()}>发送 ↑</button></footer></section>
    {!!messages.length && <details className="remote-card"><summary>会话消息</summary>{messages.map(m => <article key={m.id}><b>{m.role}</b><p>{m.content}</p></article>)}</details>}
    <section><div className="remote-section-title"><h2>活动与成果</h2><button disabled={!online} onClick={() => void refresh()}>刷新</button></div>{tasks.filter(t => !session || t.session_id === session).map(t => <button key={t.task_id} className="remote-task" onClick={() => {setSelected(t); setDetail(null); setReport(null); setChanges([]); setChange(null); setEvents([])}}><strong>{t.topic}</strong><span>{labels[t.status] || t.status} →</span></button>)}<details><summary>全部任务</summary>{tasks.map(t => <button className="remote-task" key={t.task_id} onClick={() => {setSelected(t); setDetail(null); setReport(null); setChange(null);setChanges([]);setEvents([])}}>{t.topic}<span>{labels[t.status] || t.status}</span></button>)}</details></section>
    {selected && <section className="remote-card remote-result"><div className="remote-section-title"><h2>{selected.topic}</h2><button onClick={() => setSelected(null)}>关闭</button></div><p>{labels[detail?.status || selected.status] || detail?.status}</p><article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></article>{detail?.result?.plan && <details><summary>处理过程</summary><p>{detail.result.plan}</p></details>}{!!events.length && <details><summary>最新进度事件</summary>{events.map((e,i) => <pre key={i}>{JSON.stringify(e,null,2)}</pre>)}</details>}
    {detail?.status === 'waiting_approval' && <section className="remote-review"><h3>报告审批</h3>{report ? <><p>版本 {report.report_version_id}</p><article className="markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{report.report.content}</ReactMarkdown></article></> : <p>正在获取完整版本；若内容超限，请在电脑审批。</p>}<button disabled={!online || sending || !!operation || !report?.report?.content || report.report_version_id !== detail.current_report_version_id || !report.content_hash} onClick={() => void decide('report.decide',{report_version_id:report!.report_version_id,approved:true})}>批准此版本</button><button disabled={!online || sending || !!operation || !report?.report?.content} onClick={() => void decide('report.decide',{report_version_id:report!.report_version_id,approved:false})}>拒绝此版本</button></section>}
    {changes.map(c => <button className="remote-task" key={c.id} onClick={() => {setChange(null); void transport.call<FileChangeDetail>('file_change.read',{workspace_id:c.workspace_id,change_id:c.id}).then(setChange).catch(e => setError(e.message))}}>{c.path}<span>{c.status} · 查看完整差异</span></button>)}{change && <section className="remote-review"><h3>{change.path}</h3><p>{change.reason}</p><pre>{change.diff}</pre><details><summary>原文件完整内容</summary><pre>{change.original_content}</pre></details><details><summary>建议文件完整内容</summary><pre>{change.proposed_content}</pre></details>{[true,false].map(approved => <button key={String(approved)} disabled={!online || sending || !!operation || change.status !== 'pending' || !/^[a-f0-9]{64}$/i.test(change.review_hash) || typeof change.original_content !== 'string' || typeof change.proposed_content !== 'string' || typeof change.diff !== 'string'} onClick={() => void decide('file_change.decide',{workspace_id:change.workspace_id,change_id:change.id,review_hash:change.review_hash,approved})}>{approved ? '批准并应用' : '拒绝提案'}</button>)}</section>}</section>}
    </>}<footer className="remote-footnote">通过你选择的受信中继连接 · 中继可见传输内容<br />目录与模型配置请在电脑管理</footer></main>
}



