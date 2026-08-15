# WorldForge Architecture

## 1. 核心边界

WorldForge 将动态游戏环境本身作为 Harness 的核心运行对象。系统里只有一个 **Canonical World State** 是真实世界；所有候选未来都运行在精确快照的隔离副本里。

模型和 Runtime 分工明确：

- `WorldForge-M1`：动作先验、置信度、因素解释。
- `Adaptive Planner`：根据 Goal / Belief / World State / Skill / Memory 形成动态候选空间。
- `RecursiveAgentScheduler`：按需派生专家 Agent，而不是预先写死 Agent 拓扑。
- `CounterfactualBrancher`：在多个未来中试演候选动作。
- `ActionSandbox`：真实执行前检查动作契约与风险预算。
- `StateVerifier`：执行后检查状态不变量、灾难风险、异常奖励、terminal failure。
- `EventStore`：记录所有状态与决策事件，支持审计、replay、fork。
- `FailureDrivenEvolver`：从失败轨迹生成受约束 Skill patch，经 regression gate 后才合并。

因此，Agent 的自主性不依赖固定 DAG，也不等同于“大模型自由发挥”。

## 2. 自主决策循环

每一个 tick：

1. 读取当前 Canonical World State。
2. 更新 Belief State 与隐藏机制不确定性。
3. WorldForge-M1 输出动作先验。
4. Planner 融合模型、Skill、Memory 与专家投票形成候选动作。
5. 根据当前状态按需派生 Recursive Agents。
6. Counterfactual Brancher 对 top-K 候选创建隔离环境副本。
7. 每个分支包含一个与 canonical RNG 完全一致的未来作为锚点，再加入随机扰动 rollout 估计方差与尾部风险。
8. Verifier 计算进度、风险、胜率、生存率与违规项。
9. 选择风险调整后最优分支。
10. Action Sandbox 检查真实提交动作。
11. 只在 canonical environment 上执行一个动作。
12. Post-action Verifier 判断 accept / replan / rollback。
13. 将结果写入事件链与 Memory。
14. 任务结束后进行失败归因与策略演进。

## 3. 为什么必须有 canonical future anchor

纯随机 Monte Carlo rollout 可能在高方差游戏中低估当前真实随机状态对应的灾难风险。WorldForge 的每个候选动作至少包含一个 `seed_offset=0` 的精确未来，再用额外扰动未来估计泛化风险。

这样 Counterfactual Search 同时回答两个问题：

- **如果现在真的执行，最接近当前真实世界的后果是什么？**
- **如果随机性变化，这个策略的稳定性如何？**

## 4. Event Sourcing 与 Time Travel

SQLite/WAL Event Store 采用 append-only 事件，并将 `prev_hash` 与当前 payload 计算 SHA-256 hash chain。Checkpoint 保存真实环境 snapshot，包括 RNG state。

它支持：

- WebSocket 断线重连后的事件 catch-up；
- 每次决策的 evidence trail；
- 按 seq 回看真实世界状态；
- failure trajectory regression；
- Fork / Replay；
- 验证策略演进没有破坏历史场景。

## 5. Recursive Agent 不是固定多 Agent Workflow

根节点永远是 Coordinator，但子节点由当前状态决定：

- 高不确定性：`MechanicsProbe`
- 高威胁：`SurvivalAudit`
- 高战斗压力：`CombatAgent`
- 经济场景：`EconomyAgent`
- exploit 测试：`ExploitProbe`

因此每个 tick 的专家拓扑都可以不同。

## 6. 反事实分支

每个候选动作：

```text
canonical snapshot
    ├── branch A / exact future
    ├── branch A / stochastic future 1
    ├── branch A / stochastic future 2
    ├── branch B / exact future
    └── ...
```

分支内可继续由 Planner 决策数步，但不会写入真实世界。评分结合：

- cumulative reward
- 敌方进度
- 玩家生存率
- success probability
- downside utility
- outcome bonus / terminal failure penalty
- verifier violations

## 7. QA Adversarial Probe

最优策略往往会主动避开 exploit，但 QA 的目标恰恰是**发现异常机制**。因此 WorldForge 在 exploit 场景中启动隔离的 adversarial probe：主动探索可疑奖励循环并记录复现轨迹。

探针只在 fork 环境运行；主运行的 canonical state 在探针前后进行 hash / state 对照，确保测试行为不会污染真实任务。

## 8. 策略演进

```text
失败轨迹
   ↓
Failure Attribution
   ↓
Candidate Skill / Memory Patch
   ↓
Sandbox Replay
   ↓
Regression Gate
  ↙          ↘
Reject      Merge as new version
```

不允许一次失败直接修改在线策略。每个 patch 都是新版本，且必须通过历史场景回放。

## 9. WorldForge-M1

本项目只启用自研本地模型 `WorldForge-M1`。

输入是结构化游戏状态与 Belief 特征，输出是合法动作空间上的 policy prior。训练标签来自 verified counterfactual trajectories，因此模型学习的是 Harness 已验证过的局部策略偏好，而不是绕开 Harness 直接决定真实执行。

最终动作始终由 Runtime 在模型先验、专家分析、Skill / Memory、反事实试演和 Verifier 共同约束下决定。

## 10. SaaS Control Plane（v2.0）

上面的 WorldForge Runtime 是“分析与验证引擎”；灵境产品层在它之外增加独立的 SaaS 控制面：

```text
Web Workspace
    ↓
FastAPI
    ├── Auth / Principal
    ├── Workspace Tenant Guard
    ├── Audit / Request ID / Security Headers
    ├── PostgreSQL / SQLite product store
    ├── S3 / Local object storage
    └── Durable analysis_jobs
             ↓
          Worker(s)
             ↓
      ProductAnalyzer
             ↓
      WorldForge Runtime + Model Gateway
```

产品数据与 Runtime 的 append-only world event store 是两个不同边界：

- Product Store：用户、Workspace、对话、消息、素材、任务事件、审计与分析 Job；生产建议 PostgreSQL。
- Runtime Event Store：环境状态、决策、Verifier、Replay/Fork 等运行时事件；当前本地实现继续服务于场景复核与研究运行时。

跨进程 UI 实时进度以 Product Store 的 `task_events` 为事实源，因此 API 与分析 Worker 可以分离部署，不依赖进程内 `asyncio.Queue`。
