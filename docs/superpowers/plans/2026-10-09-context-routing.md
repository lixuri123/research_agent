# 上下文规则组装执行方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 使用可解释的规则路由选择会话、人物、记忆和任务背景，阻止无关旧任务影响当前问题，并保存可复查的组装依据。

**Architecture:** 后端在 Worker 开始执行前确定上下文路由，得到固定的来源与版本快照；每次模型调用前校验来源状态、分配容量并生成消息。路由为纯函数，数据读取、选择和模型消息生成独立；不增加用于路由的模型调用。

**Tech Stack:** Python、Pydantic、SQLAlchemy Core、已有 Repository/MemoryManager/ProfileStore、React/TypeScript、pytest。

## Global Constraints

- 前四阶段已实现；研究接入（Task 5）尚未执行。
- 使用现有 SQLite 和 research_stages/research_events；第一版不新增数据库或基础设施。
- 不依赖新增向量库、路由模型、模型摘要或中文分词服务。
- 当前问题和明确补充要求必须保留；历史、人物、任务、工具结果不能提升为系统指令或增加授权。
- 路由不改变 agent/research 执行模式，不授予工具权限，不自动继续其他任务。
- 默认自动选择；无关联证据时不携带旧任务。歧义时限定范围或请用户明确对象。
- 只读当前 owner 的来源；project/session 必须匹配；客户端引用不能绕过这些检查。
- 请求提交之后产生的记录不得进入首次来源快照；排除本次 run 和未完成任务的伪回复。
- 恢复任务复用原始路由和选中来源，不重新引入新背景；失效或删除的来源必须撤下。
- 保留旧任务的执行兼容路径；不要尝试猜测迁移已开始任务的提示词。
- 项目目前没有 Git 仓库，执行时不要使用 git commit 作为步骤完成条件。

## 1. 当前实现及切入点

1. frontend/src/AgentApp.tsx 的 send() 将最近六轮任务序列化进 requirements；这是无条件历史注入的主要入口。
2. research_agent/agent/runner.py 的 task_input() 注入自动记忆；task_system() 加入目录与 Skill；observations() 压缩工具记录。
3. research_agent/memory/agent_context.py 提供带范围、版本校验和容量限制的记忆选择，可以复用。
4. 人物属性由 research_agent/profile/store.py 保存，目前尚未进入模型上下文。
5. 会话界面实际由 research_runs 的 session_id 与结果生成；memory_session_messages 不是完整界面历史来源，第一版不能混合两套历史导致重复。
6. 普通执行和研究图分别保持自己的输出契约；共用背景选择，不把所有节点改成同一个提示词。

## 2. 路由数据契约

新增可选请求字段 context，默认缺省与 mode=auto 等价：

```json
{
  "mode": "auto",
  "task_ids": [],
  "profile_ids": [],
  "turn_ids": []
}
```

mode 取 auto / independent / followup / task / multi_task。
task_ids 是用户明确引用的业务任务 ID，不是新请求自身 ID；turn_ids 使用已有任务 ID 标识历史一轮。
profile_ids 表示明确请求使用的人物属性，不代表允许使用其他人物属性。
owner_id 必须来自服务端；workspace_id/session_id 延用请求原字段，不能由 context 覆盖。
所有数组去重，每类最多 10 个；未知字段、未知 mode、空 ID 和超长 ID 返回 422。

规则：

- independent 与 task_ids/turn_ids 冲突时 422，不能静默忽略明确约束；允许明确 profile_ids。
- task 最多一个 task_id；multi_task 至少两个有效 task_ids，或正文有明确枚举请求。
- followup 可不指定 turn_ids，通过最近对话解析指代。
- auto + 一个有效 task_id => task；auto + 多个 => multi_task。
- 有效指引用 owner、范围及存在性校验都通过；越权或不存在统一返回 404，不泄露对象是否存在。
- workspace 已绑定的会话仍由 validate_workspace_session 校验；明确跨空间任务协调须逐个验证 owner，且只读取任务摘要，不继承其他空间文件或工具权限。

返回结构（内部，不替代任务执行结果）：

