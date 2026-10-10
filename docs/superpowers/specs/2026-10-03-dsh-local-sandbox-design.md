# 参照 dsh 的本地沙盒实现设计

日期：2026-10-03。状态：用户已确认实施；首版 Windows 只读固定文本检索已接入。未修改业务工作空间 ACL；可写副本、高风险工具审批与写回阶段尚未开放。

依据：本机 `D:\agent\deepseek-harness` 源码，而非仅依据截图。目标是复用 dsh 的分层和 Windows 隔离机制，不引入整个 dsh 框架，不建设 Docker 或云端执行平台。主应用继续由 uv 管理。

## 1. 先明确 dsh 实际提供什么

dsh 将策略、审批、沙盒提供方和命令执行分开。每次调用携带策略；平台提供方负责约束进程，消费方负责执行、超时和结果整理。普通文件操作也有单独的路径策略，不是所有工具都自动进入同一个进程沙盒。

其 Windows 实现不是 AppContainer，而是以下组合：

- `CreateRestrictedToken`：`DISABLE_MAX_PRIVILEGE | LUA_TOKEN | WRITE_RESTRICTED`。
- Low 完整性令牌。
- 工作目录能力 SID、私有临时目录能力 SID，以及相应 ACL 和 Low 标签。
- 挂起创建进程，加入 kill-on-close Job 后才恢复执行。

`read-only` 不授予显式可写目录；`workspace-write` 授予指定目录及私有临时目录。模式描述文件效果，**不代表读取隔离或断网**。Windows 后端明确报告 `partial`：读取、网络、进程可见性不受此机制限制，硬链接等也存在边界。

因此，“和 dsh 一样”不能同时被解释为“任意代码无法读取工作区外文件、无法联网”。命令白名单和路径校验可以限制本项目提供的固定工具，但不能把这一后端变成恶意代码安全容器。

### 与此前方案的关系

此前 `2026-10-03-local-execution-design.md` 要求系统级区外读取限制及断网。本方案不默默降低该要求：

- 如保持严格要求，dsh Windows 后端不满足，命令执行保持禁用；需要另加经过验证的读侧及网络隔离后端。
- 如确认接受 dsh 的部分隔离边界，可提供“本地受限执行”，但第一版只开放可信固定操作，不开放任意脚本，并明确告知残余风险。

用户随后确认实施，本轮采用第二条的可信固定只读操作路线，明确保留 partial 边界。后续扩展到写操作或任意代码仍需另外确认，不获得完整访问授权。

## 2. 我们沿用的结构

```text
Agent 的结构化工具请求
  → ExecutionPolicyResolver：绑定身份、工作区、能力和限制
  → ExecutionGuard：确定性校验，不允许审批绕过硬限制
  → ApprovalService：需要时暂停，批准仅适用于这一次调用
  → SandboxProvider：检查平台能力，生成受限启动方案
  → ExecutionRunner：启动、输出收集、超时、取消、进程树回收
  → ExecutionRepository：持久化结果及审计
  → Agent 获得有界结果 / 用户检查产物提案
```

首次策略校验在申请审批之前；等待结束后重新执行硬校验。审批决定允许做什么，操作系统决定进程能做什么。二者不能互相替代。

建议新增模块：

| 模块 | 实现职责 | 不拥有的职责 |
| --- | --- | --- |
| `research_agent/execution/policy.py` | 策略解析、固定操作与参数规则 | 模型决策、启动进程 |
| `research_agent/execution/service.py` | 校验、审批协调、执行生命周期 | 任务最终完成判定 |
| `research_agent/execution/storage.py` | 调用、审批、结果及恢复记录 | 文件提案确认 |
| `research_agent/execution/providers/base.py` | 后端接口和真实能力报告 | 自动降级为普通 subprocess |
| `research_agent/execution/providers/windows_acl.py` | Windows helper 适配、授权目录生命周期 | 任意 shell 工具 |
| `native/windows-sandbox/` | 受限令牌、ACL、Job、受限进程创建 | LLM、业务数据库、用户审批 |

这些是拟新增文件，不是已有实现。先实现 Windows；其他平台报告不可用，后续再按 dsh 的平台提供方方式扩展。

## 3. 语言与复用方式

可选实现有三种：

