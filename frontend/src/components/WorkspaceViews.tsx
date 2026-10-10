import { useEffect, useRef, useState } from 'react'
import { ArrowLeft, FileText, Folder, FolderOpen, Plus, RefreshCw, Save, X } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { api } from '../api'
import type { Workspace, WorkspaceEntry, WorkspaceFile } from '../types'

type Notify = (message: string) => void
const errorText = (error: unknown) => error instanceof Error ? error.message : '操作失败，请重试'

export function WorkspacePicker({ workspaces, active, roots, onSelect, onBound, notify }: {
  workspaces: Workspace[]; active: string; roots: string[]; onSelect: (id: string) => void;
  onBound: (workspace: Workspace) => void; notify: Notify
}) {
  const [binding, setBinding] = useState(false)
  const [name, setName] = useState('')
  const [root, setRoot] = useState('')
  const [agentRead, setAgentRead] = useState(false)
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    if (!binding) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape' && !saving) setBinding(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [binding, saving])
  async function bind(event: React.FormEvent) {
    event.preventDefault()
    if (saving) return
    setSaving(true)
    try { const workspace = await api.bindWorkspace({ name: name.trim(), root: root.trim(), agent_read: agentRead }); onBound(workspace); setBinding(false) }
    catch (error) { notify(errorText(error)) }
    finally { setSaving(false) }
  }
  return <div className="workspace-picker">
    <label htmlFor="workspace-select">工作空间</label>
    <div><FolderOpen size={16} /><select id="workspace-select" value={active} onChange={(event) => onSelect(event.target.value)}><option value="">个人对话 · 未绑定目录</option>{workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><button className="icon-button" aria-label="绑定本地工作空间" onClick={() => { setBinding(true); setRoot(roots[0] || ''); setName(''); setAgentRead(false) }}><Plus size={16} /></button></div>
    {binding && <div className="workspace-bind-backdrop"><form className="workspace-bind" role="dialog" aria-modal="true" aria-labelledby="workspace-bind-title" onSubmit={(event) => void bind(event)}><header><FolderOpen size={20} /><h2 id="workspace-bind-title">打开一个真实的工作空间</h2><button type="button" className="icon-button" disabled={saving} aria-label="关闭绑定窗口" onClick={() => setBinding(false)}><X size={18} /></button></header><p>绑定 API 所在电脑上的目录，不上传或复制整个目录。文件修改会直接保存到这里。</p><label>名称<input autoFocus required maxLength={100} value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：我的复现项目" /></label><label>本地绝对路径<input required value={root} onChange={(event) => setRoot(event.target.value)} placeholder="D:\projects\my-project" /></label><div className="workspace-root-note">服务端允许的目录：{roots.map((path) => <code key={path}>{path}</code>)}其他目录需先配置 WORKSPACE_ALLOWED_ROOTS，再重启服务。</div><label className="workspace-permission"><input type="checkbox" checked={agentRead} onChange={(event) => setAgentRead(event.target.checked)} /><span>允许 Agent 按任务需要读取文本文件<small>读取内容会进入已配置模型的调用及任务记录；不会自动读取整个目录。不授权也可以手动浏览与编辑。</small></span></label><footer><span>Agent 只读 · 不执行命令</span><button type="submit" disabled={saving || !name.trim() || !root.trim()}>{saving ? '正在绑定…' : '绑定并打开'}</button></footer></form></div>}
  </div>
}

export function WorkspacePane({ workspace, notify, onClose, onDirty, onSaving }: {
  workspace: Workspace; notify: Notify; onClose: () => void; onDirty: (dirty: boolean) => void; onSaving: (saving: boolean) => void
}) {
  const [directory, setDirectory] = useState('')
  const [entries, setEntries] = useState<WorkspaceEntry[]>([])
  const [truncated, setTruncated] = useState(false)
  const [file, setFile] = useState<WorkspaceFile | null>(null)
  const [content, setContent] = useState('')
  const [preview, setPreview] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [reload, setReload] = useState(0)
  const requestId = useRef(0)
  const dirty = file !== null && content !== file.content
  useEffect(() => { onDirty(dirty) }, [dirty, onDirty])
  useEffect(() => { onSaving(saving); return () => onSaving(false) }, [saving, onSaving])
  useEffect(() => () => { requestId.current += 1 }, [])
  useEffect(() => {
    let cancelled = false
    void api.workspaceEntries(workspace.id, directory).then((result) => {
      if (!cancelled) { setEntries(result.entries); setTruncated(result.truncated) }
    }).catch((error) => { if (!cancelled) notify(errorText(error)) })
    return () => { cancelled = true }
  }, [workspace.id, directory, reload, notify])
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => { if (dirty || saving) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty, saving])
  async function open(path: string) {
    if (saving || (dirty && !window.confirm('当前文件有未保存修改，放弃修改并打开其他文件？'))) return
    const id = ++requestId.current
    setLoading(true)
    try { const result = await api.workspaceFile(workspace.id, path); if (id === requestId.current) { setFile(result); setContent(result.content); setPreview(false) } }
    catch (error) { if (id === requestId.current) notify(errorText(error)) }
    finally { if (id === requestId.current) setLoading(false) }
  }
  async function save() {
    if (!file || !dirty || saving) return
    setSaving(true)
    try { const result = await api.saveWorkspaceFile(workspace.id, file, content); setFile(result); setContent(result.content); notify('已保存到本地目录') }
    catch (error) { notify(errorText(error)) }
    finally { setSaving(false) }
  }
  return <aside className="workspace-pane" aria-label="工作空间文件">
    <header><FolderOpen size={18} /><div><strong>{workspace.name}</strong><small title={workspace.root}>{workspace.root}</small></div><button className="icon-button" aria-label="关闭文件面板" disabled={saving} onClick={onClose}><X size={18} /></button></header>
    <div className="workspace-access">{workspace.agent_read ? 'Agent 可以按需读取文本' : '仅手动浏览 · 未授权 Agent 读取'}<span>只读工具</span></div>
    <div className="workspace-directory"><button className="icon-button" aria-label="上一级目录" disabled={!directory || directory === '.'} onClick={() => setDirectory(directory.split('/').slice(0, -1).join('/'))}><ArrowLeft size={15} /></button><code title={directory}>{directory && directory !== '.' ? directory : '/'}</code><button className="icon-button" aria-label="刷新目录" onClick={() => setReload((previous) => previous + 1)}><RefreshCw size={14} /></button></div>
    <nav className="workspace-entries" aria-label="文件列表">{entries.map((entry) => <button key={entry.path} className={file?.path === entry.path ? 'selected' : ''} onClick={() => entry.kind === 'directory' ? setDirectory(entry.path) : void open(entry.path)} disabled={entry.kind === 'file' && (!entry.text || saving)} title={entry.text || entry.kind === 'directory' ? entry.path : '暂不支持 PDF 或二进制文件预览'}>{entry.kind === 'directory' ? <Folder size={15} /> : <FileText size={15} />}<span>{entry.name}</span>{entry.kind === 'directory' && <small>↗</small>}</button>)}{!entries.length && <p>此目录没有可显示的文件</p>}{truncated && <p>目录较大，只显示部分条目。</p>}</nav>
    {loading && <div className="workspace-loading" role="status">正在读取文件…</div>}
    {file ? <section className="workspace-document"><div className="workspace-document-toolbar"><code title={file.path}>{file.path}{dirty && <span> ●</span>}</code><button disabled={saving || loading} onClick={() => void open(file.path)} title="重新读取磁盘文件"><RefreshCw size={13} /></button>{file.path.endsWith('.md') && <button onClick={() => setPreview(!preview)}>{preview ? '编辑' : '预览'}</button>}<button disabled={!dirty || saving || loading} onClick={() => void save()}><Save size={13} />{saving ? '保存中' : '保存'}</button></div>{preview ? <article className="markdown workspace-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown></article> : <textarea aria-label={`编辑 ${file.path}`} className="workspace-editor" spellCheck={false} value={content} disabled={saving || loading} onChange={(event) => setContent(event.target.value)} />}<footer>{dirty ? '未保存 · Agent 读取的是磁盘上的版本' : `${file.size.toLocaleString()} bytes · 当前读取版本`}<span>UTF-8</span></footer></section> : <div className="workspace-empty"><FileText size={28} /><h3>材料就在身边</h3><p>打开代码、笔记或草稿，<br />一边对话，一边工作。</p><small>暂不支持 PDF、二进制和大文件预览。<br />环境、密钥文件及链接路径受到保护。</small></div>}
  </aside>
}