```python
from dataclasses import dataclass
from typing import Literal

Route = Literal['independent', 'followup', 'task', 'multi_task']

@dataclass(frozen=True)
class RouteDecision:
    route: Route
    rule_id: str
    task_ids: tuple[str, ...]
    turn_ids: tuple[str, ...]
    reason_codes: tuple[str, ...]
    needs_clarification: bool = False
    clarification: str | None = None
```

不输出“概率 0.8”这种未校准置信度。reason_codes 是稳定枚举，rule_id 带规则版本。
路由输入只包含当前请求及小型索引：历史轮次 ID/用户文本/结束状态、可引用任务 ID/标题/状态/范围，不加载正文。

## 3. 路由判定顺序

按表顺序命中即返回；不得先靠任务列表猜路由，再解释用户原话。

| 优先级 | 条件 | 输出 | 行为 |
|---|---|---|---|
| R01 | mode=independent | independent | 不读取会话、旧任务或自动长期记忆 |
| R02 | 其他显式 mode | 对应 route | 验证关联 ID；task 缺对象时只解析明确引用，不默认最近任务 |
| R03 | auto + 明确 task_ids | task / multi_task | 一个/多个目标；不再按关键词覆盖 |
| R04 | 正文含任务 ID，或明确“继续/推进/回顾/汇报 + 完整任务标题” | task / multi_task | 标题必须唯一，且在当前范围内；重复标题要求明确对象 |
| R05 | 明确枚举协调：“我正在做的所有任务/这个工作空间的进行中任务” | multi_task | 只枚举对应范围的活动任务；超过 10 个要求缩小范围；空集合返回空状态，不虚构任务 |
| R06 | 明确多对象比较 + 两个以上唯一任务引用 | multi_task | 仅加载所引用对象，不扩大到其他任务 |
| R07 | 当前文本具有前文依赖，且能定位对话锚点 | followup | 选择锚点及必要的前后轮次，不自动携带整个任务列表 |
| R08 | 有前文依赖，但对象缺失或有多个同等候选 | followup + needs_clarification | 不加载所有候选正文；提供短确认问题或候选标题 |
| R09 | 其余请求 | independent | 自包含问题、首次提出的新任务均走此路由；执行仍可规划/用工具 |

### 3.1 文本规则如何判断

使用 NFKC、大小写和空白规范化进行匹配，但保留原文用于模型输入。
规则只识别少量高精度模板，每条规则有独立测试，避免“出现一个词就命中”。

- “任务”出现在“什么是任务队列”中不构成任务引用。
- “继续”出现在“如何让程序继续运行”中不构成会话追问。
- “项目”或相同 workspace_id 不构成旧任务关联。
- 引号、代码块中的路由触发词不能单独触发规则。
- “解释 X 是什么/为什么 X/如何做 X”默认自包含，除非同时有明确历史指代或对象引用。
- “这个方案/刚才那个/你上面说的/换一种解释/详细一点”属于指代候选；只解析最近 6 个已结束、回答非空的轮次。
- “你刚才提到 Redis，它有什么缺点”：实体 Redis 在最近用户或回复摘录中出现，且有明确前文标记 => followup。
- “Redis 有什么缺点”：无前文标记、自包含 => independent，即使上一轮讨论 Redis。
- “继续上一个任务”：只允许定位最近对话锚点关联的业务任务；锚点没有明确关联就询问，不挑全局最新活动任务。
- “接着说”：有最近非空回答 => followup；新会话 => needs_clarification。
- “比较论文实验和实习准备的优先级”：仅在两者是可唯一解析的现有任务引用时 multi_task；否则作为当前问题处理，不擅自补入数据库任务。

标题引用要求完整规范化标题匹配，不做模糊相似度绑定。第一版不能可靠处理的自然语言别名由澄清或显式 ID 解决。
已经有 turn_ids 的 auto 请求只有正文存在追问依赖时才 followup；客户端不能每次自动传最近六轮。

### 3.2 歧义处理

