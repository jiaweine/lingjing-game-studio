# Benchmarking

## 1. 目的

仓库内 benchmark 用于回答四类不同问题：

1. **Runtime / 产品是否仍然可靠**：执行、验证、恢复、权限、队列和产品生命周期有没有回归；
2. **Harness 是否真的还能自进化**：不是只会生成 candidate，而是能在独立 held-out credit 下晋升新的 generation；
3. **长期记忆是否正确且可治理**：更新、版本隔离、撤回、未批准提议、队列快照、消息摄取和重启恢复是否满足明确不变量；
4. **Memory Identity 是否足够安全**：新 proposal 是否能在不错误合并不同实体/属性的前提下，识别可能属于同一个 `memory_key` 的 revision。

这四类分数不能互相替代。benchmark 是工程验证，不是客户工作台营销榜单。没有 controlled protocol，不声称“性能超过某外部产品”。

## 2. Harness promotion benchmark

永久门禁命令：

```bash
python scripts/harness_evolution_benchmark.py
```

协议 ID：`sealed-heldout-game-harness-2026-08`。

当前固定实验设置：

- 从 bootstrap HarnessGenome 启动；
- population = 8；
- train seeds = 11 / 23；
- held-out seeds = 37 / 51；
- branch width / horizon / rollouts 评估上限 = 2 / 2 / 2；
- paired-bootstrap samples = 128；
- held-out 不参与 candidate generation、elite selection、refinement、trust-region 或 minimum-effective-edit 搜索。

Promotion 必须同时满足：

- train objective 达到最小正增益；
- sealed held-out gain 不为负；
- paired-bootstrap lower bound 不为负；
- quality 不回退；
- safety 不回退；
- efficiency 在冻结容忍范围内；
- operations 不超过冻结资源上限。

### 当前独立进程的核心 sealed credit

```text
train objective gain         +0.004712
sealed held-out gain         +0.000559
paired-bootstrap LCB           0.000000
held-out quality               0.612886
held-out safety                0.966518
held-out efficiency            0.730917
held-out operations               23.25
```

candidate 数、accepted candidate 数、promoted generation 和具体 winning lineage 属于当前搜索轨迹，会随着 Harness 搜索实现演进；CI workflow 日志是这些运行细节的事实来源，不把它们固定成长期文档常量。

这组 sealed credit 只证明：从干净进程、bootstrap Genome 出发，搜索器能够产生被 sealed judge 接受的新 Harness。增益很小，因此不能据此宣称通用 SOTA 或大幅性能领先。

## 3. 为什么 held-out 必须 sealed

如果搜索器能反复读取 held-out 再继续改 candidate，最终分数只是对测试集的优化，不是独立 credit。

当前流程严格分成：

```text
train-only proposal/search
    ↓
freeze search trajectory
    ↓
held-out evaluation
    ↓
paired-bootstrap credit
    ↓
promotion / rejection
```

被 promotion 的 Genome 就是被 held-out 评估的同一个对象；评估之后不会再更新 mutation policy 或其他字段。

## 4. Game R&D evaluator

Harness benchmark 不把“击败敌人”当作唯一目标。评估同时观察：

- task quality：success / progress / health / environment score；
- diagnostic coverage：隐藏机制被观察、环境 anomaly / finding 被发现；
- safety：critical invariant、rollback / replan 风险；
- efficiency：counterfactual decision operations。

Finding 和 unsafe execution 分开。研发 Harness 发现漏洞应该获得诊断价值，而不是因为出现 anomaly 字样就被当作安全失败。

## 5. Lingjing-MemoryBench：长期记忆 correctness floor

独立命令：

```bash
python scripts/memory_benchmark.py
```

`Lingjing-MemoryBench v1` 当前不调用外部模型，也不使用 LLM judge。它先验证长期记忆的确定性系统语义；如果这些不变量都无法满足，再高的问答准确率也没有产品意义。

当前十个 competency：

