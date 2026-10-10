import { useCallback, useEffect, useState } from 'react'
import { Archive, BookMarked, CheckCircle2, MessageSquarePlus, Plus, Search, Send, Sparkle, Trash2, UserRound } from 'lucide-react'
import { api } from '../api'
import type { MemoryRecord, SessionMessage, SkillRecord } from '../types'

function Sessions({ notify }: { notify: (message: string) => void }) {
  const [sessionId, setSessionId] = useState('')
  const [activeId, setActiveId] = useState('')
  const [messages, setMessages] = useState<SessionMessage[]>([])
  const [role, setRole] = useState('user')
  const [content, setContent] = useState('')

  async function load(id = sessionId) {
    if (!id.trim()) return
    try { setMessages((await api.session(id.trim())).messages); setActiveId(id.trim()) }
    catch (error) { notify(error instanceof Error ? error.message : '会话读取失败') }
  }
  async function append(event: React.FormEvent) {
    event.preventDefault(); if (!activeId || !content.trim()) return
    try { await api.appendMessage(activeId, role, content.trim()); setContent(''); await load(activeId) }
    catch (error) { notify(error instanceof Error ? error.message : '消息写入失败') }
  }

  return <div className="page knowledge-page"><header className="page-heading compact"><div><p className="eyebrow">CONVERSATION ARCHIVE</p><h1>会话记录</h1><p>按会话 ID 查看与补充长期研究上下文。</p></div></header>
    <div className="session-layout"><aside className="paper-panel session-finder"><MessageSquarePlus /><h2>打开会话</h2><p>输入研究任务使用的 session_id。</p><div className="inline-input"><input value={sessionId} onChange={(e) => setSessionId(e.target.value)} placeholder="例如 thesis-2026" onKeyDown={(e) => e.key === 'Enter' && void load()} /><button onClick={() => void load()}><Search /></button></div>{activeId && <div className="active-session"><span>当前会话</span><code>{activeId}</code><small>{messages.length} 条消息</small></div>}</aside>
      <section className="paper-panel messages-panel">{activeId ? <><div className="messages">{messages.length ? messages.map((message) => <article key={message.id} className={`message ${message.role}`}><div><UserRound size={15} /><b>{message.role}</b><time>{new Date(message.created_at * 1000).toLocaleString('zh-CN')}</time></div><p>{message.content}</p></article>) : <div className="empty-state"><MessageSquarePlus /><p>这个会话还是空的</p></div>}</div><form className="message-composer" onSubmit={append}><select value={role} onChange={(e) => setRole(e.target.value)}><option value="user">用户</option><option value="assistant">助手</option><option value="system">系统</option><option value="tool">工具</option></select><textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="写入一条上下文消息…" rows={2} /><button className="primary"><Send /></button></form></> : <div className="empty-state tall"><MessageSquarePlus /><p>先打开一个会话</p><small>会话消息用于隔离不同研究上下文。</small></div>}</section></div>
  </div>
}

function MemoryForm({ afterSave, notify }: { afterSave: () => Promise<void>; notify: (message: string) => void }) {
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState({ title: '', content: '', kind: 'semantic', scope: 'owner', source_type: 'manual', source_id: '' })
  async function save(event: React.FormEvent) {
    event.preventDefault()
    try { await api.createMemory({ ...form, source_id: form.source_id || `manual:${Date.now()}` }); setOpen(false); setForm({ ...form, title: '', content: '', source_id: '' }); await afterSave(); notify('候选记忆已创建') }
    catch (error) { notify(error instanceof Error ? error.message : '记忆创建失败') }
  }
  return <>{<button className="primary" onClick={() => setOpen(true)}><Plus />新建记忆</button>}{open && <div className="modal-backdrop" onMouseDown={() => setOpen(false)}><form className="modal" onSubmit={save} onMouseDown={(e) => e.stopPropagation()}><header><div><p className="eyebrow">NEW MEMORY</p><h2>写入候选记忆</h2></div><button type="button" className="icon-button" onClick={() => setOpen(false)}>×</button></header><label className="field"><span>标题</span><input required value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} /></label><label className="field"><span>内容</span><textarea required rows={7} value={form.content} onChange={(e) => setForm({ ...form, content: e.target.value })} /></label><div className="form-grid"><label className="field"><span>类型</span><select value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}><option value="semantic">语义</option><option value="episodic">情景</option><option value="procedural">程序</option></select></label><label className="field"><span>范围</span><select value={form.scope} onChange={(e) => setForm({ ...form, scope: e.target.value })}><option value="owner">用户</option><option value="project">项目</option><option value="session">会话</option></select></label></div><footer><button type="button" className="secondary" onClick={() => setOpen(false)}>取消</button><button className="primary">保存候选</button></footer></form></div>}</>
}