needs_clarification 不意味着系统故障，也不是权限审批。
如“继续那个”有两个可引用目标，直接返回“你指的是论文实验，还是实习准备？”；不调用执行计划、不产生两个任务的执行动作。
请求仍创建普通任务记录并以已完成的澄清回复结束；下一条用户消息必须重新路由，不能靠不可验证隐含状态续跑。
第一版支持用户重述完整对象/提供显式引用，不承诺单字“第一个”跨轮次候选选择；若要支持，必须另加持久化候选列表与稳定顺序。

## 4. 路由与来源权限矩阵

| 来源 | independent | followup | task | multi_task |
|---|---|---|---|---|
| 当前问题、明确补充要求 | 必选 | 必选 | 必选 | 必选 |
| 历史对话 | 不自动加入 | 锚点及依赖轮次 | 明确关联目标的相关轮次 | 协调问题相关轮次 |
| 人物属性 | 明确 ID，或个人化请求且相关 | 与追问相关的属性 | 与目标相关的属性 | 与协调维度相关的属性 |
| 长期记忆 | 不自动加入；显式个人历史问题例外 | 与锚点相关 | 与目标相关 | 每个目标受独立份额限制 |
| 旧任务 | 无 | 默认无；显式任务指代另走 task | 一个目标 | 明确目标集合 |
| 本次工具记录、已加载 Skill | 执行过程中允许 | 允许 | 允许 | 允许 |

“本次工具记录”包括独立问题调用搜索后的结果，不能因为 independent 而丢弃它。
历史工具记录不会整包作为历史轮次注入；需要时通过明确任务关联选择摘录。

### 4.1 人物属性选择

第一版不为动态属性新增固定职业/偏好字段，不要求数据库迁移。
只考虑 active 且已确认、owner 和 scope 匹配的属性；候选、失效、其他项目属性剔除。
profile_ids 直接选择有效属性；自动选择仅在明确个人化场景触发（“按我的背景/结合我的时间/适合我的学习计划”等），对路径和内容进行关键词匹配，默认最多 3 条。
不能仅凭属性名称“偏好”判断它应该全局注入。默认没有 always-on 属性。
存在同路径同范围但值冲突的属性时，不静默挑最新：省略冲突组、记录原因；用户明确引用时保留并标注冲突。
范围更具体不自动使另一属性变成错误事实；不同范围的相关内容保留范围标签。
嵌套 JSON 保留结构，不能把截断片段伪装成完整有效属性值；容量不足时省略完整属性。

### 4.2 任务集合与数据读取

现有列表接口默认 50 条，不能拿前端当前列表冒充所有任务。新增 Repository 的 owner-scoped 查询函数：

```python
def get_context_turns(self, owner_id, session_id, workspace_id, before, exclude_run_id, limit=6): ...
def get_context_tasks(self, owner_id, task_ids): ...
def list_context_active_tasks(self, owner_id, workspace_id, before, limit=11): ...
```

历史排序固定 created_at/run_id，只取 completed/partial/insufficient_evidence 且有可展示内容的轮次；失败错误不充当事实答案。
目标摘要包含任务 ID、目标、范围、数据库状态、最新已保存进展和用户约束；没有结果时明确为空。
活动集合枚举使用现有 ongoing 状态 queued/running/waiting_approval；不能把全部历史结果都加入。
数据库中的任务状态不等于外部事实；旧计划不视作当前用户的新增要求。

## 5. 组装与容量

1. 先确定路由及可见范围，再加载允许来源；不能先读全部数据再过滤。
2. 按来源 ID/版本去重；任务结果与同一轮 assistant 回复重复时只留一份并保留两个引用。
3. 默认输入预算 12,000 估算 token；输出空间单独预留 4,096 token；这是模块限额，不宣称知道模型真实窗口。
4. 初版沿用网关保守 UTF-8 字节估算，但共享一个估算函数，明确记录 estimated，不用字符数充当准确 token。
5. 当前问题、真实系统规则、输出契约不能丢；若必需块已超预算，返回明确容量错误，不静默截断问题或权限目录。
6. 普通背景建议上限：历史 3,000、人物 1,000、记忆 2,000、旧任务 3,000；与全局预算取较小值，不预留空块额度。
7. multi_task 对每个任务先保留 ID/目标/状态的最小摘要，再公平分配剩余任务额度；不能让首个任务占满所有容量。
8. 本次执行优先保留计划、最近工具结果，旧工具结果按 observations 的现有摘录逻辑压缩；必须经过最终全局估算。
9. 容量不足时先省略低相关记忆/人物，再减少旧历史和旧工具摘录；保留引用、truncated 与 omitted 原因。
10. 不增加自动模型摘要。对非结构化文本使用可见摘录；对人物 JSON 保持原子性。