| Competency | 必须满足的反例约束 |
|---|---|
| cross-conversation recall | 在 Conversation A 明确批准的项目记忆，Conversation B 绑定同一 Project 后可以检索 |
| update tracking | 新 revision 成为 head 后，旧 revision 不得继续出现在正常检索结果中 |
| scoped version isolation | build 1.4.7 / 2.0.0 的同 key 事实不能互相污染；无版本身份时不能猜一个版本 |
| conflict abstention | 当前素材给出冲突 build 身份时，只允许 general-scope 记忆，不允许随机选择某个版本事实 |
| selective forgetting | 撤回一个 memory head 后它必须消失，同时其他无关 active memory 继续可用 |
| pending memory isolation | pending proposal 不是 truth；人工批准前不能进入 Project Memory 检索 |
| provenance integrity | proposal 批准后必须保留 `user_confirmed + proposal/message` 来源，批准动作不能改写用户原文再冒充确认 |
| queued snapshot revocation | 排队时命中的 revision 后来被撤回时，旧 job 必须 invalidated，不能继续用旧正文，也不能自动换新 head |
| restart persistence | 用同一数据库重新构造全新 Store 后，active Project Memory 必须继续可检索 |
| ingestion outbox cancellation | user message / analysis job / `message.accepted` 已提交后，即使 analysis job 在 worker 启动前被取消并发生进程重启，也必须 exactly-once 恢复一个 pending proposal；replay 不得重复 |

pytest 对这十项要求全部为 `1.0`，总分必须 `1.0`。CI 还会单独运行 `scripts/memory_benchmark.py`，把每个 competency 的机器可读结果留在 workflow 日志中。

当前独立进程已经通过 **10/10**，包括 `ingestion_outbox_cancellation = 1.0`。这里的 **10/10 只表示 correctness floor 全部满足，不代表 SOTA memory accuracy**。

### Durable ingestion 与 execution 解耦

Project Memory proposal 不再依赖 analysis worker 是否启动：

```text
committed user message
    + analysis job
    + message.accepted immutable task event
                  ↓
       durable ingestion consumer
       receipt / lease / retry
                  ↓
       pending proposal only
                  ↓
       explicit human approval
                  ↓
       authoritative Project Memory
```

关键约束：

- `message.accepted` 与 user message / job 在同一数据库事务提交，因此不会出现“消息成功但 ingestion intent 不存在”；
- analysis job 的 cancel / retry / provider failure 不撤销已经提交的 ingestion intent；
- receipt 使用 event id 做 delivery 幂等，proposal 自身再使用 source fingerprint 做第二层幂等；
- stale processing lease 可恢复，failed receipt 有退避；
- API 启动恢复旧 backlog，空闲期也周期性恢复，因此不依赖下一条用户消息才能继续消费；
- unbound message 被 terminally ignored，之后不会根据相似文本或后来绑定关系猜一个 Project；
- ingestion 只能生成 reviewable pending proposal，不能直接写 authoritative memory。

下一阶段 MemoryBench 会继续增加：

- 100+ / 500+ turn 的状态变化与 premise awareness；
- contradiction / dispute / retract / delete 组合；
- 多 worker / PostgreSQL 并发与高 backlog 恢复；
- multimodal provenance 与跨素材时间线；
- provider-aware token / latency / cost；
- 模型问答层的 recall、update、abstention 和 workflow-learning accuracy。

## 6. Lingjing-IdentityBench：revision identity safety floor

独立命令：

```bash
python scripts/memory_identity_benchmark.py
```

`Lingjing-IdentityBench v1` 单独验证 proposal → existing `memory_key` 的 identity suggestion。它只评估**建议器**，不允许 benchmark 通过后直接获得 memory 写权限。

当前设计故意把 false merge 看得比 false split 更危险：

- **false merge**：把本来不同的实体或属性错误归到同一 `memory_key`，会污染 revision chain，因此必须为 `0`；
- **false split**：本来属于同一 identity 却 abstain / 新建 key，主要增加人工治理成本，风险低于错误合并；
- **abstention**：是合法安全输出。没有足够证据时宁可不建议，也不为了 recall 强行归链。

当前 deterministic v1 case set 共 10 个 case，独立 workflow 当前结果：

```text
cases                         10
positive cases                 4
negative cases                 6
recommendation precision   1.000
positive recall             1.000
false merge rate            0.000
false split rate            0.000
abstention rate             0.600
```

门槛：

