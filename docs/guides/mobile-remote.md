# 手机远程访问

桌面 API 保持绑定 `127.0.0.1`，远程连接器向可信 Relay 发起出站 WebSocket；手机只访问 Relay 的 `/mobile/`。任务、模型、工作空间和审批仍由桌面 API/Worker 处理。Relay 只保存主机、邀请和设备授权元数据，不持久化业务正文，但会看到转发的会话、材料、结果与审批流量。HTTPS 保护传输，这不是端到端加密；只使用你信任的服务器。

## 更新桌面与配对

本次未运行后续流程测试。以下列出更新桌面所需命令，由用户执行；不要覆盖已有 `.env`。

```powershell
# 项目根目录
uv sync --frozen
Set-Location frontend
npm install --cache .npm-cache
npm run build
Set-Location ..
.\start.bat
```

在“桌面头像菜单 → 手机控制”填写 Relay 的 HTTPS 源地址（如 `https://relay.example.com`，不带路径），启用并等待连接成功。生成邀请后，手机打开链接或扫描二维码；邀请约 5 分钟有效且只能兑换一次。手机填写设备名，桌面对照两边验证码后批准，并明确选择可访问的范围。个人会话是独立范围，不能因授权工作空间而自动获得。手机完成后保存浏览器会话 Cookie；邀请片段不是长期登录凭证。

iPhone 可用相机扫描二维码，再在 Safari 打开链接；保持普通浏览模式，后续使用同一浏览器以保留配对 Cookie。网页进入后台或手机锁屏时可能挂起连接，返回页面后会重连并补读状态；已接受的任务仍在电脑执行。实际手机访问需要公网 HTTPS Relay，不能使用电脑的 localhost 链接。本次未做 iPhone 实机验证。

启动器自动运行空闲连接器，无需额外 `--remote` 参数；`--port` 同时配置本地管理接口校验。手动分别启动时 API 和连接器必须使用相同 `REMOTE_LOCAL_PORT`、工作目录、数据库配置，并另开终端运行 `python -m research_agent.remote.connector`。连接器没有新 TCP 监听端口。

## 本机开发模拟

先完成前端构建，在项目根目录另开终端，用已有 Windows `.venv` 启动独立 Relay：

```powershell
$env:REMOTE_RELAY_PUBLIC_URL = 'http://127.0.0.1:9000'
$env:REMOTE_RELAY_DATABASE_URL = 'sqlite:///data/remote-relay-dev.db'
$env:REMOTE_RELAY_DEVELOPMENT = 'true'
.\.venv\Scripts\python.exe -m uvicorn research_agent.remote.relay:deployment_app --factory --host 127.0.0.1 --port 9000
```

桌面“手机控制”使用同一 `http://127.0.0.1:9000`，明确勾选本机开发模式。在同一电脑浏览器打开 `http://127.0.0.1:9000/mobile/` 或生成的邀请链接模拟手机。`localhost` 与 `127.0.0.1` 是不同源，整个流程保持一致。这个链接不能供真实手机使用：手机上的 localhost 指手机自身。真实手机访问需要下节的公网 HTTPS 部署；开发 HTTP 只允许回环地址，不能改为局域网地址绕过限制。

## 部署公网 Relay

如果服务器已有 nginx 且没有可用域名，使用[现有 nginx + 公网 IP 证书部署方案](mobile-relay-nginx.md)。该方案新增 HTTPS 8443 入口，不运行 Caddy。下面的域名/Caddy 示例仅适用于相应部署条件。

Relay 单实例运行，不运行桌面 API 或 Worker，不需要模型密钥、桌面 `.env`、工作空间文件或业务数据库。部署目录保留 `research_agent/__init__.py`、`research_agent/remote/{__init__,protocol,relay_store,relay}.py`、已构建的 `frontend/dist/`，以及下面的配置文件，目录层次与仓库相同。也可使用完整源码目录，但只安装 Relay 依赖。代码包升级时一并更新前端构建。

