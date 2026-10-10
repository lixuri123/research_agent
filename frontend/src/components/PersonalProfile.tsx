import { useCallback, useEffect, useState } from 'react'
import type { FormEvent } from 'react'
import { Check, ChevronRight, History, Pencil, Plus, RefreshCw, Trash2, UserRound, X } from 'lucide-react'
import { api } from '../api'
import type { JsonValue, ProfileAttribute, ProfileDraft } from '../types'
import '../profile.css'

const states = { candidate: '待确认', active: '已确认', stale: '已失效' }
const bases = { user_stated: '本人填写', user_confirmed: '本人确认', inferred: '待核对的推测' }
const scopes = { person: '个人', project: '项目', session: '会话' }
const sources: Record<string, string> = { user_edit: '手动填写', message: '对话', material: '材料' }
const date = (stamp: number) => new Date(stamp * 1000).toLocaleString('zh-CN')
type ValueKind = 'text' | 'number' | 'boolean' | 'structured' | 'empty'

function Value({ value, depth = 0 }: { value: JsonValue; depth?: number }) {
  if (value === null) return <span className="profile-muted">未填写</span>
  if (typeof value === 'boolean') return <span>{value ? '是' : '否'}</span>
  if (typeof value !== 'object') return <span className="profile-text">{String(value)}</span>
  if (depth > 4) return <pre className="profile-nested">{JSON.stringify(value, null, 2)}</pre>
  if (Array.isArray(value)) return value.length ? <ul className="profile-list">{value.map((item, index) => <li key={index}><Value value={item} depth={depth + 1} /></li>)}</ul> : <span className="profile-muted">空列表</span>
  return Object.keys(value).length ? <dl className="profile-object">{Object.entries(value).map(([key, item]) => <div key={key}><dt>{key}</dt><dd><Value value={item} depth={depth + 1} /></dd></div>)}</dl> : <span className="profile-muted">空对象</span>
}

