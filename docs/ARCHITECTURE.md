# 灵境 / WorldForge Architecture

## 1. 系统边界

灵境保持三层权威边界：

1. **Product Control Plane**：身份、workspace、任务、消息、素材、Job、Project binding、长期记忆治理、证据、审批、指标与审计；
2. **Self-Evolving Harness**：表示、Belief、runtime Memory/Skill、Specialist 拓扑、Planner 融合、反事实搜索预算、风险效用和 mutation policy；
3. **Frozen Runtime Kernel**：canonical state、checkpoint、Sandbox、Verifier、rollback/replan、事件链、sealed evaluation 与 atomic promotion。

ContextOS 位于 ProductAnalyzer 的**模型上下文编译边界**：它可以选择/压缩历史、读取已经授权的 Project Memory packet、规划 multimodal evidence，但不能获得 canonical state 写权限。GameAdapter 是 Frozen Kernel 管辖的外部 actuator/evidence boundary，同样不能成为 verifier 或 canonical writer。

```text
Browser Workspace
    ↓
FastAPI / Auth / Workspace Guard
    ↓
Product Store ───────────── Object Storage
    │                              │
    ├─ Durable Job Queue           └─ Raw Assets / Derivatives
    ├─ Project / Memory Governance
    └─ Immutable message.accepted Outbox
              ↓
Worker / ProductAnalyzer
    ↓
ContextOS Compiler
    ├─ TaskState
    ├─ authorized Project Memory packet
    ├─ multimodal retrieval / evidence control
    └─ provider-aware context budget
              ↓
Self-Evolving WorldForge Harness
              ↓
Frozen Execution Kernel
    ├─ canonical state / Verifier
    └─ governed GameAdapter ticket boundary
              ↓
Result / Evidence / Deliverables
              ↓
Human Feedback / Memory Approval / Audit / Product Events
```

推理模型、retrieval backend、memory suggestion 和外部 engine 都只是受控资源。它们不能绕过 workspace 权限，不能直接写 canonical state，不能修改 Verifier，也不能让自己的输出自动成为当前项目事实。

## 2. 产品控制面与持久化状态

Product Store / Project Memory 相关持久化状态包括：

- users / workspaces / memberships / workspace_invites；
- conversations / messages / assets；
- jobs / task_events / audit_logs / product_events；
- approval_requests / result_feedback；
- projects / project_conversations；
- context memory items / heads / relations / usage；
- context memory proposals；
- context memory ingestion receipts；
- GameAdapter ticket replay rows。

所有客户资源按 workspace membership 在服务端重新授权，前端按钮可见性不是权限边界。Viewer 保持只读；proposal approval、Project binding、memory revision 等写操作均由服务端 actor/workspace 检查决定。

任务生命周期支持搜索、重命名、置顶、归档/恢复、负责人交接、深链接、停止、安全重试、永久删除审批和 latest-result human quality gate。

用户消息接收时，user message、queued analysis job 与 immutable `message.accepted` event 在**同一事务**提交。新事件携带 v2 ingestion locator（job/actor/project/scope locator），因此 durable memory ingestion 不要求 analysis job row 永久保留。历史事件仍保留兼容 fallback。

完成时，job completed、assistant answer 与 `answer.ready` 同事务提交；迟到的旧执行事件不能覆盖更新状态。

## 3. Queue-safe snapshot 与 durable memory ingestion

Analysis job 不复制完整长历史或 governed memory body。队列只冻结：

- authoritative history boundary；
- actor/workspace identity；
- Project / scope locator；
- memory revision locators；
- 当前任务需要的 asset ids / request metadata。

执行时重新验证 frozen message prefix、Project binding、scope 和 memory revision 是否仍有效。被删除、superseded、disputed、retracted 或 expired 的 revision 不会因为 job 曾经排队就继续获得权限，也不会静默升级为新 head。

Memory ingestion 与 analysis execution 解耦：

```text
committed user message
  + immutable message.accepted
        ↓
receipt claim / lease / retry
        ↓
pending memory proposal
        ↓
explicit user governance
        ↓
authoritative Project Memory
```

Receipt 以 event id 做 delivery idempotency，proposal 再以 source fingerprint 做第二层幂等。Cancellation/provider failure/analysis worker failure 不会撤销已经提交的 ingestion intent，但 ingestion 只能生成 pending proposal，不能直接写 authoritative memory。

## 4. Project Memory 权威模型

Project ↔ Conversation 绑定必须显式存在；系统不会根据文本相似度猜 Project。

Project Memory 是 **scoped、versioned、revisable materialized view**，不是 canonical game state。Memory revision 支持：

- build / branch / commit / environment scope；
- CAS-style head promotion；
- provenance；
- valid-time / TTL；
- dispute / retraction；
- relations；
- usage audit。

