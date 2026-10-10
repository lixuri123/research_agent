# 记忆系统 V2 方案

> 历史文档：保留设计与实施背景，当前结构请参阅 [目录说明](../architecture/directory-structure.md)。

状态：早期完整方案，尚未实现。当前认可的轻量落地路径见 [轻量记忆架构](../architecture/memory_architecture_light.md)；本文保留为未来扩展参考，不涉及不同工作流节点的接入分配。

## 1. 目标与选择

解决五个问题：记忆属于谁、从哪里来、适用在哪里、是否仍有效、为何被召回。

采用 Postgres 权威存储，显式文件 SQLite 作为本地开发后端；Redis 为可重建会话缓存。第一步只做结构化与关键词检索，数据治理完成后再接 Milvus 派生索引。关系图谱不在本次范围内。

备选路径：仅 SQL 最容易保证一致性，适合初期；SQL + 向量索引适合语义召回但增加同步成本，作为第二步；多类记忆分别建库会增加生命周期管理复杂度，当前不采用。

## 2. 分类：内容、保留策略与作用范围分开

| 维度 | 取值 | 含义 |
|---|---|---|
| 内容类型 kind | semantic / episodic / procedural | 知道什么 / 发生过什么 / 怎么做 |
| 保留策略 retention | session / persistent | 会话内保留 / 跨会话保留 |
| 作用范围 scope | session / project / owner | 某会话 / 某项目 / 所有者个人 |
| 内容子类 subtype | preference / constraint / finding / episode / strategy | 在主类型内明确用途 |

短期、长期不再充当内容类型。实体是内容中可查询的标签或结构化引用，不单独定义为第四种记忆。未经显式授权不提供跨所有者共享作用域。

示例：中文写作偏好是 semantic + preference + persistent + owner；本次只研究近五年是 semantic + constraint + session + session；一次失败检索是 episodic + episode + persistent + project；经测试有效的检索步骤是 procedural + strategy + persistent + project。

作用范围与保留策略互不替代：一条会话范围记录可以为用户指定的后续复查保留，但不能因此自动扩大可见范围。

## 3. 数据模型

### 3.1 MemoryRecord：身份与当前生命周期

| 字段 | 要求 |
|---|---|
| id / current_revision_id | 稳定身份 / 当前不可变版本 |
| owner_id | 必填，由可信调用上下文提供 |
| scope / project_id / session_id | 按范围校验必填 ID，不用空 ID 表示公开 |
| kind / subtype / retention | 独立分类字段 |
| status | candidate / active / stale / superseded / deleted |
| created_at / updated_at / expires_at | 时间与可选过期时间 |
| superseded_by / subject_key | 替代关系；偏好等单值信息的规范键 |

### 3.2 MemoryRevision：内容与可追溯版本

| 字段 | 要求 |
|---|---|
| revision_id / memory_id / version | 不可变版本 ID、所属记录、递增版本 |
| content / payload / content_hash | 可读内容、类型化结构、规范内容哈希 |
| provenance_ids / parent_revision_ids | 原始来源、摘要或提炼所依赖的版本 |
| validation_status | unverified / source_checked / evidence_supported / evaluated |
| validation_method / validation_record_id | 验证方式和可审查记录 |
| valid_from / valid_until | 信息的适用时间，与存储过期时间不同 |
| applicability | 使用条件，例如语言、研究领域、数据库或工具版本 |
| importance | 排序辅助值，不表示真实性 |

### 3.3 MemorySource：来源

source_type 取 user_message / document_version / report_version / run_event / evaluation_record / imported_record。
保存 source_id、源版本、定位信息、作者/产生者、产生时间及访问范围。user_message 必须明确是否为用户直接表达；模型推断出的偏好仍为 candidate。

科研发现必须关联具体 claim/evidence ID；报告获得人工批准不自动等于每条事实正确。来源检查、证据支持与用户批准分别记录。

### 3.4 三种类型的 payload

- semantic：subject、predicate、value、适用时间；finding 必须有 claim_id/evidence_ids，preference 有 subject_key 和 explicit/inferred 标记。
- episodic：task_id、run_id、研究问题、输入条件、行动引用、结果、失败原因、用户反馈。优先引用已有阶段记录，避免复制整份任务轨迹。
- procedural：目标、适用条件、步骤、输入输出契约、必要权限、失败处理、成功及失败评测引用。存储文本策略，不存可自动执行的任意代码。