function Memories({ notify }: { notify: (message: string) => void }) {
  const [status, setStatus] = useState('candidate')
  const [items, setItems] = useState<MemoryRecord[]>([])
  const [query, setQuery] = useState('')
  const load = useCallback(async () => { try { setItems((await api.memories(status)).memories) } catch (error) { notify(error instanceof Error ? error.message : '记忆读取失败') } }, [status, notify])
  useEffect(() => { void load() }, [load])
  async function action(run: () => Promise<unknown>, message: string) { try { await run(); notify(message); await load() } catch (error) { notify(error instanceof Error ? error.message : '操作失败') } }
  async function search() { if (!query.trim()) return void load(); try { setItems((await api.searchMemories(query.trim())).memories) } catch (error) { notify(error instanceof Error ? error.message : '搜索失败') } }
  return <div className="page knowledge-page"><header className="page-heading compact row"><div><p className="eyebrow">CURATED MEMORY</p><h1>记忆库</h1><p>候选内容经审核后才会进入有效记忆。</p></div><MemoryForm afterSave={load} notify={notify} /></header>
    <div className="memory-toolbar"><div className="tabs">{['candidate', 'active', 'stale'].map((item) => <button className={status === item ? 'active' : ''} onClick={() => setStatus(item)} key={item}>{item === 'candidate' ? '待审核' : item === 'active' ? '有效' : '已失效'}</button>)}</div><div className="search-box"><Search /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索有效记忆" onKeyDown={(e) => e.key === 'Enter' && void search()} /><button onClick={() => void search()}>检索</button></div></div>
    {items.length ? <div className="memory-grid">{items.map((item) => <article className="memory-card" key={item.id}><header><span>{item.kind}</span><code>{item.scope}</code></header><h2>{item.title}</h2><p>{item.content || '正文保存在 Markdown 文件中'}</p><div className="memory-meta"><span>{item.source_type || 'unknown'}</span><span>{item.source_id || item.id}</span></div><footer>{item.status === 'candidate' && item.kind !== 'procedural' && <button onClick={() => void action(() => api.activateMemory(item.id), '记忆已激活')}><CheckCircle2 />激活</button>}{item.status === 'active' && <button onClick={() => void action(() => api.invalidateMemory(item.id), '记忆已失效')}><Archive />失效</button>}{item.kind === 'procedural' && item.status === 'candidate' && <button onClick={() => { const evaluation = window.prompt('评测记录 ID'); const reviewer = window.prompt('审核者'); if (evaluation && reviewer) void action(() => api.publishSkill(item.id, evaluation, reviewer), 'Skill 已发布') }}><Sparkle />发布 Skill</button>}<button className="danger-text" onClick={() => window.confirm('确认删除这条记忆？') && void action(() => api.deleteMemory(item.id), '记忆已删除')}><Trash2 />删除</button></footer></article>)}</div> : <div className="paper-panel empty-state tall"><BookMarked /><p>当前分类没有记忆</p><small>创建候选记忆，审核后将其激活。</small></div>}
  </div>
}

function Skills({ notify }: { notify: (message: string) => void }) {
  const [skills, setSkills] = useState<SkillRecord[]>([])
  const [selected, setSelected] = useState<SkillRecord | null>(null)
  const [errors, setErrors] = useState<string[]>([])
  const load = useCallback(async () => { try { const data = await api.skills(); setSkills(data.skills); setErrors(data.errors || []) } catch (error) { notify(error instanceof Error ? error.message : 'Skills 读取失败') } }, [notify])
  useEffect(() => { void load() }, [load])
  async function read(skill: SkillRecord) { if (!skill.id) return; try { setSelected(await api.readSkill(skill.id)) } catch (error) { notify(error instanceof Error ? error.message : 'Skill 正文读取失败') } }
  return <div className="page knowledge-page"><header className="page-heading compact"><div><p className="eyebrow">PROCEDURAL LIBRARY</p><h1>Skills</h1><p>本地 Skill 包与经人工发布的程序记忆，任务执行时按需加载完整指令。</p></div><button className="secondary" onClick={() => void load()}>刷新目录</button></header>
    {!!errors.length && <div className="task-errors" role="alert">{errors.map((error, index) => <p key={index}>{error}</p>)}</div>}
    {skills.length ? <div className="skill-list">{skills.map((skill, index) => <article className="paper-panel" key={String(skill.id || index)}><div className="skill-index">{String(index + 1).padStart(2, '0')}</div><div><span>{skill.source === 'local' ? 'LOCAL SKILL' : 'PUBLISHED PROCEDURE'}</span><h2>{String(skill.name || skill.title || '未命名 Skill')}</h2><p>{skill.description}</p><code>{skill.id} · {String(skill.version || 'v1').slice(0, 12)}</code></div><button className="secondary" onClick={() => void read(skill)}>查看指令</button></article>)}</div> : <div className="paper-panel empty-state tall"><Sparkle /><p>暂无可用 Skill</p><small>添加本地 SKILL.md，或在记忆库发布候选程序记忆。</small></div>}
    {selected && <div className="modal-backdrop" onClick={() => setSelected(null)}><section className="modal skill-modal" onClick={(event) => event.stopPropagation()}><header><h2>{selected.name || selected.title}</h2><button className="secondary" onClick={() => setSelected(null)}>关闭</button></header><pre>{selected.content}</pre></section></div>}
  </div>
}

export function KnowledgeViews({ mode, notify }: { mode: 'sessions' | 'memories' | 'skills'; notify: (message: string) => void }) {
  if (mode === 'sessions') return <Sessions notify={notify} />
  if (mode === 'memories') return <Memories notify={notify} />
  return <Skills notify={notify} />
}
