# 灵境 / WorldForge Architecture

## 1. 系统边界

灵境分成三个明确边界：

1. **Product Control Plane**：身份、工作空间、任务、消息、素材、Job、证据、交付、反馈、审批、指标与审计。
2. **Self-Evolving Harness**：表示、Belief、Memory、Skill、Specialist 拓扑、Planner 融合、反事实搜索预算、风险效用和 mutation policy。
3. **Frozen Runtime Kernel**：canonical state、checkpoint、Sandbox、Verifier、rollback / replan、事件链、sealed evaluation 与 atomic promotion。

推理模型只是可替换资源。它不能绕过 workspace 权限，不能直接写 canonical state，不能修改 Verifier，也不能让自己的 Harness candidate 直接上线。

```text
Browser Workspace
    ↓
FastAPI / Auth / Workspace Guard
    ↓
Product Store ───────── Object Storage
    ↓                         ↓
Durable Job Queue ───── Asset Materialization
    ↓
Worker / ProductAnalyzer
    ↓
Self-Evolving WorldForge Harness
    ↓
Frozen Execution Kernel
    ↓
Result / Evidence / Deliverables
    ↓
Human Feedback / Audit / Product Events
```

## 2. 产品控制面

持久化产品状态包括：

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

所有资源都按 workspace membership 在服务端校验。前端按钮可见性不是权限边界。

任务生命周期支持搜索、重命名、置顶、归档 / 恢复、负责人交接、深链接、真实停止、安全重试、永久删除审批和 latest-result human quality gate。

任务接收时，user message、queued job 与 `message.accepted` 同事务提交；完成时，job completed、assistant answer 与 `answer.ready` 同事务提交。迟到的旧执行事件不能覆盖更新状态。

## 3. 多模态任务上下文

图片、视频、音频、日志、配置和文档统一进入 workspace 资产生命周期。Job payload 会携带当前任务已存在的素材上下文，因此后续追问不会因为本轮 `asset_ids=[]` 丢失之前的证据。

对象存储写入成功但数据库登记失败时会清理已写入对象，减少孤儿资源。

## 4. Frozen Kernel

`worldforge/runtime/engine.py` 是冻结执行内核。它只负责不可委托给 Harness candidate 的职责：

- canonical world-state ownership；
- checkpoint / restore；
- Sandbox；
- invariant verification；
- rollback / replan；
- append-only Runtime event chain；
- bounded inner-policy update；
- 真实动作提交与完成语义。

Frozen Kernel 不包含“低血量应该 heal”“某标签应该 farm”“某场景应该启动某 Specialist”之类任务策略。

环境上报 anomaly 时，Verifier 统一记录为 `finding:<name>`。Finding 是研发证据；只有关键不变量、灾难性生存风险和 terminal failure 等 unsafe state 才触发安全失败 / 恢复语义。

## 5. Evolvable Harness Genome

当前 active Harness 由 `HarnessGenome` 表示并持久化。可进化面包括：

- feature representation / scales / caps；
- belief uncertainty；
- memory feature weights / similarity temperature / recency；
- Skill gate / action bias / reliability；
- Specialist topology / gate / confidence / action feature weights；
- Planner fusion / repeat friction / epistemic action coefficients；
- counterfactual width / horizon / rollout allocation；
- branch risk utility；
- mutation operator logits / sigma / temperature / exploration。

Bootstrap prior 只存在 `default_harness_genome.json`。Python Runtime 是解释器，不是任务策略表。

动态标签统一变成 `tag:<name>` 特征；Runtime 不需要预先知道 `economy`、`boss` 或任何具体项目标签。

## 6. Runtime Phenotype

每个 decision step：

1. Frozen Kernel 建 checkpoint；
2. active Genome 将当前 state 映射成 feature phenotype；
3. core / dynamic Specialists 通过平滑 gate 激活；
4. Skill、Memory、Policy prior、Specialist bias 汇合到 Genome-interpreting Planner；
5. CounterfactualBrancher 在调用方资源上限内，由 Genome 动态分配 width / horizon / rollouts；
6. clone-world rollout 由 Verifier 独立检查；
7. Sandbox 在 canonical commit 前检查；
8. 只有 Kernel 能向真实环境提交动作；
9. post-state Verifier 决定 continue / rollback / replan / finding。

