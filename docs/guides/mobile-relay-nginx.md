# 使用现有 nginx 部署无域名 Relay

基于用户提供的配置：`/etc/nginx/sites-enabled/qingjian` 是唯一的站点配置，监听 IPv4/IPv6 的 80 端口，`server_name bagiman.cc.cd`，代理到本机 8000。未配置 TLS。站点名称不能证明域名控制权，公网 IP 也不能从这份配置中推断。

本方案使用固定公网 IPv4 的可信 IP 证书，在同一个 nginx 进程上新增 HTTPS 8443 入口，Relay 绑定 `127.0.0.1:9000`。现有青笺的根路径继续代理到 8000。Caddy 不运行。

## 1. 准备证书验证目录

在服务器执行：

```bash
sudo mkdir -p /var/www/letsencrypt/.well-known/acme-challenge
sudoedit /etc/nginx/sites-enabled/qingjian
```

在现有 `server { ... }` 内添加以下块，与原 `location /` 并列，保留原配置：

```nginx
location ^~ /.well-known/acme-challenge/ {
    root /var/www/letsencrypt;
    default_type text/plain;
    try_files $uri =404;
}
```

确认配置后重新加载同一个 nginx：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

公网 IP 的 TCP 80 必须能到达该 nginx。当前站点是 default_server，因此 IP 请求会进入它的验证目录。若存在未贴出的前置代理或公网转发，需确保验证路径也到达这里。

## 2. 申请可信 IP 证书

使用 Certbot 5.4 或以上。检查版本，旧版应按 Certbot 官方安装说明升级；Ubuntu 软件源的版本不能直接假定足够新。

```bash
certbot --version
sudo certbot certonly --webroot \
  --webroot-path /var/www/letsencrypt \
  --ip-address PUBLIC_IP \
  --preferred-profile shortlived \
  --cert-name relay-ip \
  --deploy-hook 'nginx -t && systemctl reload nginx'
```

替换 `PUBLIC_IP` 为固定公网 IPv4。第一次执行按提示完成账户设置。使用 `webroot` 不停止现有 nginx；不要使用需要占用 80 端口的 standalone 模式。命令申请正式证书，未添加 staging 参数。

IP 证书有效期约 6 天，部署前必须配置 Certbot 自动续期调度，并保留成功续期后的 nginx reload hook。使用你当前 Certbot 安装方式提供的定时任务，不要假定仅执行 certonly 就已经有续期调度。IP 变化后需要重新签发证书并更新双方地址。

官方说明：https://letsencrypt.org/2026/03/11/shorter-certs-certbot

## 3. 新增 nginx 8443 入口

证书生成后，将仓库的 `config/remote-relay.nginx.conf` 复制到 `/etc/nginx/conf.d/remote-relay.conf`，替换其中的 `PUBLIC_IP`。确认 8443 未被其他程序占用。

```bash
sudo nginx -t && sudo systemctl reload nginx
```

该配置使用 `$http_host` 保留 8443 端口，使 Host 与浏览器 Origin 一致；传递 HTTPS 标记和 WebSocket 升级头。9000 后端只信任来自本机 nginx 的代理头。模板按 nginx 直接面向公网配置；如果 nginx 前还有受信代理，需另行设置真实客户端地址。

nginx 官方 WebSocket 说明：https://nginx.org/en/docs/http/websocket.html

## 4. 启动 Relay

在独立 Relay 仓库根目录建立专用 Python 虚拟环境，安装 `config/remote-relay.requirements.txt`。`.env.relay` 内容为：

```dotenv
REMOTE_RELAY_PUBLIC_URL=https://PUBLIC_IP:8443
REMOTE_RELAY_DATABASE_URL=sqlite:///data/remote-relay.db
REMOTE_RELAY_DEVELOPMENT=false
```

替换公网 IP。手动前台启动命令：

```bash
.venv-relay/bin/python -m uvicorn research_agent.remote.relay:deployment_app \
  --factory --env-file .env.relay --host 127.0.0.1 --port 9000 \
  --proxy-headers --forwarded-allow-ips 127.0.0.1
```

正式运行使用系统服务管理器保持进程运行，固定工作目录与持久化 data 目录。只运行单个 Relay 进程。服务器安全组及主机防火墙允许公网 TCP 80 和 8443，9000 不开放到公网。

电脑的「手机控制」填写 `https://PUBLIC_IP:8443`，手机入口为同源 `/mobile/`。不要把原来青笺的 HTTP 80 地址当作 Relay 地址，也不要填写 `/relay/` 路径前缀。

## 当前交付范围

文件是待填写公网 IP 的配置模板。只读取用户贴出的 nginx 输出并准备本地文件，未连接服务器、修改远程配置、申请证书或运行部署测试。公网 IP、DNS 控制权、Certbot 版本和端口可达性仍待确认。
