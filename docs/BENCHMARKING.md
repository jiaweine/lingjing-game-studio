# Benchmarking

## 1. 目的与证据分层

Lingjing 仓库内 benchmark 不是一张“综合性能榜”。不同协议回答不同问题，证据等级不能互相替代：

1. **Runtime / 产品回归**：执行、验证、恢复、权限、队列和产品生命周期是否仍可靠；
2. **Harness 自进化**：search → sealed held-out credit → promotion 是否仍然成立；
3. **Project Memory correctness**：版本、scope、撤回、proposal、queue snapshot、durable ingestion 等治理不变量是否正确；
4. **Memory Identity safety**：revision identity suggestion 是否能在 precision-first / 可 abstain 的前提下避免错误合并；
5. **ContextOS / multimodal correctness**：10k/50k 长历史、文本/图片/视频/音频、scope/provenance 和 contamination gate 是否正确；
6. **模型 / live retrieval / engine / production-like load 的外部证据**：只有真实 held-out、真实 provider/backend/数据库/游戏引擎环境才能产生，CI smoke 不能替代。

没有 controlled protocol，不声称“性能超过某外部产品”。Synthetic / deterministic 分数只作为 correctness、safety 或 protocol floor。

真实外部实验统一按 [`EXTERNAL_EVIDENCE_RUNBOOK.md`](./EXTERNAL_EVIDENCE_RUNBOOK.md) 执行，先冻结代码、数据、部署身份和证据标签，再讨论任何质量、吞吐、延迟或成本结论。

## 2. Harness promotion benchmark

永久门禁：

```bash
python scripts/harness_evolution_benchmark.py
```

协议 ID：`sealed-heldout-game-harness-2026-08`。

固定设置包括 bootstrap HarnessGenome、population 8、train seeds 11/23、held-out seeds 37/51、branch width / horizon / rollouts 上限 2/2/2，以及 128 次 paired bootstrap。Held-out 不参与 candidate generation、elite selection、refinement、trust-region 或 minimum-effective-edit 搜索。

Promotion 必须同时满足 train objective 正增益、sealed held-out 不回退、**paired-bootstrap LCB** 不为负，以及 quality / safety / efficiency / operations 的冻结门槛。

当前独立进程的核心 sealed credit：

```text
train objective gain         +0.004712
sealed held-out gain         +0.000559
paired-bootstrap LCB           0.000000
held-out quality               0.612886
held-out safety                0.966518
held-out efficiency            0.730917
held-out operations               23.25
```

这只证明 Harness generation / promotion 机制在冻结协议下成立；增益很小，不能据此宣称通用 SOTA。

## 3. Held-out 为什么必须 sealed

搜索器如果在看过 held-out 后继续修改 candidate，分数就不再是独立 credit。当前流程保持：

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

被 promotion 的 Genome 与 held-out 被评估的是同一个冻结对象；评估后不会再回写 mutation policy。

## 4. Lingjing-MemoryBench：长期记忆 correctness floor

命令：

```bash
python scripts/memory_benchmark.py
```

`Lingjing-MemoryBench v1` 不调用外部 LLM judge，先验证系统不变量。目前十个 competency：

| Competency | 必须满足的约束 |
|---|---|
| cross-conversation recall | Conversation A 批准的项目记忆，在绑定同一 Project 的 Conversation B 可检索 |
| update tracking | 新 revision 成为 head 后，旧 revision 不得继续出现在正常检索结果 |
| scoped version isolation | build / branch scope 不能互相污染；无版本身份时不能猜版本 |
| conflict abstention | 当前素材 scope 冲突时只能退到合法 general scope，不能随机选版本 |
| selective forgetting | 撤回一个 head 后它必须消失，其他 active memory 不受影响 |
| pending memory isolation | pending proposal 在人工批准前不是 Project Memory truth |
| provenance integrity | 批准后保留 user-confirmed proposal/message 来源，不改写成伪确认 |
| queued snapshot revocation | queued revision 被撤回后旧 job 必须 invalidated，不能偷偷升级到新 head |
| restart persistence | 同一数据库重建 Store 后 active Project Memory 继续可检索 |
| ingestion outbox cancellation | accepted message 即使 analysis job 被取消/删除，durable ingestion 仍 exactly-once 恢复 proposal |

当前 correctness floor 为 **10/10**。这不是 SOTA memory accuracy，只是治理与恢复语义全部满足。