1. Python 调用 dsh 的 Node runner：与现有机制最接近，但需要 Node、koffi 和 dsh 的相关包及版本管理，不是复制一个 runner 文件即可运行。
2. **Python 主应用 + 小型原生 Windows helper，建议作为长期实现**：只移植受限令牌、ACL 和进程创建部分，运行时不需要 Node 或容器。建议 C++ + Windows SDK，发布时附带固定版本 exe，开发构建才需要编译工具链。
3. Python ctypes 直接调用全部 Win32 API：部署看似简单，但结构体布局、句柄继承、错误处理和清理容易出错，不建议作为第一选择。

原生 helper 的成本是一次 Windows 专用构建与少量真实边界验证，不应把移植说成零成本。若优先快速验证机制，可先用 Node 适配器验证接口，再替换为原生 helper；不要为了过渡方案引入整个 Cordis 或 dsh UI。

dsh 包声明 MIT；复用代码时保留相应许可和版权，锁定本次参照的源码版本，同时核对实际引入的依赖许可证。此次不安装或执行这些依赖。

## 4. 工作空间与执行目录

**用户业务工作空间与操作系统授予写权限的执行目录分离。**

```text
用户绑定的业务工作空间              可信运行时安装目录
  ↓ 已授权文件读取、版本与路径校验     ↓ 固定 exe，不从业务目录查找
data/execution/<execution-id>/
  inputs/       已筛选材料副本
  work/         未来可写的处理副本
  temp/         本次私有临时目录
  outputs/      未来待审查产物
  ↓ 校验产物及原文件版本
已有 FileChangeService → 差异预览 → 用户确认 → 写回原文件
```

第一版只读检索不授予上述目录写权限；目录准备由可信 Python 服务完成。未来可写操作中，将 `work/` 作为沙盒工作根，把其他可写产物组织在该根内部，并单独授权私有 `temp/`。`inputs/` 不跟随工作根获得可写授权。

业务工作空间的现有读取许可不等于命令执行许可，增加独立 `agent_execute` 开关，默认关闭。模型不能选择根目录、SID、可执行程序、额外参数、环境变量或策略模式。

材料复制复用 WorkspaceService 的受保护文件、路径和链接规则，并增加总文件数、总字节数限额；保存原文件相对路径与内容版本的 manifest。复制完成后再次核对版本，不复制凭据。执行目录采用不可预测的新身份，不接受现有目录冒充，不与业务根或受信运行时重叠。

这样做的理由：dsh 的工作区 ACL 和 Low 标签会常驻在真实目录，退出后不撤销，并可能影响同一用户的其他 Low 完整性进程。我们不把这类改动直接传播到用户论文、代码和个人资料目录。

副本不是读隔离：受限进程仍可能读取调用者可读的其他路径。因此只允许运行受信固定程序；仍不得声称可以安全执行论文仓库中的任意代码。也不保证两个同用户进程之间的读取保密。

## 5. 逐调用策略与模式

由服务端解析不可变 ExecutionPolicy：

- `owner_id / session_id / run_id / step_id / workspace_id`，来自已验证的任务身份。
- 工作空间规范路径与身份，执行目录身份、输入 manifest 及其摘要。
- `operation`、固定 argv、`mode`、审批策略及策略版本。
- 时限、输出限制和后端必须满足的能力。

不以字符串前缀判断目录包含关系。进入执行前和审批恢复时重新检查工作区授权、owner、任务租约与根目录身份；没有有效 worker 租约不得启动。

第一版仅支持 `read-only`。第二阶段才加入 `workspace-write`，它只表示允许写处理副本，不表示允许写原文件。暂不提供 dsh 的 `danger-full-access`。

后端报告至少分开列出：`file_write=partial`、`file_read=not_isolated`、`network=not_isolated`、Job 管理是否已启用，以及初始化错误。界面展示“本地受限执行”，不展示“完全安全沙盒”。要求后端不具备的能力时返回 `SANDBOX_UNAVAILABLE`，不得降级。

## 6. Windows helper 的实现步骤