function AttributeEditor({ attribute, onSave, onCancel }: { attribute: ProfileAttribute | null; onSave: (draft: ProfileDraft) => Promise<void>; onCancel: () => void }) {
  const [path, setPath] = useState<string[]>(attribute ? [...attribute.path] : [''])
  const [kind, setKind] = useState<ValueKind>(() => attribute?.value === null ? 'empty' : Array.isArray(attribute?.value) || typeof attribute?.value === 'object' ? 'structured' : typeof attribute?.value === 'number' ? 'number' : typeof attribute?.value === 'boolean' ? 'boolean' : 'text')
  const [value, setValue] = useState(() => attribute ? typeof attribute.value === 'string' ? attribute.value : JSON.stringify(attribute.value, null, 2) : '')
  const [scope, setScope] = useState<ProfileDraft['scope']>(attribute?.scope || 'person')
  const [scopeId, setScopeId] = useState(attribute?.scope_id || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError('')
    try {
      const parts = path.map((part) => part.trim())
      if (parts.some((part) => !part)) throw new Error('请填写每一层的名称，或移除不需要的层次。')
      let parsed: JsonValue = value
      if (kind === 'number') {
        if (!value.trim() || !Number.isFinite(Number(value))) throw new Error('请填写有效数字。')
        parsed = Number(value)
      } else if (kind === 'boolean') parsed = value === 'true'
      else if (kind === 'empty') parsed = null
      else if (kind === 'structured') {
        try { parsed = JSON.parse(value) as JsonValue } catch { throw new Error('内容格式有误，请检查列表或对象中的引号、逗号和括号。') }
        if (parsed === null || typeof parsed !== 'object') throw new Error('此类型需填写列表或对象；普通文字请选择“文本”。')
      }
      if (scope !== 'person' && !scopeId.trim()) throw new Error('请填写适用的项目或会话 ID。')
      setBusy(true)
      await onSave({ path: parts, value: parsed, basis: 'user_stated',
        source_refs: [...(attribute?.source_refs || []).slice(-19), { type: 'user_edit', id: 'profile-ui' }],
        scope, scope_id: scope === 'person' ? null : scopeId.trim() })
    } catch (err) { setError(err instanceof Error ? err.message : '保存失败') }
    finally { setBusy(false) }
  }
  return <form className="profile-editor" onSubmit={(event) => void submit(event)}>
    <div className="profile-editor-heading"><strong>{attribute ? '修正这条认识' : '添加一条认识'}</strong><button type="button" className="icon-button" onClick={onCancel} disabled={busy} aria-label="关闭属性编辑"><X size={17} /></button></div>
    <fieldset className="profile-path-editor"><legend>属性路径</legend>{path.map((part, index) => <div className="profile-path-row" key={index}><label>{index === path.length - 1 ? '属性名称' : `上级分类 ${index + 1}`}<input aria-label={index === path.length - 1 ? '属性名称' : `上级分类 ${index + 1}`} autoFocus={index === path.length - 1} value={part} onChange={(event) => setPath((current) => current.map((name, position) => position === index ? event.target.value : name))} placeholder={index === path.length - 1 ? '例如：研究方向' : '例如：学习经历'} maxLength={120} required /></label>{path.length > 1 && <button type="button" className="icon-button" disabled={busy} aria-label={`移除第 ${index + 1} 层`} onClick={() => setPath((current) => current.filter((_, position) => position !== index))}><X size={14} /></button>}</div>)}<div className="profile-path-tools"><small>每一层单独填写，名称由你决定。</small><button type="button" className="secondary" disabled={busy || path.length >= 12} onClick={() => setPath((current) => [...current.slice(0, -1), '', current[current.length - 1]])}><Plus size={13} />添加分类</button></div></fieldset>
    <div className="profile-editor-row"><label>内容类型<select aria-label="内容类型" value={kind} onChange={(event) => { const next = event.target.value as ValueKind; setKind(next); setValue(next === 'boolean' ? 'true' : next === 'structured' ? '[]' : '') }}><option value="text">文本</option><option value="number">数字</option><option value="boolean">是 / 否</option><option value="structured">列表或对象</option><option value="empty">暂未确定</option></select></label><label>适用范围<select aria-label="适用范围" value={scope} onChange={(event) => setScope(event.target.value as ProfileDraft['scope'])}><option value="person">个人</option><option value="project">特定项目</option><option value="session">特定会话</option></select></label></div>
    {scope !== 'person' && <label>范围 ID<input aria-label="范围 ID" value={scopeId} onChange={(event) => setScopeId(event.target.value)} placeholder="对应的工作空间或会话 ID" required maxLength={255} /></label>}
    {kind === 'boolean' ? <label>内容<select aria-label="内容" value={value === 'true' ? 'true' : 'false'} onChange={(event) => setValue(event.target.value)}><option value="true">是</option><option value="false">否</option></select></label> : kind !== 'empty' && <label>内容{kind === 'number' ? <input aria-label="内容" value={value} onChange={(event) => setValue(event.target.value)} inputMode="decimal" required /> : <textarea aria-label="内容" rows={kind === 'structured' ? 6 : 3} value={value} onChange={(event) => setValue(event.target.value)} maxLength={32768} placeholder={kind === 'structured' ? '["论文", "实习"] 或 {"每周时间": 8}' : '写下你希望保存的信息'} />}{kind === 'structured' && <small>列表可保存多项内容，对象可保存带名称的内容。</small>}</label>}
    {error && <p className="profile-error" role="alert">{error}</p>}
    <div className="profile-editor-footer"><small>保存后可核对并确认。</small><button type="button" className="secondary" onClick={onCancel} disabled={busy}>取消</button><button type="submit" className="profile-primary" disabled={busy}>{busy ? '正在保存…' : '保存'}</button></div>
  </form>
}