Durable ingestion 的权威链：

```text
committed user message
  + analysis job
  + immutable message.accepted event (v2 locator)
              ↓
    ingestion receipt / lease / retry
              ↓
         pending proposal
              ↓
       explicit human approval
              ↓
      authoritative Project Memory
```

新 v2 outbox locator 不依赖 analysis job row 存活；历史事件仍保留 timestamp fallback。Delivery 由 receipt idempotency 防重，proposal 再用 source fingerprint 做第二层防重。

### Multi-worker load protocol

命令：

```bash
python scripts/memory_ingestion_load_benchmark.py --events 40 --workers 4 --require-complete
```

CI SQLite run 只标为 `sqlite-concurrency-mechanism-smoke`。生产型 PostgreSQL 证据必须使用 disposable database，并显式传 `--confirm-disposable-database`；结果标签为 `postgresql-multiworker-load-measurement`，但仍不是通用生产 SLA。真实执行方式见 external evidence runbook。

## 5. Lingjing-IdentityBench：revision identity safety floor

命令：

```bash
python scripts/memory_identity_benchmark.py
```

Identity Resolver 只提供 suggestion，仍是 shadow/advisory path；UI 必须先“采用建议 key”，再执行独立 proposal approval。False merge 比 false split 更危险，因此允许 abstain，不为了 recall 强行归链。

当前 deterministic adversarial corpus 为 **280 cases**：124 positives、156 negatives，覆盖 value/paraphrase 更新、cross-build、cross-branch、predicate/entity collision、ambiguous key、kind mismatch 和 retracted head。

当前结果：

```text
correct                                  280 / 280
precision                                1.0
positive recall                          1.0
false merge rate                         0.0
false split rate                         0.0
abstention rate                          0.557143
safe coverage at zero false merge        0.442857
unsafe force-best false merge rate       0.589744
```

默认 gate 要求 `false_merge_rate == 0`、precision `== 1.0`、positive recall `>= 0.80`。Force-best comparator 说明“不允许 abstain”会显著放大错误合并风险；这仍然只是 synthetic adversarial safety floor，不是外部 identity SOTA。

## 6. ContextOS long-horizon correctness

独立 Context workflow 包含：

```bash
python scripts/context_memory_benchmark.py --messages 5000
python scripts/context_correctness_benchmark.py
python scripts/context_adversarial_benchmark.py
```

Adversarial protocol 真实构造 **10k + 50k dense histories**，检查旧约束、远距离 identifier、状态覆盖、premise safety、缓存/编译边界等机制。ContextCompiler / ContextBudgetBroker 的 deterministic character budget 是 tokenizer-independent compatibility floor；provider-aware token packing/native count 是后续安全层。

这些结果证明长上下文控制机制没有退化，不等于最终模型问答质量。

## 7. 模型级 long-horizon protocol

`lingjing-long-horizon-model-v1` 用同一 provider/model 比较 `baseline_last8` 与 `contextos`：

```bash
python scripts/long_horizon_model_benchmark.py
```

无参数只运行 4-case synthetic scorer/packing smoke，标签固定为：

```text
evidence_class = synthetic-protocol-smoke-not-model-quality-evidence
quality_claim  = none-protocol-smoke
```

真实 held-out 最低要求 40 cases，QA/update/abstention/workflow 各至少 10，且至少 30 个 long-range anchor 位于 legacy last-eight 之外。即使完成真实 frozen held-out 自动评测，也只允许 `measured-heldout-anchor-rubric-only`，更广泛的语义质量结论仍要求 blind human adjudication。

详见 [`LONG_HORIZON_MODEL_BENCHMARK.md`](./LONG_HORIZON_MODEL_BENCHMARK.md)。

## 8. Multimodal correctness 与 live quality protocol

CI 的 `Multimodal Context Benchmark` 验证：

- text/image/video/audio 都能进入 bounded context；
- full-log/text、hierarchical video segment、acoustic window 保留 raw-source provenance；
- build/branch/commit/environment scope 在发送到 semantic backend **之前** fail-safe 过滤；
- wrong-build / forbidden asset 不得消耗 multimodal budget，也不能从 sidecar 回流；
- deterministic smoke 与 live semantic quality 明确分开。

Real `game-rd-mm-v1` corpus 由真实私有素材 + 人工 query/relevance/temporal/scope 标注构建，repository 不内置伪造 held-out 数据。Scaffold/readiness/freeze 工具都不会自动生成 gold labels。

