import { useEffect, useState } from 'react'
import { api } from '../api'
import type { FileChange, FileChangeDetail } from '../types'

const labels = { pending: '待你确认', applying: '写入待核对', applied: '已写入', rejected: '已拒绝', conflict: '文件冲突' }

function ChangeCard({ change, blocked, refresh }: { change: FileChange; blocked: boolean; refresh: () => void }) {
  const [detail, setDetail] = useState<FileChangeDetail | null>(null)
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function preview() {
    if (open) { setOpen(false); return }
    setBusy(true); setError('')
    try { setDetail(await api.fileChange(change.workspace_id, change.id)); setOpen(true) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  async function decide(approved: boolean) {
    if (!detail || busy || (approved && blocked)) return
    if (approved && !window.confirm(`确认将提案写入原文件 ${change.path}？这不是沙盒操作。`)) return
    setBusy(true); setError('')
    try { await api.decideFileChange(change.workspace_id, detail, approved); refresh() }
    catch (err) { setError((err as Error).message); refresh() }
    finally { setBusy(false) }
  }
  return <section className={`file-change-card state-${change.status}`}>
    <header><span>文件修改提案</span><small>{labels[change.status]}</small></header>
    <strong>{change.path}</strong><p>{change.reason}</p>
    <button className="subtle-button" disabled={busy} onClick={() => void preview()}>{open ? '收起差异' : '查看差异'}</button>
    {open && detail && <>
      <pre className="file-change-diff" aria-label="文件修改差异">{detail.diff ? detail.diff.split('\n').map((line, index) => <span key={index} className={line.startsWith('+') ? 'added' : line.startsWith('-') ? 'removed' : line.startsWith('@@') ? 'hunk' : ''}>{line || ' '}</span>) : '内容无变化'}</pre>
      {(change.status === 'pending' || change.status === 'applying') && <div className="file-change-actions">
        <button disabled={busy || blocked} onClick={() => void decide(true)}>{change.status === 'applying' ? '核对并完成写入' : '确认写入原文件'}</button>
        {change.status === 'pending' && <button className="subtle-button" disabled={busy} onClick={() => void decide(false)}>拒绝提案</button>}
      </div>}
      {blocked && change.status === 'pending' && <small>请先保存或放弃编辑器中的修改，再确认写入。</small>}
    </>}
    {change.status === 'applied' && <small>原文件已更新；已打开的编辑器需重新读取。</small>}
    {change.status === 'conflict' && <small>未覆盖文件。请让 Agent 重新读取并生成新提案。</small>}
    {error && <p role="alert" className="file-change-error">{error}</p>}
  </section>
}

export function FileChanges({ workspace, run, ongoing, blocked }: { workspace: string; run: string; ongoing: boolean; blocked: boolean }) {
  const [changes, setChanges] = useState<FileChange[]>([])
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    let loading = false
    async function load() {
      if (loading) return
      loading = true
      try { const result = await api.fileChanges(workspace, run); if (active) { setChanges(result.changes); setError('') } }
      catch (err) { if (active) setError((err as Error).message) }
      finally { loading = false }
    }
    void load()
    const timer = ongoing ? window.setInterval(() => void load(), 2500) : undefined
    return () => { active = false; window.clearInterval(timer) }
  }, [workspace, run, ongoing, revision])
  return <div className="file-changes">{changes.map(change => <ChangeCard key={change.id} change={change} blocked={blocked} refresh={() => setRevision(value => value + 1)} />)}{error && <p className="file-change-error" role="alert">修改提案加载失败：{error} <button onClick={() => setRevision(value => value + 1)}>重试</button></p>}</div>
}