- `false_merge_rate == 0`；
- recommendation precision `== 1.0`；
- positive recall `>= 0.75`。

这套 v1 case 覆盖同一属性数值更新、跨 build 同 identity、属性差异、命名实体冲突、候选接近时 abstain、kind mismatch 和 retracted head 排除等安全边界。当前 resolver 只输出 `candidate_memory_keys + scores + reasons + margin`；API 是 read-only shadow path，UI 也不会自动填 key。用户必须先显式“采用建议 key”，再单独执行 proposal 批准。

**10/10、precision 1.0 和 false-merge 0 只代表当前 deterministic safety floor，不代表真实项目分布上的 SOTA。** 下一阶段要扩展：

- 数百 / 数千个 hard-negative 与 paraphrase case；
- cooldown / duration / amount / rate 等近邻属性；
- 同前缀不同实体、别名、跨语言中英混合；
- build / branch / environment 变化与 stale/disputed/retracted 组合；
- risk-coverage / abstention calibration；
- 与强 lexical、embedding、reranker / memory baseline 在相同 budget 下比较。

只有在这些 controlled protocol 下与强基线比较后，才有资格讨论 game-R&D memory SOTA。

## 7. Runtime / 产品 / deployment 回归

除了 Harness promotion、MemoryBench 与 IdentityBench，CI 继续运行：

- Python compile；
- 完整 pytest（包含 MemoryBench / Identity safety / outbox recovery regression）；
- 从空 SQLite 执行真实 `alembic upgrade head` 的 migration-chain regression，并验证 memory ingestion receipt schema 与 outbox event index；
- standalone `Memory correctness benchmark`；
- 独立 `Memory Identity Benchmark` workflow；
- JavaScript syntax（`app.js` + `memory_panel.js` + `memory_identity_panel.js`）；
- Backend product E2E；
- Browser product E2E；
- **Memory governance browser E2E**：显式 Project 绑定、proposal 审批/拒绝、revision/state/history、workspace role 重新授权，以及 identity suggestion 可见 / 不自动填 / 显式采用 / viewer 只读；
- README / repository consistency；
- GitHub README 真实浏览器图片加载与产品首页渲染检查。

其中：

- Browser product E2E 证明主产品交互闭环没有被算法改造破坏；
- Memory governance browser E2E 证明用户真的可以完成长期记忆治理，并验证 identity suggestion 不会越过人工动作；
- standalone Harness benchmark 证明 Harness generation 机制在干净进程中仍可成功；
- MemoryBench 证明项目长期记忆的版本、治理、撤回、持久化和 ingestion cancellation 基本语义没有回归；
- IdentityBench 证明当前 deterministic case set 下建议器满足 false-merge safety floor；
- migration-chain regression 证明生产 schema migration 确实包含 proposal / receipt / outbox recovery 所需结构，而不是只依赖 dev/test `create_all`。

这些证据必须分别报告，不能揉成一个“综合性能分”。

## 8. 外部比较规则

如果与任何 Agent / Harness / workflow / memory 系统比较，至少冻结：

1. 同一模型 / policy checkpoint；
2. 同一系统提示与可用上下文；
3. 同一环境版本；
4. 同一 observation；
5. 同一 action / tool schema；
6. 同一权限；
7. 同一 token / compute / tool budget；
8. 同一最大环境步数；
9. 同一 seeds；
10. 同一 timeout / retry policy；
11. 同一评分代码；
12. 同一 train / held-out 隔离协议；
13. 对 memory 比较额外冻结相同的 write policy、update policy、retention policy 和删除语义。

Pi、DeerFlow、DeepSeek Agent 生态等可以作为 Harness engineering / integration reference，但不能在不同底座、不同预算或不同环境下拿 README 数字直接横向宣称性能领先。

## 9. 产品指标不是 Runtime / Memory Benchmark

产品层另外通过 `product_events` 观察真实任务闭环，例如首次任务完成率、首次交付耗时、中断率、失败率、恢复率、继续执行率、人工介入率、证据打开率、结果采纳率和人工验证反馈率。

这些指标回答“产品是否帮助研发任务完成”，不能和 shadow-arena Harness objective、MemoryBench correctness score 或 IdentityBench safety score 混成同一套分数。
