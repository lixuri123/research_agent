# Provider Settings Implementation Plan

**Goal:** 在现有工作台配置 OpenAI 兼容 Provider。

**Architecture:** 独立 provider_settings 模块负责校验、原子存储和公开视图；config 将覆盖配置合入缓存的环境默认值。API router 提供读取、保存、测试，React 面板接入当前侧栏。

**Tech Stack:** FastAPI、Pydantic、Python、React、TypeScript、现有 OpenAI SDK。

## Verification

- 新增 Provider 测试 14 项与模型网关、Worker 相关检查：22 passed，排除 1 项已复现的基线时序失败。
- `test_restart_after_reader_does_not_repeat_completed_work` 使用 0.1 秒 Worker 租约，在本机租约过期，未到达模拟进程退出；恢复原有缓存配置读取函数后仍失败。
- `npm run build` 成功。
- `python -m scripts.check_provider_ui` 使用 Edge、真实设置路由与假模型完成桌面/手机验证，涵盖测试不保存、保存、密钥清空、刷新恢复、角色模型和无页面脚本错误。
- 真实 Provider 连通性未自动调用，可在设置页手动测试。
- 独立代码审查指出非法顶层输入可能被 FastAPI 默认错误回显；已改为统一校验脱敏并补充两路由的数组与字符串回归测试。

## Constraints
- 不覆盖 .env，不展示已有密钥，不访问真实 Provider 进行自动化测试。
- 当前目录没有 Git 元数据，直接在当前工作区实现，无法提交或创建 Git worktree。
- 用户已明确要求实现，当前会话内顺序执行。

## Tasks
- [x] 新增 tests/test_provider_settings.py：用临时路径测试 GET/PUT，密钥保留，跨进程读取，URL 校验和测试调用错误脱敏；先运行 `.venv/Scripts/python.exe -m pytest tests/test_provider_settings.py -q` 验证失败。
- [x] 新增 research_agent/provider_settings.py 和 research_agent/provider_api.py。GET /settings/provider 返回公开配置；PUT 保存完整非密钥字段与可选密钥；POST /settings/provider/test 使用未保存表单并保留已有密钥，只尝试一次。
- [x] 修改 config.py：缓存环境 Settings，在 get_settings 中加载经过校验的覆盖文件；保留 cache_clear 兼容接口。api.py 在静态文件挂载前注册 router，.gitignore 忽略 provider 文件。
- [x] 新增 ProviderSettings.tsx 与独立样式，定义前端接口，在 AgentApp 和旧 App 两个入口接入。显示加载、保存、测试结果和字段验证；留空密钥保留；测试编辑状态不保存。
- [x] 执行新增测试及模型网关/Worker 回归，运行 `npm run build`，检查文档与脱敏行为；README 记录配置文件与热更新规则。

