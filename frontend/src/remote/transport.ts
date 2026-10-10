export type ConnectionState = 'connecting' | 'online' | 'offline' | 'unpaired'
export class RemoteError extends Error { constructor(public code: string, message: string) { super(message) } }
export interface RemoteSession { host_id: string; device_id: string; name: string; online: boolean; csrf_token: string }
type Event = { subscription_id?: string; task_id: string; cursor: number; payload: Record<string, unknown> }
export async function remoteHTTP<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch('/remote/' + path, { credentials: 'same-origin', headers: { 'Content-Type': 'application/json', 'X-Remote-CSRF': '1' }, ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }) })
  const value = await response.json()
  if (!response.ok) throw new RemoteError(value.error?.code || (response.status === 401 ? 'unauthorized' : 'conflict'), value.error?.message || '连接请求失败')
  return value
}
export class RemoteTransport {
  session: RemoteSession | null = null
  private socket: WebSocket | null = null
  private stopped = true
  private retry = 0
  private timer?: number
  private heartbeat?: number
  private lastSeen = 0
  private hostOnline = false
  private generation = 0
  private pending = new Map<string, { resolve: (value: unknown) => void; reject: (error: Error) => void; timer: number }>()
  private subscriptions = new Map<string, { id: string; cursor: number; callback: (event: Event) => void }>()
  constructor(private state: (state: ConnectionState) => void) {}
  private wake = () => { if (document.visibilityState === 'visible' && !this.stopped) { if (this.socket) this.socket.close(); else { clearTimeout(this.timer); void this.connect() } } }
  start() { if (!this.stopped) { this.wake(); return }; this.stopped = false; window.addEventListener('pageshow', this.wake); document.addEventListener('visibilitychange', this.wake); void this.connect() }
  stop() { this.stopped = true; this.generation++; window.removeEventListener('pageshow', this.wake); document.removeEventListener('visibilitychange', this.wake); clearTimeout(this.timer); clearInterval(this.heartbeat); this.socket?.close(); this.socket = null; this.rejectPending(); this.subscriptions.clear() }
  private rejectPending() { for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new RemoteError('unknown', '连接中断，操作结果待确认')) }; this.pending.clear() }
  private schedule() { if (!this.stopped) this.timer = window.setTimeout(() => void this.connect(), Math.min(30000, 1000 * 2 ** Math.min(10, this.retry++) * (.8 + Math.random() * .4))) }
  private setOnline(value: boolean) { const recovered = value && !this.hostOnline; this.hostOnline = value; this.state(value ? 'online' : 'offline'); if (recovered) for (const task of this.subscriptions.keys()) void this.restore(task) }
  private async connect() {
    if (this.stopped) return
    const generation = ++this.generation
    this.state('connecting')
    try { this.session = await remoteHTTP<RemoteSession>('session') }
    catch (e) { if (this.stopped || generation !== this.generation) return; if (e instanceof RemoteError && e.code === 'unauthorized') { this.state('unpaired'); return }; this.state('offline'); this.schedule(); return }
    if (this.stopped || generation !== this.generation) return
    const socket = new WebSocket(`${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/remote/ws/device`)
    this.socket = socket
    socket.onopen = () => {
      if (this.socket !== socket || generation !== this.generation) { socket.close(); return }
      this.retry = 0; this.lastSeen = Date.now(); this.hostOnline = false; this.setOnline(!!this.session?.online)
      this.heartbeat = window.setInterval(() => { if (Date.now() - this.lastSeen > 60000) { socket.close(); return }; socket.send(JSON.stringify({ version: 1, type: 'ping' })); void this.call<{online: boolean}>('host.status', {}).then(() => this.setOnline(true)).catch(e => { if (e instanceof RemoteError && e.code === 'offline') this.setOnline(false) }) }, 20000)
    }
    socket.onmessage = e => {
      if (this.socket !== socket || generation !== this.generation) return
      this.lastSeen = Date.now()
      let msg: Record<string, any>; try { msg = JSON.parse(e.data) } catch { socket.close(); return }
      if (msg.type === 'ping') { socket.send(JSON.stringify({ version: 1, type: 'pong' })); return }
      if (msg.type === 'response') { const p = this.pending.get(msg.request_id); if (!p) return; clearTimeout(p.timer); this.pending.delete(msg.request_id); if (msg.error) { if (msg.error.code === 'offline') this.setOnline(false); p.reject(new RemoteError(msg.error.code, msg.error.message)) } else { this.setOnline(true); p.resolve(msg.result) } }
      if (msg.type === 'event') this.deliver(msg as Event)
    }
    socket.onclose = () => { if (this.socket !== socket || generation !== this.generation) return; this.socket = null; clearInterval(this.heartbeat); this.hostOnline = false; this.rejectPending(); if (!this.stopped) { this.state('offline'); this.schedule() } }
    socket.onerror = () => socket.close()
  }
  call<T = unknown>(method: string, payload: unknown, key?: string): Promise<T> {
    if (this.socket?.readyState !== WebSocket.OPEN || !this.session) return Promise.reject(new RemoteError('offline', '电脑尚未连接'))
    if (this.pending.size >= 100) return Promise.reject(new RemoteError('rate_limited', '正在处理的请求过多，请稍后重试'))
    const request_id = crypto.randomUUID()
    return new Promise<T>((resolve, reject) => {
      const timer = window.setTimeout(() => { this.pending.delete(request_id); reject(new RemoteError('unknown', '等待响应超时，操作结果待确认')); this.socket?.close() }, 45000)
      this.pending.set(request_id, { resolve: value => resolve(value as T), reject, timer })
      try { this.socket!.send(JSON.stringify({ version: 1, type: 'request', request_id, host_id: this.session!.host_id, method, payload, ...(key ? { idempotency_key: key } : {}) })) } catch { clearTimeout(timer); this.pending.delete(request_id); reject(new RemoteError('unknown', '提交状态待确认')) }
    })
  }
  subscribe(task: string, callback: (event: Event) => void) { this.subscriptions.set(task, { id: crypto.randomUUID(), cursor: 0, callback }); void this.restore(task); return () => { const sub = this.subscriptions.get(task); this.subscriptions.delete(task); if (sub) void this.call('task.unsubscribe', { task_id: task, subscription_id: sub.id, cursor: sub.cursor }).catch(() => {}) } }
  private deliver(event: Event) { const sub = this.subscriptions.get(event.task_id); if (!sub || (event.subscription_id && event.subscription_id !== sub.id) || event.cursor <= sub.cursor) return; sub.cursor = event.cursor; sub.callback(event) }
  private async restore(task: string) {
    const sub = this.subscriptions.get(task); if (!sub) return
    try {
      const result = await this.call<{events: Event[]}>('task.subscribe', { task_id: task, subscription_id: sub.id, cursor: sub.cursor })
      for (const event of result.events) this.deliver({ ...event, payload: event.payload || event as unknown as Record<string, unknown>, task_id: task, subscription_id: sub.id })
      if (result.events.length === 100 && this.subscriptions.get(task) === sub) void this.restore(task)
    } catch (e) { if (e instanceof RemoteError && e.code === 'resync_required') { sub.cursor = 0; sub.callback({ task_id: task, cursor: 0, payload: { resync_required: true } }); void this.restore(task) } }
  }
}
