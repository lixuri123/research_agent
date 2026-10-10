import { useEffect, useState } from 'react'
import { Shield, RefreshCw } from 'lucide-react'
import { api } from '../api'
import type { ExecutionPermission, ExecutionRecord } from '../types'

export function ExecutionAccess({ workspace }: { workspace: string }) {
  const [permission, setPermission] = useState<ExecutionPermission | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true
    setPermission(null); setError('')
    void api.executionPermission(workspace).then(value => { if (active) setPermission(value) })
      .catch(err => { if (active) setError((err as Error).message) })
    return () => { active = false }
  }, [workspace, revision])
  async function toggle() {
    if (!permission || busy) return
    const enabled = !permission.enabled
    if (enabled && !window.confirm('允许此工作空间使用固定文本检索？材料副本与命中会进入任务记录及模型上下文。Windows 后端仅提供部分写限制，不隔离工作区外读取或网络；不会开放任意脚本，也不会修改原文件。')) return
    setBusy(true); setError('')
    try { setPermission(await api.setExecutionPermission(workspace, enabled)) }
    catch (err) { setError((err as Error).message) }
    finally { setBusy(false) }
  }
  return <details className="execution-access"><summary><Shield size={14} />本地受限执行<span>{permission?.enabled ? permission.backend.available ? '已授权' : '未就绪' : '未授权'}</span></summary>
    <p>固定文本检索 · 原文件只读<br />部分写限制，不隔离读取和网络。</p>
    {permission && <><small>{permission.backend.available ? 'Windows 执行器可用' : permission.backend.error || '执行器不可用'}</small>
      <button className="subtle-button" disabled={busy || (!permission.enabled && (!permission.agent_read || !permission.backend.available))} onClick={() => void toggle()}>{busy ? '正在更新…' : permission.enabled ? '撤回执行授权' : '授权固定检索'}</button>
      {!permission.agent_read && <small>需要先允许 Agent 读取材料。</small>}</>}
    {error && <p role="alert">{error}</p>}<button className="subtle-button" disabled={busy} onClick={() => setRevision(value => value + 1)}><RefreshCw size={12} />刷新状态</button>
  </details>
}

const labels: Record<string, string> = { prepared: '准备材料', running: '正在检索', succeeded: '检索完成', failed: '执行失败', unavailable: '后端不可用', timeout: '已超时' }

export function ExecutionLog({ workspace, run, ongoing }: { workspace: string; run: string; ongoing: boolean }) {
  const [records, setRecords] = useState<ExecutionRecord[]>([])
  const [error, setError] = useState('')
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let active = true, loading = false
    setRecords([]); setError('')
    async function load() {
      if (loading) return
      loading = true
      try { const value = await api.executions(workspace, run); if (active) { setRecords(value.executions); setError('') } }
      catch (err) { if (active) setError((err as Error).message) }
      finally { loading = false }
    }
    void load()
    const timer = ongoing ? window.setInterval(() => void load(), 2500) : undefined
    return () => { active = false; window.clearInterval(timer) }
  }, [workspace, run, ongoing, revision])
  return <div className="execution-log">{records.map(record => <details key={record.id} className="agent-process"><summary>本地文本检索 · {labels[record.status] || record.status}</summary>
    <p>字面量查询：<code>{record.arguments.query}</code></p><small>只读材料副本 · {record.manifest.length} 个输入文件 · 部分隔离</small>
    {!!record.result?.truncated && <p>输入或输出已截断，结果不代表完整工作空间。</p>}
    {!!record.result?.error && <p role="alert">{record.result.error}</p>}
    {record.result?.matches.map((match, index) => <div className="execution-match" key={index}><code>{match.path}:{match.line}</code><pre>{match.text}</pre></div>)}
    {record.result && !record.result.matches.length && !record.result.error && <p>本次输入中未得到匹配。</p>}
  </details>)}{error && <p role="alert">执行记录读取失败：{error} <button onClick={() => setRevision(value => value + 1)}>重试</button></p>}</div>
}