Inference 使用经过 scope shadowing 的 active heads；governance UI 可以查看完整 scope/history。Pending proposal 和 Memory Identity suggestion 都是 advisory。Identity Resolver precision-first，允许 abstain；suggestion 必须经过“查看建议 → 显式采用 key → 独立批准 proposal”的人工链路。

## 5. ContextOS long-horizon compiler

ContextCompiler 不再依赖 inference-time `history[-8:]` 保持连续性。它从历史中构造 bounded TaskState，并结合 bounded old-history retrieval：

- 当前 goal；
- constraints；
- decisions；
- explicitly confirmed facts；
- open/closed questions；
- build/version refs；
- superseding state；
- conditional-premise safety。

`ContextBudgetBroker` 将 Verification / TaskState / ProjectMemory / EvidenceControl 合并为 bounded Context Kernel Pack，同时保留少量最新/检索历史，避免 legacy provider last-N 再次丢掉 long-range state。

Character budget 是 deterministic tokenizer-independent compatibility floor；`ProviderAwareContextBudgetBroker` 与 provider native count 进一步执行 token-aware packing / last-mile limit verification。

ContextOS 产生的是**模型上下文**，不是新的用户消息或项目真相。冲突时优先级保持：当前 Verification / 原始证据 > TaskState > Project Memory。

## 6. 多模态证据与 retrieval 边界

图片、视频、音频、日志、配置和文档统一进入 workspace asset lifecycle。原件始终保留可追溯 provenance，derivative/index 是可丢弃加速层。

Multimodal compiler / retriever 支持：

- full text/log access 与 bounded excerpts；
- timestamp-safe keyframes 与 on-demand scene-aware frames；
- hierarchical video segments；
- acoustic windows / audio-from-video；
- temporal hint escalation；
- optional semantic workers；
- Claim → Evidence graph。

Project/build/branch/commit/environment scope 在昂贵 semantic retrieval **之前** fail-safe 过滤；wrong-build/scope-ineligible asset 不能被发送到 worker、不能消费 multimodal budget，也不能从 worker result 回流。

Retrieval/memory 只提供 prior/context。Verification-scope confidence cap 防止“相似素材命中”自动升级成当前项目验证事实。

## 7. Provider-aware inference boundary

`worldforge/providers/` 是服务端推理适配层。客户 workspace 不依赖某一家 provider，也不让 provider route 成为授权边界。

当前 token safety 分两层：

1. deterministic multilingual/CJK/code-aware estimate + operator-declared context profile；
2. provider native last-mile count（可用且可信时）。

Gemini 使用 model metadata + `models.countTokens`；Claude 使用 model metadata + `/v1/messages/count_tokens`。Metadata cold start 使用 singleflight，并区分 success cache 与 failure negative cache。

OpenAI-compatible/custom gateway 不假设存在官方统一 token-count API；只有 operator 显式配置 count endpoint 且标记 trusted 时才启用 exact hook。Native count 请求失败时 fail-open 到 deterministic budget；只有成功 exact count 明确超出 safe input limit 时才在 generation 前阻断。

Request telemetry 使用 task-local `ContextVar` 隔离，并记录 provider/model selection、estimate/exact delta、ratio、native-count RTT 和 media accounting（可用时）。

## 8. Frozen Kernel

`worldforge/runtime/engine.py` 是冻结执行内核。它只负责不能委托给 Harness candidate 的职责：

- canonical world-state ownership；
- checkpoint / restore；
- Sandbox；
- invariant verification；
- rollback / replan；
- append-only Runtime event chain；
- bounded inner-policy update；
- 真实动作提交与完成语义。

Frozen Kernel 不包含“低血量应该 heal”“某标签应该 farm”“某场景应该启动某 Specialist”这类任务策略。

环境上报 anomaly 时，Verifier 统一记录 finding。Finding 是研发证据；只有关键不变量、灾难性生存风险和 terminal failure 等 unsafe state 才触发安全失败/恢复语义。

## 9. GameAdapter 外部引擎边界

`lingjing-game-adapter-v1` 允许 Unity、Unreal 或 custom bridge 执行**显式授权**的外部 action，但 adapter 是 actuator + evidence source，不是 canonical-state authority。

每次 dispatch 都需要短时 HMAC ticket，ticket 绑定：

- adapter id；
- action id；
- exact canonical action payload；
- dry-run mode；
- ordered evidence request；
- project/build scope digest；
- expiry 与 one-use nonce。

Ticket 在 dispatch **之前**消费。`SqlGameAdapterReplayStore` 配合 Alembic `20260909_0007`，以 ticket primary key + unique nonce 做跨 kernel process 原子 replay protection；replay store 不可用时 fail-closed，不执行外部 action。

