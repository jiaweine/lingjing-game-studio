# WorldForge / 灵境 Architecture

## 1. 系统边界

灵境分成两层：

1. **产品控制面**：身份、工作空间、任务、消息、素材、执行 Job、证据、交付、人工反馈、审批、指标、审计与实时事件。
2. **WorldForge Runtime**：世界状态、规划、候选未来试演、Sandbox、Verifier、Memory / Skill / Policy 与回归约束。

外部或本地推理资源只提供感知、文本理解和推理能力。它们不能直接绕过产品权限、修改 canonical state、跳过验证、消费审批或决定任务是否完成。

```text
Browser Workspace
    ↓
FastAPI / Auth / Workspace Guard
    ↓
Product Store ───── Object Storage
    ↓                    ↓
Durable Job Queue ── Asset Materialization
    ↓
Worker / ProductAnalyzer
    ↓
WorldForge Runtime + Server-side Inference Routing
    ↓
Result / Evidence / Deliverables
    ↓
Human Feedback / Quality Gate / Product Events
```

## 2. 产品控制面

产品持久化状态包括：

- users
- workspaces
- memberships
- workspace_invites
- conversations
- messages
- assets
- task_events
- audit_logs
- jobs
- approval_requests
- result_feedback
- product_events

`conversations` 同时保存任务负责人、任务状态、置顶和归档信息。

### 工作空间与权限

所有产品资源都在服务端按 workspace membership 校验。前端隐藏按钮不是权限边界。

- Owner / Admin：成员管理、邀请和治理动作；
- Member：在权限范围内创建和推进任务；
- Viewer：服务端只读。

会话解析会重新读取当前 membership role，使角色降级或移除能够及时生效，而不是只等待旧 token 过期。

### 任务生命周期

产品任务支持：

- 搜索、重命名、置顶；
- 归档 / 恢复；
- 负责人分配与交接；
- 深链接；
- 实际停止；
- 最近一次失败/停止 Job 的安全重试；
- 持久化永久删除审批；
- latest-result human quality gate。

任务状态包括 active、review、waiting_approval、blocked、verified、stopped 等语义。

## 3. 确定性 Job 生命周期

### 接收任务

用户提交任务时，以下内容在同一数据库事务中写入：

1. user message；
2. queued job；
3. `message.accepted` event。

创建 Job 时会锁定并验证 conversation，同时检查是否已经存在 active Job，避免消息/Job 分裂或重复运行。

### 完成任务

完成时以下内容同事务提交：

1. job → completed；
2. 最终 assistant message；
3. `answer.ready` event。

取消或更新的执行状态不会被迟到的旧失败/成功事件覆盖。进度事件带 `job_id`，前端恢复时只绑定当前或最近一次执行。

### 停止与重试

停止是持久化终止状态，不是假 UI。安全重试只允许最近一次失败或停止的执行，并复用原 Job payload、历史任务上下文和素材上下文。

系统不承诺外部推理调用可以在任意 token / 指令点无损恢复，因此没有伪造 pause/resume 语义。

## 4. 多模态任务上下文

上传素材先写对象存储，再登记 Product Store。若数据库登记失败，会清理已经写入的 source / keyframe 对象，减少孤儿对象。

支持：

- image
- video + adaptive keyframe extraction
- audio
- logs / config / structured text
- generic documents / text

用户消息只记录本轮显式选择的附件，但执行 Job payload 会携带**当前任务全部已有素材上下文 + 本轮显式素材**。因此后续追问即使 `asset_ids=[]`，也不会丢失任务早先的截图、录像、音频、日志或配置。

## 5. 永久删除治理

永久删除不是浏览器 `confirm()`。

1. 用户创建 delete approval request；
2. conversation 进入 `waiting_approval` 并锁定突变操作；
3. Owner / Admin approve 或 reject；
4. reject 恢复 `previous_status`；
5. approve 后执行对象存储清理；
6. 对象存储成功后，审批校验 + conversation 数据删除 + `conversation.delete` audit 在一个数据库事务中完成。

如果对象存储删除失败，任务和 approved approval 都保留，可以重试；如果数据库事务失败，approved approval 也不会提前被消费掉。

Local 和 S3-compatible delete 都按幂等语义处理。

## 6. 人工反馈与质量门

`result_feedback` 针对最近一次 assistant 交付保存：

- verdict: correct / partial / incorrect
- evidence usefulness
- human verified
- note

质量门只评估**最新交付**，避免历史错误永久污染后续修正结果：

- 没有交付 → active
- 交付完成 → review
- 最新交付存在 incorrect → blocked
- 最新交付至少一个 `human_verified && correct` 且无 incorrect → verified

该 gate 也会作为后续候选策略演进的输入。人工反馈不会直接修改在线策略。

## 7. 产品指标

`product_events` 用于计算产品级闭环指标，包括：

- task count / active tasks
- first-task completion rate
- average time to first result
- interruption rate
- failure rate
- recovery rate
- continuation rate
- manual intervention rate
- evidence open rate
- result adoption rate
- human verified feedback rate

指标按 conversation / event set 去重，而不是简单按事件条数相除。

## 8. WorldForge Runtime

WorldForge 将动态游戏环境本身作为执行对象。只有一个 **Canonical World State** 代表真实提交路径；候选未来运行在隔离副本中。

核心组件：

- `AdaptivePlanner`：根据 Goal / Belief / World State / Skill / Memory 形成动态候选空间；
- bounded specialists：根据状态提供有界偏置，不能直接提交动作；
- `CounterfactualBrancher`：在多个候选未来中试演动作；
- `ActionSandbox`：提交前检查动作契约和风险预算；
- `StateVerifier`：执行后检查状态不变量、灾难性风险和异常奖励循环；
- Event Store：append-only hash chain、checkpoint、replay、fork；
- Failure-driven evolution：从失败轨迹生成候选更新，经回归与人工质量门约束后才能进入后续运行。

自主性来自受约束的状态循环，不依赖固定 DAG，也不等同于让推理模型自由提交动作。

## 9. Canonical Future Anchor

随机 rollout 可能在高方差游戏里低估当前真实随机状态对应的风险。每个候选动作至少包含一个与 canonical RNG 对齐的 exact future，再用额外扰动未来估计泛化风险。

因此候选评估同时回答：

- 如果现在真的执行，最接近当前真实状态的后果是什么？
- 如果随机性变化，这个选择是否仍稳定？

## 10. Event Sourcing 与 Realtime

Runtime Event Store 用于环境状态、决策、Verifier、Replay / Fork 等运行时轨迹；Product Store 的 `task_events` 是客户工作台实时进度的事实源，两者边界不同。

产品 realtime 通过 durable cursor 读取并 fan-out。WebSocket 使用 subscribe-before-replay、event-id deduplication 和 `after_id` 续传，减少重连时的事件丢失和重复。

## 11. 推理资源边界

`worldforge/providers/` 是服务端推理资源适配层。客户工作台不展示供应商或模型选择器。

推理路由可以根据文本、视觉、音频等输入能力选择可用资源，但以下职责始终属于灵境 / WorldForge：

- workspace authorization
- task lifecycle
- canonical state
- planning and execution control
- sandbox / verifier
- rollback / replan
- approvals
- human quality gate
- completion semantics

因此更换推理资源不等于更换产品控制面或执行语义。
