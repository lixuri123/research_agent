# 本地受限执行（Windows 第一版）

已接入固定 `workspace_search_text`，不是通用终端或代码沙盒。Python 主应用仍使用 uv，Windows helper 使用受限令牌、Low 完整性级别及 Job Object。无需 Docker、系统账户或防火墙初始化，不修改用户业务工作空间 ACL。

## 安全边界

- Windows 后端为 **partial**：限制写类访问，但读取、网络和进程可见性不隔离。它不适合运行不可信脚本。
- 只执行受信 rg.exe 的固定字面量搜索；模型不能传程序、目录、环境或额外命令参数。
- 先通过 WorkspaceService 筛选并复制已授权 UTF-8 文本，排除凭据、隐藏目录、链接、二进制和过大文件。用户原文件不被命令写入。
- 执行开关与工作空间读取授权独立，默认关闭；实际调用和运行期间重新检查授权与租约。
- 后端缺失、权限初始化失败或失去租约时停止，不回退普通进程执行。确认修改原文件仍走现有文件提案接口。
- MCP/浏览器/Skill 等其他能力不自动受此 runner 管辖。

## 构建与启动

需要 Windows、Visual Studio C++/Windows SDK 构建环境和一个受信 rg.exe。脚本不自动安装依赖；构建阶段才需要编译工具，运行时只需 exe。

在项目根目录 PowerShell 执行：

```powershell
.\native\windows-sandbox\build.ps1
```

输出 `native\windows-sandbox\bin\research-sandbox.exe`。设置 `.env`（请勿提交密钥）：

```dotenv
EXECUTION_ENABLED=true
EXECUTION_TIMEOUT_SECONDS=15
EXECUTION_OUTPUT_BYTES=16384
```

如果 API/worker 启动环境的 PATH 不含 rg，另设 `EXECUTION_RG_PATH` 为已安装 rg.exe 的绝对路径。helper 默认使用项目内构建产物，也可设置 `EXECUTION_HELPER_PATH` 指向受信安装路径。两者不得位于所绑定的业务工作空间内，路径不支持链接。更改配置后重启 API 和 worker；设置不自动传播到已运行的进程。

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv run uvicorn research_agent.api:app --host 127.0.0.1 --port 8000
```

另开终端启动 worker：

```powershell
$env:UV_CACHE_DIR = "$PWD\.uv-cache"
uv run python -m research_agent.worker
```

打开聊天界面，绑定已允许 Agent 读取的业务工作空间，在侧栏“本地受限执行”中查看状态，明确接受部分隔离边界后点击“授权固定检索”。随后**新建任务**，例如“在这个工作空间的文本中检索 Transformer”。已有任务的工具发现快照不自动增补新工具。

如业务工作空间绑定整个 research_agent 项目，而 helper 也放在此项目内，程序位置检查会拒绝；应使用独立资料工作空间，或把可信程序安装到工作空间外后配置绝对路径。不要通过放宽路径检查解决。

## 限额和记录

默认最多处理 100 个合规文本文件、4 MiB 输入，准备上限 5 秒且最多检查 1000 个条目。单文件仍受现有 128 KiB 限制；准备过程受目录列表截断规则影响。命令运行上限 15 秒，stdout/stderr 合计保留 16 KiB，返回最多 50 条命中。未匹配不代表整个目录不存在该文本；界面和模型会得到截断信息及输入版本。

执行记录、查询和输入 manifest 保存到业务数据库；命中可以进入模型调用。内部副本运行后清理，不代表任务记录同步删除。异常退出可能留下 `data/execution` 残留，不能在存在活跃执行时手动清理。

同一步骤参数不能改变，已保存结果不会再次执行。授权撤回或租约失效后即使已有结果也不能继续调用。仅此固定只读操作允许中断后重试；不能将该规则用于未来写操作。

API：`GET/PUT /workspaces/{id}/execution-permission`，开启时必须传 `accept_partial=true`；`GET /workspaces/{id}/executions?run_id=...` 读取执行记录。没有直接运行任意命令的 HTTP 接口。

## 尚未开放

处理副本写入、任意 Python/PowerShell、依赖安装、删除、新文件、单次高风险工具审批与自动产物写回都没有开放。后续写阶段需增加独立审批挂起/恢复及副作用不明时的人工处理，不能复用研究报告审批或一键完整访问。

平台自检确认令牌和 Job 能创建，不代表全面安全认证。部署到其他 Windows 环境时仍需少量真实边界验证；失败时保持禁用。若在已经受限制的宿主进程中启动，CreateRestrictedToken 可能报 Win32 87；正常用户进程与该宿主的结果可以不同，不得因此改为不受限执行。