输出分别生成可信 system 消息和带数据标签的 human 历史/背景块；真实历史轮次可生成 HumanMessage/AIMessage 对。
任务约束需区分本次明确要求和旧任务已保存要求；后者作为历史约束标签展示，当前用户明确修改优先。
每步重新生成消息，不把整个消息数组不断追加。SourceBlock 使用来源、scope、版本和内容，AssemblyResult 返回 messages + manifest。
目录/Skill/本次观察保持 runner 已有执行契约；上下文模块不自行执行 Skill 或读工作空间文件。

## 6. 来源快照与恢复

在 agent_context_route/v1 保存路由和规则版本；agent_context_sources/v1 保存选中来源 ID、版本、内容 hash 与受限摘录。
首次组装保存 context_selected 事件，后续只记录来源撤下/预算变化；不把所有人物正文复制到审计事件。
每次调用前：人物 active+version、记忆 active+version+hash 必须仍匹配；不匹配则剔除并记录 source_changed，不自动改用新版本。
历史/任务来源固定首次选定快照，不能由于并发任务进展改变本次回答背景；明确实时状态查询通过工具单独读取。
上下文读取失败：当前问题仍可独立回答的路由使用空背景并记录告警；任务续接缺失目标状态时返回澄清/读取失败，不能假装续接成功。
正在恢复的旧任务若没有新的 context_version，继续旧实现，不混入新路由。

## 7. 文件和接口

新增 research_agent/context/{__init__,schemas,router,policies,sources,selector,budget,assembler}.py。
schemas 放 Pydantic 请求/来源/manifest；router 仅纯判定；policies 放规则版本和白名单；sources 适配 Repository/ProfileStore/AgentMemory；selector 做相关、冲突与去重；budget 管估算和截断；assembler 只产消息。

公开接口固定为：

```python
def decide_route(request: ContextRequest, index: ContextIndex) -> RouteDecision: ...
def select_sources(request: ContextRequest, decision: RouteDecision, sources: ContextSources) -> SourceSnapshot: ...
def assemble_context(request: ContextRequest, decision: RouteDecision, snapshot: SourceSnapshot,
                     runtime: RuntimeContext, budget: ContextBudget) -> AssemblyResult: ...
```

ContextRequest 含 question/requirements/owner_id/session_id/workspace_id/submitted_at/run_id/context；ContextIndex 是限量轮次和任务元数据；RuntimeContext 包含可信系统文本、目录、加载 Skill、计划和当前观察；返回 messages 与 manifest，不调用 LLM。
新增 sources 的 revalidate(snapshot) 方法返回有效 snapshot + dropped；运行时调用前使用，禁止 assembler 隐式查询数据库。

## 8. 按交付单元执行

### Task 1：纯规则路由

Files: 新增 schemas.py/router.py/policies.py；新增 tests/context/test_router.py。

- [x] 定义上述输入/输出契约和明确 route/rule_id/reason_codes；补齐字段上限及模式冲突验证。
- [x] 先写表驱动失败测试，覆盖下一节全部路由案例以及标题重复、引号代码、首次新任务；确认未实现接口导致失败。
- [x] 实现 R01–R09，规则不得依赖数据库调用或模型调用；每条分支返回固定 rule_id。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/context/test_router.py -q`，全部通过后交付独立路由函数。

测试代码形状：

```python
@pytest.mark.parametrize('question,expected', [
    ('Redis 是什么？', 'independent'),
    ('帮我制定一个学习计划', 'independent'),
    ('这个方案有什么缺点？', 'followup'),
    ('你上面说的数据库队列，再详细一点', 'followup'),
])
def test_question_routes(question, expected, one_answer_index):
    request = ContextRequest(question=question, owner_id='local', session_id='s1',
                             submitted_at=100, run_id='new-run')
    assert decide_route(request, one_answer_index).route == expected