1. 打开当前进程令牌，取得登录 SID，派生执行根能力 SID；私有临时目录使用独立能力 SID。不把所有登录用户组都放入 restricting SID 列表。
2. 只读模式不添加可写根 SID。可写模式添加当前执行根和私有临时目录 SID；登录 SID 与 Everyone 的保活处理按 dsh 实现移植，不任意删改以免 DLL/CNG 初始化失败。
3. 创建 `WRITE_RESTRICTED` 令牌，并设置 Low 完整性级别。
4. 可写模式对服务拥有的执行目录合并安全描述符：能力 SID 的允许 ACE、目录上的 Everyone `FILE_DELETE_CHILD` 拒绝 ACE、Low no-write-up 标签。保留既有 ACL；拒绝 ACE 只继承到目录，不能错误传播到文件。
5. 按 dsh 的方案补充 TokenDefaultDacl，避免子进程创建管道等对象时因第二次访问检查失败。只移植令牌创建而忽略这一环会导致运行异常。
6. 创建非可继承的 Job 控制句柄，启用 `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`。使用受限令牌 `CreateProcessAsUserW`，以 `CREATE_SUSPENDED` 启动，先成功加入 Job，再 `ResumeThread`；失败立即终止，不留下未受管进程。
7. 受控传递 stdio，显式限制继承句柄；控制通道、数据库和凭据句柄不能被目标进程继承。仅在可写模式设置私有 TEMP/TMP，不授予系统临时根写权限。
8. 超时、取消、租约丢失时终止整个 Job；持续排空输出但只保留有界内容，避免输出上限导致管道死锁。时限由可信执行器控制，不依赖目标进程自觉退出。
9. 子进程全部结束后再清理临时授权和句柄。执行根常驻授权只落在内部副本；跨进程串行化同一目录的 ACL 改动。崩溃残留登记并在确认无活跃执行后清理，不能扫描删除任意用户目录。

内存、进程数量等 Job 限额可以按操作配置；不把 dsh 的 kill-on-close 直接描述为已有完整资源限额。后台隐藏启动必须验证控制台/stdio 行为，不能随意添加破坏初始化的创建标志。

helper 从受信安装目录以绝对路径启动，不能放在已授予写权限的执行根中。它不是提权服务：不接受模型请求，不接受任意路径授权，不携带管理员令牌。任何需要管理员或所有权变更的初始化必须另行说明并取得授权；此次设计不执行。

## 7. Python 与 helper 的协议

使用有版本的结构化请求，包含执行身份、经过校验的模式/目录、固定程序及参数、限额和策略摘要。Python 端校验之外，helper 再检查模式、目录组合、固定操作和程序位置；未知字段或未知协议版本拒绝。

目标 stdout/stderr 是不可信输出，不能作为控制协议。helper 通过目标不可继承的控制通道返回 `started / result / failure`，附后端版本、真实约束、PID、退出码、截断情况及 Win32 错误。保留完整 Windows 退出码语义。

`RUNNER_FAILURE` 与 `COMMAND_EXIT_NONZERO` 分开；不能仅凭 stderr 前缀判断初始化失败，也不能把任意 access denied 输出当作可安全重试的证据。若无法确认进程是否已启动或是否产生副作用，记录 `unknown`。

## 8. 审批与 worker 恢复

新增执行专用记录，不复用研究报告审批。当前 `SQLRepository.finish(waiting_approval)` 要求已有报告，普通 Agent 也没有工具审批恢复分支，需要明确扩展，而不是直接套用现有报告状态。

执行状态建议：`prepared → waiting_approval → authorized → starting → running → succeeded/failed/cancelled/unknown`，另有 `denied`。无需审批的固定只读操作可从 prepared 进入 authorized。

审批记录绑定不可变摘要：owner、任务、步骤、工作区身份、操作/参数、输入版本、请求模式、策略版本、有效期。用户仅批准这一次请求；重复点击幂等，批准不变成永久 shell 授权。审批取消、不可用或过期均不允许执行。沿用 dsh 语义时，`never` 表示不弹审批且拒绝需要审批的请求，不是全部批准。

等待时持久化 Agent 当前 decision 和未完成 step，释放 worker，不占住线程或租约。新增工具审批专用挂起原因/状态；批准后 CAS 重新入队，从已保存的 decision 恢复，不重新问模型来替换已批准操作。恢复前重新检查硬限制；改变参数或输入意味着新请求。

启动前原子消费单次许可，并持久化 starting；helper 回报 started 后记录 running。为同一 operation-id 实现持久唯一约束与执行器单实例控制，防止两个 worker 同时启动。旧 worker 失去租约后必须终止 Job；接管者不能仅因租约过期就并行启动副作用操作。