export function PersonalProfile({ notify }: { notify: (message: string) => void }) {
  const [attributes, setAttributes] = useState<ProfileAttribute[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [filter, setFilter] = useState('all')
  const [editing, setEditing] = useState<ProfileAttribute | null | undefined>(undefined)
  const [busy, setBusy] = useState(false)
  const [history, setHistory] = useState<ProfileAttribute[] | null>(null)
  const load = useCallback(async () => {
    setError('')
    try { setAttributes((await api.profile()).attributes) }
    catch (err) { setError(err instanceof Error ? err.message : '人物描述读取失败') }
    finally { setLoading(false) }
  }, [])
  useEffect(() => { void load() }, [load])

  async function action(run: () => Promise<unknown>, message: string) {
    setBusy(true)
    try { await run(); notify(message); await load() }
    catch (err) { setError(err instanceof Error ? err.message : '操作失败，请刷新后重试') }
    finally { setBusy(false) }
  }
  async function save(draft: ProfileDraft) {
    if (editing) await api.updateProfileAttribute(editing.id, draft, editing.version)
    else await api.createProfileAttribute(draft)
    setEditing(undefined); notify('人物属性已保存，待你确认'); await load()
  }
  async function showHistory(item: ProfileAttribute) {
    setBusy(true)
    try { setHistory((await api.profileHistory(item.id)).versions) }
    catch (err) { setError(err instanceof Error ? err.message : '历史读取失败') }
    finally { setBusy(false) }
  }
  const visible = attributes.filter((item) => filter === 'all' || item.status === filter)
  const groups = new Map<string, ProfileAttribute[]>()
  visible.forEach((item) => { const group = item.path.slice(0, -1).join(' / ') || '个人'; groups.set(group, [...(groups.get(group) || []), item]) })

  return <div className="profile-page">
    <header className="profile-intro"><span className="profile-eyebrow">PERSONAL NOTES</span><h1>关于我的认识</h1><p>随着交流，逐渐补充，也随时修正。</p><div className="profile-summary"><span>{attributes.length} 条认识 · {attributes.filter((item) => item.status === 'active').length} 条已确认</span><button className="icon-button" aria-label="刷新人物描述" onClick={() => void load()} disabled={busy}><RefreshCw size={16} /></button></div></header>
    <div className="profile-context-note">当前只保存人物描述，尚未用于回答问题。</div>
    <div className="profile-toolbar"><div className="profile-filters">{[['all', '全部'], ['active', '已确认'], ['candidate', '待确认'], ['stale', '已失效']].map(([key, label]) => <button key={key} className={filter === key ? 'selected' : ''} onClick={() => setFilter(key)} aria-pressed={filter === key}>{label}</button>)}</div><button className="profile-primary" onClick={() => setEditing(null)} disabled={busy || editing !== undefined}><Plus size={15} />添加属性</button></div>
    {error && <div className="profile-error" role="alert">{error}<button className="secondary" onClick={() => void load()}>重新读取</button></div>}
    {editing !== undefined && <AttributeEditor key={editing?.id || 'new'} attribute={editing} onSave={save} onCancel={() => setEditing(undefined)} />}
    {loading ? <p role="status" className="profile-muted">正在读取人物描述…</p> : visible.length ? <div className="profile-tree">{Array.from(groups).map(([group, items]) => <section className="profile-group" key={group}><h2><span />{group}<small>{items.length}</small></h2>{items.map((item) => <article className={`profile-attribute ${item.status}`} key={item.id}><header><h3>{item.path.at(-1)}</h3><span className={`profile-state ${item.status}`}>{states[item.status]}</span></header><div className="profile-value"><Value value={item.value} /></div><div className="profile-provenance"><span>{bases[item.basis]}</span><span>{scopes[item.scope]}{item.scope_id ? ` · ${item.scope_id}` : ''}</span><span>{date(item.updated_at)}</span></div><details className="profile-details"><summary><ChevronRight size={13} />来源与版本</summary><p>第 {item.version} 版 · 建立于 {date(item.created_at)}</p>{item.source_refs.map((source, index) => <p key={index}>{sources[source.type] || source.type} · {source.id === 'profile-ui' ? '关于我面板' : source.id}</p>)}</details><footer>{item.status !== 'active' && <button disabled={busy} onClick={() => void action(() => api.confirmProfileAttribute(item.id, item.version), '人物属性已确认')}><Check size={14} />确认</button>}<button disabled={busy || editing !== undefined} onClick={() => setEditing(item)}><Pencil size={14} />修改</button>{item.status !== 'stale' && <button disabled={busy} onClick={() => void action(() => api.invalidateProfileAttribute(item.id, item.version), '人物属性已失效')}>标记失效</button>}<button disabled={busy} onClick={() => void showHistory(item)}><History size={14} />历史</button><button className="profile-delete" disabled={busy} onClick={() => window.confirm('删除这条属性及其修改历史？') && void action(() => api.deleteProfileAttribute(item.id, item.version), '人物属性已删除')}><Trash2 size={14} />删除</button></footer></article>)}</section>)}</div> : <div className="profile-empty"><UserRound size={30} strokeWidth={1.3} /><h2>{attributes.length ? '这个分类还没有内容' : '从一条认识开始'}</h2><p>{attributes.length ? '切换分类，或添加新的属性。' : '记录你愿意保存的信息。属性可以逐步增加。'}</p></div>}
    {history && <section className="profile-history" role="dialog" aria-modal="true" aria-label="人物属性修改历史"><header><h2>修改历史</h2><button className="icon-button" aria-label="关闭修改历史" onClick={() => setHistory(null)}><X size={18} /></button></header>{[...history].reverse().map((item) => <article key={item.version}><div><strong>第 {item.version} 版</strong><span>{states[item.status]} · {bases[item.basis]} · {date(item.updated_at)}</span></div><h3>{item.path.join(' / ')}</h3><Value value={item.value} /><small>{scopes[item.scope]}{item.scope_id ? ` · ${item.scope_id}` : ''} · {item.source_refs.map((source) => `${sources[source.type] || source.type}: ${source.id}`).join('；')}</small></article>)}</section>}
  </div>
}