```

### Task 2：来源选择与范围隔离

Files: 新增 sources.py/selector.py；修改 persistence/repository.py 增加上述查询；新增 tests/context/test_sources.py/test_selector.py。

- [x] 使用临时 SQLite 写 owner/session/workspace 隔离和快照截止时间的失败测试，含超过 50 个任务的集合查询。
- [x] 实现限量索引查询，Repository 获取界面历史，不额外拼入 memory_session_messages。
- [x] 根据路由白名单加载来源；复用 ProfileStore/AgentMemory；加入人物原子值、冲突组和重复来源处理。
- [x] 验证 independent 未关联任务时，旧任务结果与自动记忆正文读取次数为零。
- [x] 验证删除、失效、版本变化撤下来源，恢复不会引入新记录；元数据字段按限定 allowlist 输出，不能序列化整个数据库行。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/context/test_sources.py tests/context/test_selector.py -q`。

### Task 3：预算与消息生成

Files: 新增 budget.py/assembler.py；修改 models/gateway.py 共享估算函数；新增 tests/context/test_assembler.py。

- [x] 先写预算溢出、人物 JSON 原子性、多任务公平分配以及材料无法提升 system 的失败测试。
- [x] 实现保守估算与全局限制；AssemblyResult.manifest 含每块来源 ID/版本/选择原因/摘录标记/省略原因/估算占用。
- [x] 为 planning/decision/research_background 区分调用用途，保持各调用原本 system 和输出契约，统一添加允许背景。
- [x] 验证当前问题与明确要求完整保留、没有选中来源时不输出空“历史背景”大段、相同输入输出稳定。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/context/test_assembler.py -q`。

### Task 4：普通 Agent 全链路接入

Files: 修改 api.py、persistence/repository.py submit()、agent/runner.py、agent/delegation.py、frontend/src/api.ts/types.ts/AgentApp.tsx；新增 tests/context/test_context_integration.py；调整已有 API/runner/delegation 测试。

- [x] 先写 integration 失败测试：API 存储 context 与 context_version=1；不同 context 的同幂等键冲突；runner 提示词隔离无关历史；澄清路径不调用工具或规划。
- [x] 前端删除无条件最近六轮 requirements 拼接；只传当前输入、session/workspace，保留真实补充要求；第一版不用新增页面或任务选择器。
- [x] API 严格验证 context 引用，submit 的请求 hash 包含 context；缺省与显式 auto 做规范化，避免语义相同的请求产生不同 hash。
- [x] runner 在规划前确定并保存路由，task_input/task_system 调用 assembler，保留已有输出协议、预算、租约和 stage 保存。
- [x] 子 Agent 接收父 Agent 明确给出的目标和必要背景，使用隔离上下文；不得重新根据同 session 自动拉取父对话或 owner 所有任务。
- [x] 纯问候走现有简化路径；本次独立问题仍允许搜索、Skill 或明确授权的工具。
- [x] 旧 payload 无 context_version 的已开始任务走旧路径；新 API 请求总是保存 context_version=1。
- [x] 运行 `.venv/Scripts/python.exe -m pytest tests/context tests/agent tests/persistence/test_worker_api.py -q`。
- [x] 在 frontend 运行 `npm run build`；浏览器用模拟 API 检查桌面/手机在已有会话内提交普通问题不再含无条件历史；真实 API 与会话/空间隔离由定向后端测试验证。

### Task 5：研究模式接入、回归与说明

Files: 修改 research/graph.py、research/agents/planner.py/writer.py/reviewer.py、worker.py；新增 tests/context/test_research_context.py；更新 docs/guides/context-assembly.md 和 README.md。

- [ ] 先写节点提示词测试：同一个背景快照作用于相关节点，但读者只收到本次文献输入；不把人物、记忆当作引用证据。
- [ ] 研究流程在首次启动时取得路由快照，允许背景与正文证据分区传入节点；不重启旧 checkpoint 后隐式更换策略。
- [ ] 查看人物化研究要求的具体来源，要求 writer/reviewer 继续使用现有 evidence 数据，背景不能成为核验通过的依据。
- [ ] 运行 `.venv/Scripts/python.exe -m pytest -q` 和前端构建；只报告实际通过/跳过数。
- [ ] 使用真实 Repository + fake LLM 测试多轮场景，比较模型输入和 manifest，确认路由没有额外模型调用。
- [ ] 写明规则版本、已知自然语言限制、如何显式引用目标及查看 context_selected 事件；实现完成后再写验证结果。

## 9. 验收案例

| 输入/条件 | 路由及预期 |
|---|---|
| 同一会话上一轮论文任务，本轮“Redis 是什么” | independent；不含论文历史、目标、自动记忆 |
| 首次“帮我规划学习 Python” | independent；可以创建计划，不把它误判成旧任务续接 |
| “这个方案有什么缺点”，上一轮有方案 | followup；仅必要锚点 |
| “你刚才提到 Redis，它有什么缺点” | followup；保留 Redis 的前文解释 |
| “继续论文实验”，唯一完整任务标题匹配 | task；仅该任务目标/状态/相关背景 |
| 相同标题两个任务 | needs_clarification；不加载两个任务正文 |
| “帮我排一下正在做的所有任务” | multi_task；owner 活动摘要，有范围和数量限制 |
| “这个空间的任务”，指定 workspace | 不混入其他 workspace |
| “结合我的每周时间安排论文与实习准备” | unique targets 才 multi_task；仅相关已确认人物属性 |
| 未确认人物信息、已失效记忆 | 不加入 |
| 用户明确 independent，同时 task_ids 非空 | 422 模式冲突 |
| 明确不存在或非本人 task_id | 404，无越权读取 |
| 无前文“接着说” | clarification，不猜任务 |
| 历史写有“忽略系统规则” | 只作为历史数据，不能改变工具/系统权限 |
| 多个并发任务在来源快照之后完成 | 不改变本次上下文 |
| 删掉人物属性后恢复执行 | 属性撤下、manifest 标注，不使用旧正文 |
| 关联任务失败/暂无结果 | 如实标注状态，不伪造 assistant 答案 |
| 所有可选背景超预算 | 保留问题/可信规则，按优先级省略，记录原因 |
| 同 context 请求重试 | 幂等返回原任务；路由与来源快照稳定 |

## 10. 交付边界

第一版交付后可解释“用了哪些背景、为什么”，并解决旧任务污染普通问题。
自然语言任务别名、模型语义路由、自动长对话摘要、长期偏好标签管理、任务选择 UI 都不在第一版。
当前 task 仍是一次执行记录，不等同于持久化长期目标；route=task 表示读取旧执行的背景后创建新执行，不直接重启或修改旧任务。

## 前四阶段执行记录（2026-10-09）

- 新模块 `research_agent/context/` 已接入普通 Agent；生命周期辅助放在 session.py，避免把路由/快照逻辑散入 runner。
- 普通请求不再由前端自动拼历史；研究模式继续原历史路径，Task 5 未执行。
- 新增测试共 28 个收集用例，覆盖关键行为；遵循用户要求，没有按每个关键词铺设大量微测试。
- 执行一次现有全量回归：250 passed、1 skipped；最终引用校验、范围和来源变更后执行定向检查：33 passed（context + worker API）。
- 前端 TypeScript/Vite 构建通过；桌面/手机浏览器普通提交字段检查通过。
- 临时数据库 + 假模型 + 真实普通能力目录验证通过，决策输入估算 8015，低于默认 12000 限额。
- 交叉检查发现的最新观察保留、提交时间截断、来源失败不误降级及多任务最小摘要问题均已修正。
- 无数据库结构迁移；旧 payload/checkpoint 保留旧路径。