程序记忆只能建议策略，不能产生工具授权。工具权限仍由应用服务决定。

## 4. 统一接口与兼容策略

保留门面名称：remember、remember_episode、consolidate、recall。新增 remember_procedure、get、update、invalidate、forget、assemble。

```python
manager = MemoryManager(access_context=trusted_context)
manager.remember(content, kind="semantic", subtype="preference",
                 scope="owner", retention="persistent", source=source_ref)
manager.remember_episode(episode, source=run_ref)
manager.remember_procedure(procedure, source=evaluation_ref)
manager.consolidate(revision_ids, policy="summarize")
manager.recall(query, top_k=5, filters=filters)  # 保留列表返回形式
manager.assemble(query, token_budget=budget, filters=filters)  # 返回 ContextPack
manager.invalidate(memory_id, reason="source_changed")
manager.forget(memory_id)
```

access_context 由应用边界提供，包含 owner_id、可访问项目及当前会话。不能由模型或用户请求任意声明。当前单用户模式由服务端固定提供 local。管理器不再是跨所有者的全局单例；底层连接池可以共享。

MemoryStore 仍提供 add/search，但 search 必须携带可信访问上下文与类型化过滤条件。clear 改为限定范围的管理操作，拒绝没有范围的全库清空。

兼容的是门面与主要调用习惯，不承诺保留错误语义。旧 memory_type=long_term 显式映射为 semantic + persistent 并发出弃用提示；缺失来源或作用范围的旧记录进入 candidate 隔离区，不能自动成为 active。短期写入必须指定当前会话，不能遍历全部会话补齐上下文。

MemoryItem 作为返回视图保留 id、content、importance、created_at 等属性，新增类型、来源、范围和版本字段；旧字段 memory_type 仅作为过渡视图。

## 5. 写入与生命周期

```text
输入 → 身份/范围检查 → 来源与结构检查 → 幂等去重 → candidate
                                                        ↓ 验证或明确确认
                                                     active
                                                        ↓ 过期/源变化/冲突
                                                      stale
active → 新版本替代 → superseded
任意未删除状态 → 删除请求 → deleted → 清理正文与派生副本
```

- 明确用户偏好：确认来源确实是用户表达后可激活；推断偏好先等待确认。
- 情节：确认源 run 与事件真实存在后可激活，但记录的模型结果仍标明其验证状态，不能充当事实证据。
- 科研发现：证据可访问、版本有效、支持关系有核验记录后才进入证据支持类别。
- 程序策略：由有效情节提炼为 candidate，满足明确评测标准后才以 evaluated 状态作为优先策略召回。一次任务成功不足以证明泛化有效。

幂等键包含所有者、来源版本、提取规则版本和内容哈希。完全重复不新增；语义近似只能产生合并建议，不能自动合并数值、时间或适用条件不同的事实。

用户将“中文”改为“英文”时，同一 scope + subject_key 产生新版本并保留历史。本次任务的临时约束不改写全局偏好。科研结论相互矛盾时保留两条与 conflict_group，不用“更新较晚”判定哪条为真。

模型摘要只生成新 candidate revision，携带所有 parent_revision_ids。摘要不能提升验证等级或扩大访问范围；不同来源的事实应保留独立证据关联。自动压缩优先用于会话经历，不自动压缩并替换可引用科研证据。

## 6. 短期窗口与上下文预算

消息持久化在 SQL，会话缓存只保存最近记录的 ID/版本。20 条可保留为读取候选的软上限，不能作为上下文安全保证。

```text
可用记忆预算 = 模型上下文上限
             - 系统提示 - 当前输入 - 工具/证据等其他内容
             - 输出预留 - 安全余量
```

组装器只接收剩余预算，由调用者声明其他输入占用。Token 计数使用对应模型的分词器；未知模型使用保守估算，并保留不确定性余量。最终完整请求再做一次总长度检查。

窗口溢出时：当前目标、明确约束与未解决事项优先保留；旧交互可生成带来源的滚动摘要；大段原文保留引用按需读取。摘要失败不删除源记录，可退回更小窗口。输入超过可用预算时要求缩小材料或分批处理，不能通过截断明确约束假装满足预算。

最近消息是否进入当前上下文，与底层记录是否删除是两种操作。窗口移出不等于永久遗忘。

## 7. 持久化与派生数据一致性