崩溃后只读操作可以在确认旧进程已结束后重试。可写操作结果不明时进入 unknown，先检查产物/请求用户判断，不自动重跑；即使命令被拒绝或非零退出，也可能已经产生部分副作用。

## 9. 第一版具体接入什么

仅新增 `workspace_search_text`：模型给出普通文本查询和受限结果数量。服务端选出允许的文本文件并生成副本 manifest，调用受信 `rg` 的绝对路径，固定使用字面量匹配、禁用外来配置，以参数终止符隔离查询；不接受模型提供的 flags、shell、管道或重定向。

默认执行上限 15 秒，保留输出上限 16 KiB；输入准备也应设独立文件数/总大小/耗时限额。搜索返回原业务文件相对路径、命中位置、输入版本和截断标志，而不是把内部副本路径当作可供用户编辑的原文件。默认不自动安装缺失的 rg。

在 `TaskCapabilities` 的发现和实际调用两处检查权限，防止保存过的工具目录在授权撤回后继续调用。`personal_agent.py` 为审批等待增加独立控制分支，不被通用异常转换成 failed，也不提前保存已完成 step。`worker.py` 负责挂起/恢复和取消信号；API 负责用户决策，不直接启动执行进程。

前端在对话内显示真实操作、权限边界、有限输出和执行状态；需要审批时显示一次性确认卡片。可写阶段才增加产物检查并转换为现有 FileChangeService 提案，原文件仍由用户确认后版本校验写回。不默认支持新文件、删除或自动批量覆盖。

MCP 的外部执行、第三方 Skill 脚本、浏览器以及模型网关不是这个 runner 自动覆盖的范围。不得因为加入此后端就宣称整个 Agent 已被沙盒化。

## 10. 实施顺序与最小验证

1. 确认接受 partial 的可信固定工具路线，还是继续要求读侧/网络硬隔离；未确认不变更此前安全承诺。
2. 定义接口与能力报告，实现 Windows helper 的只读启动和 Job 生命周期；后端未就绪不影响已有聊天和文件工具。
3. 接入固定检索、执行记录和有限输出；随后实现独立工具审批挂起/恢复。
4. 确有写处理需求时，再加入内部副本 ACL 授权、产物审查和文件提案；不提前建设任意代码终端。

实施时只做少量定向验证，不调用真实模型、不跑全量测试：合法检索与危险参数拒绝；执行授权撤回；后端故障不降级；Job 超时/取消与子进程回收；审批重复/篡改及恢复不重复执行。可写阶段另验证区外写入、父目录删除和跨执行根/临时目录写入被拒绝。

后端必须如实记录“读取/网络并未隔离”，不可把已知边界写成测试通过了完全隔离。首版实施进行了真实 Windows 受控写拒绝、Low 完整性、环境清理、超时和取消回收验证，未修改原业务目录 ACL；未执行网络隔离验收，也不提供该保证。运行和验证记录见对应实施计划及 native report。

## 11. 本机源码依据

- [沙盒接口与模式](D:/agent/deepseek-harness/docs/subsystems/sandbox.zh.md:11)：模式仅定义文件效果，后端必须暴露 partial。
- [Windows 实现说明](D:/agent/deepseek-harness/packages/sandbox/sandbox-windows-acl/README.zh.md:100)：令牌、双访问检查、父目录删除保护与 SID 生命周期。
- [Windows 已知边界](D:/agent/deepseek-harness/packages/sandbox/sandbox-windows-acl/README.zh.md:127)：读取/网络不受限、常驻 ACL 和 Low 标签的影响。
- [令牌实现](D:/agent/deepseek-harness/packages/sandbox/sandbox-windows-acl/src/token.ts:112)：TokenDefaultDacl、Low 完整性、restricted token。
- [进程加入 Job 后恢复](D:/agent/deepseek-harness/packages/subprocess/win32-process/src/process.ts:501)：失败关闭与挂起启动顺序。
- [审批结果语义](D:/agent/deepseek-harness/packages/interaction/user-approval/src/index.ts:55)：单次批准及 never 的含义。
- [依赖与许可](D:/agent/deepseek-harness/packages/sandbox/sandbox-windows-acl/package.json)：现有 Node 包依赖及 MIT 声明。

结论：沿用 dsh 的执行边界和平台后端思路，主应用保持 Python；先可信只读操作，写操作只作用于内部副本，再经文件提案写回。不要把 dsh 的 Windows 写限制误当成完整的读写与网络隔离。