在 Linux 部署目录创建专用环境（Python 3.10–3.12），复制示例配置为 `.env.relay` 并修改域名及数据库路径：

```bash
python3 -m venv .venv-relay
.venv-relay/bin/python -m pip install -r config/remote-relay.requirements.txt
cp config/remote-relay.example.env .env.relay
# 编辑 .env.relay 后启动；相对数据库路径相对于当前部署目录
.venv-relay/bin/python -m uvicorn research_agent.remote.relay:deployment_app --factory --env-file .env.relay --host 127.0.0.1 --port 9000 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

Windows 可在部署目录使用已有根 `.venv`（已安装项目依赖）：

```powershell
.\.venv\Scripts\python.exe -m uvicorn research_agent.remote.relay:deployment_app --factory --env-file .env.relay --host 127.0.0.1 --port 9000 --proxy-headers --forwarded-allow-ips 127.0.0.1
```

`REMOTE_RELAY_PUBLIC_URL` 必须是手机最终访问的 HTTPS 源；`REMOTE_RELAY_DEVELOPMENT=false`。`REMOTE_RELAY_DATABASE_URL` 使用独立持久化 SQLite 文件，启动工厂会创建父目录；保护该目录并备份元数据，避免重建数据库导致所有配对失效。只运行一个 Uvicorn 进程，不添加多 Worker；连接路由状态在进程内，不能直接横向扩容。

将域名 DNS 指向服务器，允许公网 80/443，为 `config/remote-relay.Caddyfile` 替换同一域名，交由 Caddy 终止 TLS 并转发 HTTP/WebSocket 到 `127.0.0.1:9000`：

```bash
caddy run --config config/remote-relay.Caddyfile --adapter caddyfile
```

后台运行可交给系统服务管理器，固定工作目录并配置重启；上面前台命令用于说明启动参数。不要公开 9000 端口，不要信任所有转发 IP；若代理不在回环，需要显式信任实际代理地址并限制后端访问。证书签发要求域名及端口可达。Relay 仅提供 `/remote/*`、`/mobile/*` 和 `/assets/*`，不挂载桌面管理 API 或根桌面页面。完成部署后手机访问 `https://你的域名/mobile/`，再从桌面生成新邀请。本次未部署公网服务器。

## 授权、恢复与离线

桌面可收窄设备范围或撤销设备；权限以本地持久化状态为准，撤销先阻止本地业务，再通知 Relay。关闭远程访问保留配对，重新启用可恢复；“重置”会停用旧主机、撤销全部设备并清理本机主机凭证，之后重新配对。旧 Relay 不可达时清理是尽力操作，桌面会说明未确认，但本地旧授权已失效。更换已经配对的 Relay 先重置。

手机清 Cookie、使用另一浏览器或重装浏览器会丢失登录；在桌面撤销旧设备并生成新邀请。不要试图重复使用已兑换或过期的邀请。主机凭证保存在 Windows 用户 DPAPI 文件（其他系统为用户私有文件），不放在参数、链接或 SQL 中；迁移机器应重新配对。

桌面必须保持开机、唤醒、API/Worker/连接器运行与联网。心跳检测后离线显示可能有短暂延迟；连接器会退避重连，Relay 不在离线时持久化排队业务请求。提交后断网可能是“结果未知”，不意味着提交失败；恢复后先查任务，并用原来的幂等键重试原操作，避免换键重复提交。事件恢复使用游标补读；手机重载后通过任务详情重新获取状态。

## 排查

连接器离线时检查桌面进程、Relay 地址与 HTTPS 证书、服务器和桌面出站网络；开发模式检查双方是否使用同一回环源。`forbidden` 通常表示源地址、CSRF 或代理 HTTPS 转发配置不一致；不要关闭这些检查。配对长期等待时检查桌面是否在线并批准正确验证码，过期则重新生成邀请。Cookie 丢失或撤销显示未授权时重新配对。服务器报缺少 `frontend/dist` 时更新构建并复制完整 `dist`，不能只复制 index.html。