SQL 表：memory_records、memory_revisions、memory_sources、memory_source_links、memory_events、memory_outbox；检索日志另存受控统计，不默认重复存储全文。

一个事务写入当前版本、来源关系、审计事件和索引更新 outbox。后台任务幂等更新 Redis/Milvus。向量 ID 使用 revision_id，保存内容哈希和 embedding 版本；旧索引不能覆盖新版本。

召回向量候选后必须回查 SQL，核验 owner、范围、当前版本、状态、过期时间及来源访问权限。索引过滤用于减少候选，SQL 过滤决定是否可返回。

SQL 不可用时记忆写入失败；记忆读取可以明确返回 unavailable，由上层决定是否无记忆继续任务。Redis 不可用则直接读 SQL。Milvus 不可用则使用 SQL 检索并标明降级，不能读取未经授权的全局内存兜底。

删除先事务标记 deleted，使新召回立即不可见，再清理向量、缓存、正文、旧版本和派生摘要。子摘要暂标 stale，重新生成时排除被删来源。事件只保留必要的无正文审计信息。历史 Prompt 快照或外部追踪中存在的正文也纳入保留/删除策略；删除后允许历史回放不再完整。

## 8. 召回质量

处理顺序固定：

```text
可信身份与范围过滤
    → 类型/状态/适用条件过滤
    → SQL、关键词及可选向量候选检索
    → 合并与按版本去重
    → 相关性重排与最低匹配门槛
    → 冲突及来源检查
    → 预算内选择
```

规则优先于分数：已删除、过期、越权、不适用记录必须先排除，不能因高相似度重新进入结果。

不同类型分别排序：偏好以明确表达、范围与当前有效版本优先；情节以输入条件相似和结果有记录优先；程序以适用条件匹配和有效评测记录优先；发现以证据有效及问题相关优先。

第一版采用可解释规则重排，不先设未经验证的综合权重。候选量、相似度门槛与重排参数从标注集校准；没有合格结果返回空列表，top_k 是上限，不是必须凑满的数量。

重要度不替代相关性，频繁访问不提升事实可信度。统计 last_accessed_at/access_count 用于诊断与缓存，不能据此自动确认内容。

## 9. ContextPack 组装结果

输出包括：rendered_text、selected revision IDs、来源引用、estimated_tokens、selection_reasons、conflicts、omitted_count、retrieval_mode。

内容按用户偏好/当前约束、相关历史经历、适用策略、带证据的历史发现分区；各区标注使用性质，保留事实与推断差异。用户本次明确要求优先于历史偏好，外部文本与程序策略没有系统指令或授权效力。

记录每条入选理由，支持回答“为何召回这条记忆”。默认只记录 ID/版本与分数等元数据，完整 Prompt 保存需服从数据策略。

## 10. 测试与验收

确定性验收要求：

- 重启后记忆、来源与生命周期状态可读取；任何后端异常不虚报写入成功。
- 跨所有者、无权限项目、非当前会话的记录不会越界返回。
- 重复来源幂等；更新有历史版本；删除和过期记录即使向量尚未清理也不能被召回。
- 摘要不扩大范围、不提升验证等级，删除来源会使依赖摘要失效。
- 无相关结果返回空，超 Token 预算不组装，无效程序记忆不能授予工具权限。

质量评测集覆盖相似但不适用的任务、过期偏好、同主题不同结论、相反事实、重复记忆、长消息和恶意历史文本。衡量 Recall@K、Precision@K、冲突识别、过期/越界结果数量、上下文超预算率，并明确分母和标注依据。

对同一批任务比较无记忆与有记忆版本的结果质量、成本和耗时。召回正确但任务效果无提升时，不能认定系统已经有效。没有足够标注样本前不承诺数值效果目标。

## 11. 落地顺序

M1：类型化模型、可信访问上下文、SQL 仓储、来源与不可变版本，先用规则/关键词检索。旧模块通过适配器过渡，旧记录隔离导入，不默认跨所有者迁移。

M2：更新/失效/删除、依赖传播、会话滚动摘要、Token 预算与 ContextPack，形成完整生命周期。

M3：outbox 同步、向量候选与重排、质量标注集及有无记忆对照评测。SQL 查询已满足需求时不强制引入 Milvus。

M4：程序记忆的提炼、评测、适用条件与失败经验管理。工作流按不同节点接入的分配另行设计，当前主流程不会因本设计文档自动启用记忆。
