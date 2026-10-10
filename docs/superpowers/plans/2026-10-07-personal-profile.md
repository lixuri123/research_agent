# Structured Personal Profile Implementation Plan

**Goal:** 实现开放人物属性的持久化与工作台管理，暂不向 Agent 上下文注入。

**Architecture:** 独立 `profile/` 包使用当前 Repository 的 SQLAlchemy Engine，新增属性和历史表。API 提供属性增删改、版本冲突校验、确认与失效；前端“关于我”面板管理动态路径及多种值类型。

**Tech Stack:** SQLAlchemy、Pydantic、FastAPI、React、TypeScript；不新增依赖。

## Constraints

- 用户已明确本次是人物描述，并授权完成该部分。
- 不修改现有任务、记忆召回或模型提示词；不调用真实模型。
- 属性名称开放；固定外层的来源、确认状态、范围、时间和版本。
- 所有修改需要对应当前版本；历史记录与当前属性同事务提交。
- 手工编辑保存为待确认；明确确认后成为有效属性。
- 现有本地单用户 API 使用 local owner；存储仍按 owner 隔离。

## Tasks

- [x] 编写持久化、动态 JSON 值、版本竞态、owner 隔离与 API 生命周期测试，先验证功能缺失。
- [x] 新增 `research_agent/profile/{__init__,schemas,store}.py` 和 `api_routes/profile.py`，API lifespan 初始化新增表。
- [x] 新增 `frontend/src/components/PersonalProfile.tsx`、人物属性类型和客户端接口，在工作台侧栏加入“关于我”；Vite 代理 `/profile`。
- [x] 沿用工作台样式，用分类树呈现属性；支持文本、数字、布尔、列表和对象值；提供编辑、确认、失效、删除及历史查看。
- [x] 跑人物属性测试及全量 pytest、前端构建，使用独立临时数据库检查新接口和前端交互。
- [x] 更新 README、目录导航和功能说明，记录验证与本次范围。