远端 evidence provenance 会被 sanitize；adapter 不能伪造 `source_type=verifier` 或 `verified=true`。Gateway 输出始终保持：

```text
canonical_write_allowed = false
verifier_status = not-run
evidence_class = external-engine-observation-unverified
```

真实 engine observation 只有经过后续独立 Frozen Kernel verification 才可能成为权威验证结果。仓库包含 protocol/reference client/conformance，不声称已经执行真实 Unity/Unreal 项目。

## 10. Evolvable Harness Genome

当前 active Harness 由 `HarnessGenome` 表示并持久化。可进化面包括：

- feature representation / scales / caps；
- belief uncertainty；
- runtime memory feature weights / similarity temperature / recency；
- Skill gate / action bias / reliability；
- Specialist topology / gate / confidence / action feature weights；
- Planner fusion / repeat friction / epistemic action coefficients；
- counterfactual width / horizon / rollout allocation；
- branch risk utility；
- mutation operator logits / sigma / temperature / exploration。

Bootstrap prior 只存在 `default_harness_genome.json`。Python Runtime 是解释器，不是任务策略表。动态标签统一变成 `tag:<name>` 特征，Runtime 不预先知道任何具体项目标签。

## 11. Runtime Phenotype

每个 decision step：

1. Frozen Kernel 建 checkpoint；
2. active Genome 将 state 映射成 feature phenotype；
3. core/dynamic Specialists 通过平滑 gate 激活；
4. Skill、runtime Memory、Policy prior、Specialist bias 汇合到 Genome-interpreting Planner；
5. CounterfactualBrancher 在资源上限内分配 width/horizon/rollouts；
6. clone-world rollout 由 Verifier 独立检查；
7. Sandbox 在 canonical commit 前检查；
8. 只有 Kernel 能向真实环境提交动作；
9. post-state Verifier 决定 continue / rollback / replan / finding。

候选未来只能操作 clone，不持有 canonical state 写权限。

## 12. Harness Self-Evolution

产品入口是 `SelfEvolvingWorldForgeEngine`。失败、finding、恢复或 invalid action 会形成 evolution evidence。

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

Candidate generation、elite selection、refinement、trust-region 和 behavior-boundary bisection 只使用 train cases；搜索轨迹冻结后才打开 held-out。Candidate 无法修改 Verifier、game-R&D evaluator、train/held-out split、paired-bootstrap credit 或 promotion transaction。

## 13. Game R&D Evaluator

`game_harness_evaluator.py` 将研发任务与单纯 game-playing 分开：

- success/progress/health/environment score → task quality；
- hidden mechanic observation/anomaly finding → diagnostic coverage；
- rollback/replan/critical invariant → unsafe penalty；
- counterfactual operations → efficiency。

Finding 与 unsafe execution 分离，避免“发现漏洞反而被 benchmark 惩罚”。

## 14. Semantic QD 与代际谱系

Archive 使用 `WHERE × WHY` cell 保存互补 elite，而不是只维护一个全局 champion。Candidate 同时比较 objective、safety、efficiency 与 novelty。

每个 Genome 保存 `genome_id / generation / parent_ids / origin`。Promotion 原子替换：被持久化的对象就是 held-out 被评估的同一个 Genome，不允许评估后偷偷改参数。

## 15. 可复现 promotion gate

仓库提供：

```bash
python scripts/harness_evolution_benchmark.py
```

协议 `sealed-heldout-game-harness-2026-08` 从 bootstrap Genome 启动，train seeds 为 11/23，held-out seeds 为 37/51。当前独立进程已验证 train objective gain `+0.004712`、sealed held-out gain `+0.000559`、paired-bootstrap lower bound `0.000000`。

这些数字证明 promotion mechanism 可工作，不代表跨项目通用 SOTA。

## 16. Event Sourcing 与 Realtime

Runtime Event Store 保存环境 state、decision、Verifier、checkpoint、Harness evolution/promotion 等轨迹；Product Store 的 `task_events` 是客户 workspace 进度/accepted-message outbox 的事实源。二者边界不同。

产品 realtime 使用 durable cursor + fan-out。WebSocket 采用 subscribe-before-replay、event-id deduplication 与 `after_id` 续传。

## 17. Evidence 与完成边界

CI 可以证明 repository mechanism/correctness contracts，但不能制造真实外部证据。真实 held-out model QA、multimodal retrieval quality、GPU latency/cost、PostgreSQL production-like throughput 和 Unity/Unreal project execution 必须在对应真实环境单独运行。

所有外部实验统一按 [`EXTERNAL_EVIDENCE_RUNBOOK.md`](EXTERNAL_EVIDENCE_RUNBOOK.md) 冻结代码/data/deployment identity、保存 raw artifacts，并严格遵守 evidence label。Synthetic smoke 不得被升级成 SOTA、SLA 或真实项目验证声明。
