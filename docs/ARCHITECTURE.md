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

任务接收时，user message、queued job 与 `message.accepted` 同事务提交；完成时，job completed、assistant answer 与 `answer.ready` 同事务提交。Worker 通过可续约 lease、heartbeat 与每次领取唯一的 fencing token 执行任务；过期 lease 自动回队列，旧 attempt 无法覆盖新 attempt 或停止后的终态。

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

协议 `runtime-trace-sealed-heldout-game-harness-2026-08` 从真实 Runtime timeout 轨迹提取 WHERE × WHY evidence，并从 bootstrap Genome 启动；train seeds 为 11 / 23，held-out seeds 为 37 / 51。当前已验证的独立进程结果：52 个候选中 6 个通过门禁，generation 1 晋升到 generation 4；train objective gain `+0.004712`，sealed held-out gain `+0.044598`，paired-bootstrap lower bound `0.000000`。`docs/benchmark-results.json` 是 CI 对比的机器可校验摘要。

这些数字证明 promotion mechanism 可工作，不代表跨项目通用 SOTA。

## 12. Event Sourcing 与 Realtime

Runtime Event Store 保存环境状态、decision、Verifier、checkpoint、Harness evolution / promotion 等轨迹；Product Store 的 `task_events` 是客户工作台进度事实源。二者边界不同。

产品 realtime 使用 durable cursor + fan-out。WebSocket 采用 subscribe-before-replay、event-id deduplication 与 `after_id` 续传。

## 13. 推理资源边界

`worldforge/providers/` 是服务端推理资源适配层。客户工作台不展示供应商或内部模型选择器。

更换推理资源不会改变以下灵境职责：workspace authorization、task lifecycle、canonical state、Harness generation、Sandbox / Verifier、rollback / replan、approval、human quality gate 与 completion semantics。

## 14. 证据与结论可信度边界

用户上传内容标为 `observed`，内部 WorldForge 场景标为 `synthetic`，只有显式加载目标游戏
Build 的 `GameExecutionAdapter` 才能产生 `reproduced` 证据。`reproduced` 只说明证据来自真实
运行环境；报告问题是否复现仍要求 reproduction assertions 全部通过。

无可用推理模型时，ProductAnalyzer 返回 `analysis_mode=demo`；即使有模型，当前分析链仍返回
`claim_status=hypothesis_only` 与 `verification_status=not_verified`。内部场景和模型文本都不能把
结论自动升级为真实游戏已验证。前端在结果和每条证据旁直接显示这一来源边界；人工将待验证
假设标为已验证时，必须填写真实游戏 Build、验证条件与结果说明，服务端同样强制此约束。

## 15. 生产持久化边界

Product Control Plane 使用 PostgreSQL，资产使用 S3-compatible Object Storage。单机 Compose
通过共享 `runtime_data` volume 让 API 与 Worker 读取同一 Runtime / Harness 文件事实源；多主机
扩展时必须把该文件层替换为共享、带并发控制的持久化 Runtime Store，不能把本地容器文件系统
当作分布式事实源。
