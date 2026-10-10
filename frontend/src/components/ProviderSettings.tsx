import { useEffect, useState } from 'react'
import { CheckCircle2, KeyRound, LoaderCircle, Plug, Plus, Save, Trash2 } from 'lucide-react'
import { api } from '../api'
import type { MultiProviderDraft, ProviderEntry, ProviderRegistry } from '../types'
import '../provider.css'

const roles = [['planner_model', '规划模型'], ['reviewer_model', '审核模型'], ['writer_model', '写作模型'], ['summary_model', '摘要模型']] as const
const empty = (): MultiProviderDraft => ({ name: '', base_url: '', api_key: '', models: [], model: '', planner_model: '', reviewer_model: '', writer_model: '', summary_model: '' })
function draftOf(provider: ProviderEntry): MultiProviderDraft {
  const { id: _, revision: _revision, has_api_key: _key, ...fields } = provider
  return { ...fields, api_key: '' }
}

export function ProviderSettings() {
  const [catalog, setCatalog] = useState<ProviderRegistry | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [draft, setDraft] = useState<MultiProviderDraft | null>(null)
  const [modelText, setModelText] = useState('')
  const [loading, setLoading] = useState(true)
  const [action, setAction] = useState<string | null>(null)
  const [result, setResult] = useState<{ ok: boolean; message: string } | null>(null)
  const [loadError, setLoadError] = useState('')
  const [reload, setReload] = useState(0)
  const saved = catalog?.providers.find((provider) => provider.id === selected)
  const models = [...new Set(modelText.split('\n').map((value) => value.trim()).filter(Boolean))]
  const dirty = !!draft && JSON.stringify({ ...draft, models }) !== JSON.stringify(saved ? draftOf(saved) : empty())
  function show(provider?: ProviderEntry) {
    setSelected(provider?.id || null); setDraft(provider ? draftOf(provider) : empty())
    setModelText(provider?.models.join('\n') || ''); setResult(null)
  }
  useEffect(() => {
    let cancelled = false
    setLoading(true); setLoadError('')
    api.providers().then((values) => {
      if (!cancelled) { setCatalog(values); show(values.providers.find((p) => p.id === values.active_provider_id) || values.providers[0]) }
    }).catch((error: unknown) => { if (!cancelled) setLoadError(error instanceof Error ? error.message : '无法读取服务配置') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [reload])
  function switchTo(provider?: ProviderEntry) {
    if (action || (dirty && !window.confirm('当前配置有未保存修改，放弃修改？'))) return
    show(provider)
  }
  function edit(field: keyof MultiProviderDraft, value: string) {
    setDraft((current) => current && { ...current, [field]: value }); setResult(null)
  }
  function editModels(text: string) {
    setModelText(text); setResult(null)
    const names = [...new Set(text.split('\n').map((value) => value.trim()).filter(Boolean))]
    setDraft((current) => {
      if (!current) return current
      const next = { ...current, models: names, model: names.includes(current.model) ? current.model : names[0] || '' }
      for (const [field] of roles) if (!names.includes(next[field])) next[field] = ''
      return next
    })
  }
  async function run(kind: 'save' | 'test' | 'delete' | 'default') {
    if (!draft || action) return
    if (kind === 'delete' && (!saved || !window.confirm(`删除“${saved.name}”？已有任务使用的历史配置会保留。`))) return
    setAction(kind); setResult(null)
    try {
      if (kind === 'test') setResult(await api.testProviderDraft({ ...draft, models }, selected || undefined))
      else if (kind === 'default' && saved) {
        setCatalog(await api.defaultProvider({ provider_id: saved.id, model: saved.model }))
        setResult({ ok: true, message: '已设为默认服务。' })
      } else {
        const updated = kind === 'delete' ? (await api.deleteProvider(selected!), null) : selected ? await api.updateProvider(selected, { ...draft, models }) : await api.createProvider({ ...draft, models })
        const values = await api.providers(); setCatalog(values)
        show(updated ? values.providers.find((p) => p.id === updated.id) : values.providers.find((p) => p.id === values.active_provider_id) || values.providers[0])
        setResult({ ok: true, message: kind === 'delete' ? '服务已删除，已有任务继续使用提交时的配置。' : '服务已保存，可以在对话输入框旁选择模型。' })
      }
    } catch (error) { setResult({ ok: false, message: error instanceof Error ? error.message : '操作失败，请重试' }) }
    finally { setAction(null) }
  }
  return <div className="page provider-page multi-provider-page">
    <header className="provider-heading"><p className="eyebrow">MODEL CONNECTIONS</p><h1>模型 Provider</h1><p>管理多个模型服务，在每次对话中选择适合的模型。</p></header>
    {loading ? <p role="status" className="provider-loading"><LoaderCircle size={18} />正在读取配置…</p> : loadError ? <div className="provider-result error" role="alert">{loadError}<button type="button" onClick={() => setReload((v) => v + 1)}>重新加载</button></div> : draft &&
      <div className="provider-manager"><aside className="provider-list" aria-label="模型服务列表"><div className="provider-list-heading"><span>{catalog?.providers.length || 0} 个服务</span><button type="button" disabled={!!action} onClick={() => switchTo()}><Plus size={14} />新增服务</button></div>
        {catalog?.providers.map((p) => <button type="button" key={p.id} disabled={!!action} className={`provider-item ${selected === p.id ? 'selected' : ''}`} onClick={() => switchTo(p)}><strong>{p.name}</strong><small>{p.models.length} 个模型 · {p.has_api_key ? '已配置密钥' : '免密钥'}</small>{catalog.active_provider_id === p.id && <span>默认</span>}</button>)}
        {!catalog?.providers.length && <p className="provider-note">添加一个服务，开始使用模型。</p>}</aside>
        <form onSubmit={(event) => { event.preventDefault(); void run('save') }}><fieldset disabled={!!action} className="provider-fields"><section className="provider-card">
          <div className="provider-card-title"><Plug size={19} /><h2>{selected ? '编辑服务' : '新建服务'}</h2><span>OpenAI 兼容</span></div>
          <label htmlFor="provider-name">服务名称<input id="provider-name" required maxLength={100} value={draft.name} onChange={(e) => edit('name', e.target.value)} placeholder="例如：研究服务、本地模型" /></label>
          <label htmlFor="provider-url">API 地址<input id="provider-url" type="url" required maxLength={2048} value={draft.base_url} onChange={(e) => edit('base_url', e.target.value)} placeholder="https://your-provider.example/v1" /><small>填写服务商提供的基础地址，通常包含 /v1。</small></label>
          <label htmlFor="provider-key"><span>API Key <KeyRound size={13} /></span><input id="provider-key" type="password" autoComplete="new-password" value={draft.api_key} onChange={(e) => edit('api_key', e.target.value)} placeholder={saved?.has_api_key ? '留空保留此服务的已有密钥' : '本地免密钥服务可留空'} /><small>密钥不会回显；每个服务独立保存。</small></label>
          <label htmlFor="provider-models">模型列表<textarea id="provider-models" required rows={4} value={modelText} onChange={(e) => editModels(e.target.value)} placeholder={'model-one\nmodel-two'} /><small>每行填写一个模型 ID，需与服务商支持的名称一致。</small></label>
          <label htmlFor="provider-model">默认模型<select aria-label="默认模型" id="provider-model" required value={draft.model} onChange={(e) => edit('model', e.target.value)}><option value="" disabled>选择默认模型</option>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>
        </section><details className="provider-card provider-roles"><summary>研究角色模型 <span>可选配置</span></summary><p>显式选择对话模型时，整个任务使用所选模型；未指定时可按角色使用下面的配置。</p><div className="provider-role-grid">{roles.map(([field, label]) => <label key={field} htmlFor={`provider-${field}`}>{label}<select aria-label={label} id={`provider-${field}`} value={draft[field]} onChange={(e) => edit(field, e.target.value)}><option value="">使用默认模型</option>{models.map((model) => <option key={model} value={model}>{model}</option>)}</select></label>)}</div></details>
        <div className="provider-actions">{saved && <button className="provider-delete" type="button" onClick={() => void run('delete')}><Trash2 size={15} />删除服务</button>}<button className="secondary" type="button" onClick={(event) => { if (event.currentTarget.form?.reportValidity()) void run('test') }}>{action === 'test' ? <LoaderCircle size={16} className="provider-spinner" /> : <Plug size={16} />}{action === 'test' ? '测试中…' : '测试连接'}</button><button className="primary" type="submit">{action === 'save' ? <LoaderCircle size={16} className="provider-spinner" /> : <Save size={16} />}{action === 'save' ? '保存中…' : '保存服务'}</button></div>
        {saved && catalog?.active_provider_id !== saved.id && <button type="button" className="provider-set-default" onClick={() => void run('default')}>将已保存的服务设为默认</button>}</fieldset>
        <p className="provider-note">测试会发送一次简短请求，可能产生少量费用；测试不会保存配置。</p>{result && <div className={`provider-result ${result.ok ? 'success' : 'error'}`} role={result.ok ? 'status' : 'alert'}>{result.ok && <CheckCircle2 size={18} />}<span>{result.message}</span></div>}
        </form></div>}
  </div>
}