候选未来只能操作 clone，不持有 canonical state 写权限。

## 7. Harness Self-Evolution

产品入口是 `SelfEvolvingWorldForgeEngine`。出现失败、finding、恢复或 invalid action 时，运行轨迹会形成 Harness evolution evidence。

当前进化链：

```text
Verified Trace
  → policy-agnostic reflection
  → WHERE × WHY semantic cell
  → true antithetic mutation pairs
  → behavior-plateau detection / sigma escalation
  → stable train elites
  → topology / gate / skill / memory / parameter refinement
  → minimum-effective-edit trust region
  → freeze search trajectory
  → sealed held-out evaluation
  → Pareto / semantic-QD credit
  → atomic generation promotion or reject
```

### Proposal 与 credit 分离

候选生成、elite selection、refinement、trust-region 和 behavior-boundary bisection只使用 train cases。整个搜索轨迹冻结后才打开 held-out。

候选 Harness 无法修改：

- Verifier；
- game-R&D evaluator；
- train / held-out split；
- paired-bootstrap credit；
- safety / quality / efficiency floor；
- promotion transaction。

因此 candidate 不能通过“把裁判改松”取得晋升。

## 8. Game R&D Evaluator

`game_harness_evaluator.py` 把游戏研发与单纯 game-playing 区分开：

- 胜利、进度、健康、环境 score 贡献 task quality；
- hidden mechanic observation / anomaly finding 贡献 diagnostic coverage；
- rollback / replan / critical invariant 贡献 unsafe penalty；
- counterfactual operations 显式计入 efficiency。

Finding 与 unsafe execution 分离，避免“发现漏洞反而被 benchmark 惩罚”。

## 9. 持续 Memory 与 Skill

Memory 使用 Genome 定义的连续相似度核，不使用 `low / mid / high` 人工状态桶。Genome 可以改变检索特征、距离权重、温度、recency decay 和 success bonus。

Skill 的名称/描述是可审计元数据；真正 gate、action bias、reliability 来自 active Genome，并可由 Harness evolution 修改。

## 10. Semantic QD 与代际谱系

Archive 使用 `WHERE × WHY` cell 保存互补 elite，而不是只维护一个全局 champion。候选同时比较 objective、safety、efficiency 与 novelty。

每个 Genome 保存：

- `genome_id`
- `generation`
- `parent_ids`
- `origin`

Promotion 采用原子替换：被持久化的对象就是 held-out 评估过的同一个 Genome，不允许评估之后再偷偷修改参数。

## 11. 可复现 promotion gate

仓库提供独立命令：

```bash
python scripts/harness_evolution_benchmark.py
```

协议 `sealed-heldout-game-harness-2026-08` 从 bootstrap Genome 启动，train seeds 为 11 / 23，held-out seeds 为 37 / 51。当前已验证的独立进程结果：36 个候选中 4 个通过门禁，generation 1 晋升到 generation 3；train objective gain `+0.004712`，sealed held-out gain `+0.000559`，paired-bootstrap lower bound `0.000000`。

这些数字证明 promotion mechanism 可工作，不代表跨项目通用 SOTA。

## 12. Event Sourcing 与 Realtime

Runtime Event Store 保存环境状态、decision、Verifier、checkpoint、Harness evolution / promotion 等轨迹；Product Store 的 `task_events` 是客户工作台进度事实源。二者边界不同。

产品 realtime 使用 durable cursor + fan-out。WebSocket 采用 subscribe-before-replay、event-id deduplication 与 `after_id` 续传。

## 13. 推理资源边界

`worldforge/providers/` 是服务端推理资源适配层。客户工作台不展示供应商或内部模型选择器。

更换推理资源不会改变以下灵境职责：workspace authorization、task lifecycle、canonical state、Harness generation、Sandbox / Verifier、rollback / replan、approval、human quality gate 与 completion semantics。