Live single/matrix runner 报告 Recall@K、MRR、temporal IoU/hit rate、contamination、latency、bytes 和 worker-lane proxy；measured label 还要求 immutable deployment identity、warmup、重复 full-corpus runs、每次都看到 semantic backend，以及 **zero contamination**。

详见 [`MULTIMODAL_QUALITY_BENCHMARK.md`](./MULTIMODAL_QUALITY_BENCHMARK.md) 与 external evidence runbook。

## 9. GameAdapter / Integration Contracts

`Integration Contracts` 独立 workflow 运行：

```bash
python scripts/game_adapter_conformance.py \
  --execute-dry-run \
  --durable-replay-smoke \
  --require-conformance

python scripts/memory_ingestion_load_benchmark.py \
  --events 40 \
  --workers 4 \
  --require-complete

python scripts/long_horizon_model_benchmark.py
```

GameAdapter ticket 绑定 adapter/action、完整 request semantics 和 scope；消费发生在 dispatch 前。`SqlGameAdapterReplayStore` 配合 Alembic `20260909_0007` 使用 ticket primary key + unique nonce 做跨进程原子 replay protection，store 不可用时 fail-closed。

Synthetic adapter 输出固定保持：

```text
canonical_write_allowed = false
verifier_status = not-run
evidence_class = external-engine-observation-unverified
```

真实 Unity/Unreal/custom bridge 通过 conformance 也只证明 contract，不等于真实项目验证。详见 [`GAME_ADAPTER_PROTOCOL.md`](./GAME_ADAPTER_PROTOCOL.md)。

## 10. Provider-aware token safety 与校准

Provider path 支持：

- deterministic multilingual/CJK/code-aware estimate；
- operator-declared context profiles；
- Gemini model metadata + `models.countTokens`；
- Claude model metadata + `/v1/messages/count_tokens`；
- Gemini/Claude metadata cold-start singleflight、success cache 和 negative cache；
- OpenAI-compatible/custom **仅在 operator 显式提供 count endpoint 且标记 trusted 时**启用 exact count；
- estimate/exact delta、exact-estimate ratio、native-count extra RTT 和 media accounting telemetry。

Native count 在 `auto` 模式是 selective last-mile verifier，失败 fail-open；只有成功 exact count 明确超 safe input limit 时才阻断 generation。Token-count RTT 不是 generation TTFT，character estimate 也不是 provider billable cost。

## 11. Runtime / 产品 / deployment 回归

主 CI 包含：

- dependency manifest consistency；
- Python compile；
- full pytest；
- Harness self-evolution benchmark；
- Memory correctness benchmark；
- JavaScript syntax；
- Backend product E2E；
- Browser product E2E；
- Memory governance browser E2E；
- README / repository consistency 与 browser visibility；
- migration-chain regression，升级至 `20260909_0007` 并验证 memory-ingestion 与 GameAdapter replay schema。

Browser E2E 证明产品交互闭环没有被算法改造破坏；Memory governance E2E 证明 Project binding、proposal approve/reject、revision/state/history、workspace role re-authorization 和 identity suggestion 的显式人工动作都能走通。

这些证据必须分别报告，不能揉成一个“综合性能分”。

## 12. 外部比较规则

与任何 Agent / Harness / workflow / memory / retrieval 系统比较，至少冻结：

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
13. 对 memory 比较额外冻结相同 write/update/retention/delete policy；
14. 对 retrieval 比较额外冻结 corpus digest、deployment identity、warmup/repeat schedule 和 contamination gate。

不同底座、不同预算、不同环境的 README 数字不能直接横向宣称性能领先。

## 13. 产品指标不是 Runtime / Memory Benchmark

产品层通过 `product_events` 观察真实任务闭环，例如首次任务完成率、首次交付耗时、中断率、失败率、恢复率、继续执行率、人工介入率、证据打开率、结果采纳率和人工验证反馈率。

这些指标回答“产品是否帮助研发任务完成”，不能和 Harness objective、MemoryBench correctness、IdentityBench safety、synthetic Context smoke 或 retrieval protocol score 混成同一套分数。

真实 held-out、GPU、PostgreSQL、provider cost/TTFT 或 Unity/Unreal 项目证据尚未提供时，必须明确写“未测”，不能用 CI smoke 补位。
