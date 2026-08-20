<div align="center">

# 灵境

### Self-Evolving Game R&D Agent Harness Runtime

**不是把模型接进一个聊天框，而是把游戏研发任务放进一个有状态、可验证、可恢复，并能在独立门禁下改进 Harness 的执行系统。**

`STATEFUL` · `SELF-EVOLVING HARNESS` · `COUNTERFACTUAL` · `SEALED EVAL` · `RECOVERABLE`

目标、素材、世界状态、Agent 拓扑、Skill、Memory、反事实预算与恢复路径进入同一条可审计任务轨迹；安全内核保持冻结，Harness 行为在独立评估门禁下持续进化。

</div>

![灵境游戏研发执行工作台总览](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/cover.png)

> **当前产品边界（2026-08）**：仓库内 `canonical execution / rollback / verifier / counterfactual` 针对内置可验证环境 **BalanceLab**。用户上传的图片、视频、音频、日志和配置不会自动变成“可执行的真实游戏环境”。工作台对用户素材形成事实性内容结论时依赖已配置、且能力匹配的推理 Provider；`demo` 只用于产品流程与内部 Harness 自检，内部 BalanceLab 结果不会被当作用户素材的“同条件复现”或证据。真正对某个游戏版本做自动操作与同条件复现，需要额外接入该游戏的可执行 adapter / telemetry / test environment。

---

## Product Thesis · 从“调用 Agent”变成“拥有一个可进化的研发执行系统”

> **目标 → 素材 / 可执行状态 → Harness phenotype → Agent 决策 → clone 试演 → canonical 执行 → Verifier → 证据 → 交付 → Harness evolution**

| CONTROL | TRUST | LIFECYCLE | EVOLUTION |
|---|---|---|---|
| **执行可控**：产品任务可停止、重试；BalanceLab Runtime 支持 checkpoint / rollback / replan，canonical state 只有 Runtime 能推进 | **结论有边界**：素材索引、任务事件、推理结果与人工反馈可追踪；内部 demo 自检不冒充用户证据 | **任务可治理**：身份、协作、搜索、归档、审批删除、反馈门禁持久化 | **Harness 可进化**：调度、专家结构、Skill、Memory、搜索与融合策略进入 Genome，并由 sealed held-out gate 决定是否晋升 |

灵境把“模型能力”与“Agent Harness 能力”分开：模型可以替换；**任务状态所有权、工具循环、上下文、恢复、验证、Harness 进化与代际谱系**属于 Runtime。对于用户上传素材，模型/Provider 负责语义理解；Runtime 不会因为任务被分类成“战斗/数值/回归/NPC”就凭空生成事实判断。

---

# Agent Architecture · Frozen Kernel × Evolvable Harness

WorldForge 现在不是“固定 Planner + 若干手写 Specialist”。产品入口是 `SelfEvolvingWorldForgeEngine`：外层 Harness 可以进化自己的可执行程序面，内层 Frozen Kernel 负责不可被候选篡改的状态、安全、验证和晋升协议。

> **核心边界：Harness 可以改变“怎么工作”，但不能改变“谁拥有 canonical state、什么算安全、谁给候选打分、什么条件允许上线”。当前仓库里的 canonical environment 是 BalanceLab，不等同于任意用户游戏进程。**

### 01 · Architecture at a glance

```mermaid
flowchart TB
    INPUT["Goal + Assets + Runtime Evidence"] --> STATE["Canonical World State + Belief"]

    subgraph GENOME["EVOLVABLE HARNESS GENOME"]
        FEAT["Representation / Features"]
        MEM["Memory Retrieval"]
        SKILL["Skills + Activation Gates"]
        TOPO["Specialist Topology + Gates"]
        FUSION["Planner Fusion + Epistemic Control"]
        SEARCH["Counterfactual Budget + Risk Utility"]
        MUT["Mutation Policy"]
    end

    subgraph EXEC["RUNTIME PHENOTYPE"]
        SPEC["Genome-instantiated Specialists"]
        PLAN["Genome-interpreting Planner"]
        BRANCH["Clone-world Counterfactual Search"]
        CHOOSE["Candidate Selection"]
    end

    subgraph KERNEL["FROZEN KERNEL"]
        CHECK["Checkpoint / State Ownership"]
        SANDBOX["Sandbox"]
        CANON["Canonical Environment"]
        VERIFY["Independent Verifier"]
        EVENTS["Audit / Event Chain"]
        PROMOTE["Sealed Evaluation + Atomic Promotion"]
    end

    STATE --> FEAT
    FEAT --> SPEC
    MEM --> PLAN
    SKILL --> PLAN
    TOPO --> SPEC
    SPEC --> PLAN
    FUSION --> PLAN
    PLAN --> BRANCH
    SEARCH --> BRANCH
    BRANCH --> CHOOSE
    CHECK --> CHOOSE
    CHOOSE --> SANDBOX --> CANON --> VERIFY
    VERIFY --> EVENTS
    VERIFY -->|continue / rollback / replan| STATE

    EVENTS --> REFLECT["Policy-agnostic Trace Reflector"]
    REFLECT --> QD["WHERE × WHY Semantic Archive"]
    QD --> EVOLVE["Antithetic ES + Stable-Elite Refinement + Minimum Effective Edit"]
    MUT --> EVOLVE
    EVOLVE --> SHADOW["Train-only Shadow Arena"]
    SHADOW --> FREEZE["Freeze Search Trajectory"]
    FREEZE --> PROMOTE
    PROMOTE -->|accepted| GENOME
    PROMOTE -->|rejected| QD
```

### 02 · 哪些可以进化，哪些永远不能由进化器修改

| Surface | 状态 | 说明 |
|---|---|---|
| Feature representation / normalization | **Evolvable** | 决定 Harness 如何表示连续世界状态与动态 `tag:*` 特征 |
| Belief / uncertainty parameters | **Evolvable** | 决定隐藏机制下的 belief 形状 |
| Memory kernel / feature weights / recency | **Evolvable** | 决定经验如何被检索并回流当前决策 |
| Skill gate / bias / reliability | **Evolvable** | Skill 不再由 Python `if hp < ...` 触发 |
| Specialist topology / activation / action features | **Evolvable** | 可以 split、prune、重组；Python 不认识“什么时候必须生成某专家” |
| Planner fusion / epistemic action coefficients | **Evolvable** | 不再把 `1.65`、`2.2`、`scout +1.15` 写进 Runtime 逻辑 |
| Counterfactual width / horizon / rollouts / risk utility | **Evolvable** | 调用方只给资源上限，Genome 决定实际预算 |
| Mutation operator policy | **Evolvable** | 连“用哪种变异更有效”也能随代际改变 |
| Canonical state ownership | **Frozen** | 候选 Harness 永远不能直接写 canonical state |
| Sandbox / invariant verification / rollback | **Frozen** | 不能通过“把安全规则改松”获得高分 |
| Train / held-out split 与 credit protocol | **Frozen** | held-out 不参与候选生成与 refinement |
| Atomic promotion / lineage | **Frozen** | 只有被独立评估的同一 Genome 对象才能晋升 |

### 03 · 单次执行：Genome 决定工作方式，Kernel 决定执行权限

```mermaid
sequenceDiagram
    participant K as Frozen Kernel
    participant G as Harness Genome
    participant A as Specialists / Skills / Memory
    participant P as Planner
    participant C as Counterfactual
    participant X as Clone Worlds
    participant S as Sandbox
    participant E as Canonical Env
    participant V as Verifier

    K->>K: checkpoint canonical state
    K->>G: read active generation
    G->>A: instantiate gates / topology / memory policy
    A-->>P: bounded scores + priors
    G->>P: fusion / epistemic coefficients
    P-->>K: ranked legal actions
    G->>C: adaptive search budget + utility
    C->>X: parallel bounded rollouts
    X->>V: verify simulated transitions
    V-->>C: utility + findings + unsafe violations
    C-->>K: branch ranking
    K->>S: pre-execution check
    S-->>K: allow / alternative
    K->>E: execute one canonical action
    E-->>K: state + reward + evidence
    K->>V: post-state verification
    V-->>K: continue / rollback / replan / finding
```

### 04 · 自进化：搜索者不能给自己发毕业证

```mermaid
flowchart LR
    TRACE["Verified Traces"] --> REF["Policy-agnostic Reflection"]
    REF --> CELL["WHERE × WHY"]
    CELL --> PAIR["Antithetic Candidate Pairs"]
    PAIR --> PLATEAU["Behavior Plateau Detection"]
    PLATEAU --> ELITE["Stable Train Elites"]
    ELITE --> REFINE["Topology / Gate / Skill / Memory / Parameter Refinement"]
    REFINE --> MEE["Minimum Effective Edit"]
    MEE --> LOCK["Freeze Search"]
    LOCK --> HELD["Sealed Held-out Judge"]
    HELD --> PARETO["Pareto + Semantic QD Archive"]
    PARETO -->|pass| NEXT["Atomic Generation Promotion"]
    PARETO -->|reject| KEEP["Keep Current Generation"]
```

这条链的关键不是“会改配置”，而是 **proposal 与 credit 分离**：搜索阶段只看 train；搜索轨迹冻结以后才打开 held-out；候选不能修改 Verifier、评估器或 promotion gate。

---

## 工作台 · Real Product Surface

下面 8 张图来自浏览器产品状态。README Gallery 会从运行中的产品采集 PNG，发布到稳定 Release 资产，并在 CI 中检查 README 资源可访问性。

| **01 · 身份入口** | **02 · 新任务** |
|---|---|
| 工作空间身份、权限与安全边界。<br><br>![登录与工作空间入口](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/auth.png) | 从研发目标开始，而不是从模型配置开始。<br><br>![空任务工作台](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/workspace-empty.png) |

| **03 · 素材输入** | **04 · 执行中** |
|---|---|
| 图片、视频、音频、日志、配置和文档进入同一个任务上下文。<br><br>![多模态素材上传](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/upload.png) | 产品任务进度持续进入任务事件轨迹，需要时可以停止。<br><br>![任务执行状态](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/task-running.png) |

| **05 · 任务结果** | **06 · 证据核验** |
|---|---|
| 推理结果、后续动作、结构化交付和协作留在同一个工作台；无可用 Provider 时会明确返回“证据不足”，而不是生成场景化事实。<br><br>![任务结果工作台](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/workspace.png) | 结果保留素材索引与可用证据入口；只有实际用户素材能计入用户证据，内部 BalanceLab demo 自检不会被伪装成“同条件复现”。<br><br>![证据核验](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/evidence.png) |

| **07 · 持续上下文** | **08 · 产品总览** |
|---|---|
| 后续追问继承当前任务已有素材，不把上一轮上下文丢掉。<br><br>![多模态任务上下文](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/multimodal.png) | 控制、证据、交付与协作集中在同一个任务空间。<br><br>![产品总览](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/cover.png) |

### Inference / Demo boundary

- `auto`：只会选择**当前已配置且能力匹配**的 Provider；没有可用 Provider 时不生成具体游戏事实结论。
- `demo`：不是一个伪装成模型的 inference provider。它可以运行内部 BalanceLab Harness 自检，但返回会标记 `scope=internal_balance_lab`、`user_evidence=false`，不会进入用户证据，也不会因为一次产品任务的人工反馈直接触发 Harness evolution。
- 图片/视频/音频是否能被真正理解取决于所选 Provider 的 multimodal / audio 能力；文本文件可通过提取后的文本上下文进入文本 Provider。
- 如果要声称“真实游戏同条件复现”，必须接入该游戏的可执行测试环境；仅有录像、截图或日志不等于自动复现。

---

# Method · Game Harness Evolution

这里的公式对应当前实现的**符号化机制**，不再把 bootstrap prior 的具体数字冒充算法本身。初始数值只存在 `default_harness_genome.json`，之后可以由 Harness Evolution 改变；Python Runtime 负责解释 Genome。

### 01 · Evolvable representation

世界状态首先由当前 Genome $G$ 的表示参数映射成特征：

```math
\phi_G(s)_k
=
\mathrm{clip}
\left(
\frac{x_k(s)}{d_{G,k}},
-c_{G,k},
c_{G,k}
\right)
```

动态场景标签不需要 Python 认识具体名字，统一进入 `tag:<name>` 特征空间。

### 02 · Smooth activation gate

```math
g_j(s;G)
=
\sigma
\left(
\frac{w_j^\top\phi_G(s)-\tau_j}{T_j}
\right)
```

门控阈值、温度和特征权重都属于 Genome；因此 Specialist / Skill 的“什么时候出现”不是手写 `if`。

### 03 · Specialist phenotype

```math
b_j(a\mid s,G)
=
g_j(s;G)c_j
\left(
\beta_{j,a}+u_{j,a}^\top\phi_G(s)
\right)
```

一个 Specialist 的角色名只是可审计元数据；真正行为由 gate、confidence、action bias 和 action-feature weights 共同决定。

### 04 · Bounded specialist aggregation

```math
B_{\mathrm{spec},G}(a)
=
\mathrm{clip}
\left(
\sum_{j\in\mathcal A_G(s)}b_j(a\mid s,G),
-C_G,
C_G
\right)
```

### 05 · Unified planner

```math
S_G(a\mid s)
=
V_G(a\mid s)
+\lambda_{\mathrm{skill},G}B_{\mathrm{skill},G}(a\mid s)
+\lambda_{\mathrm{mem},G}M_G(s,a)
+\lambda_{\mathrm{policy},G}Z_\theta(a\mid s)
+\lambda_{\mathrm{spec},G}B_{\mathrm{spec},G}(a\mid s)
+E_G(a,s)-R_G(a,s)
```

这里所有 Harness 融合系数都从当前 Genome 读取；`Policy` 只是 Harness 内一个 prior，而不是 Harness 本身。

### 06 · Epistemic tension

```math
T_G(a,s)
=
\min
\left(
C_G^{\mathrm{dis}},
\mathrm{Std}(v_1(a),\ldots,v_n(a))
\right)
u(s)
```

### 07 · Generic epistemic control

```math
E_G(a,s)
=
\beta^E_{G,a}
+T_G(a,s)\kappa_{G,a}
+T_G(a,s)\,\rho(s)\,\xi_{G,a}
```

Python 不再写 `if action == scout`。每个动作的 base / tension / threat-tension 系数都属于 Genome，可被同一演化机制修改。

### 08 · Continual memory kernel

```math
M_G(s,a)
=
\frac{
\sum_{i:a_i=a}
\left(r_i+\zeta_G y_i\right)
\exp\left(-d_G(s,s_i)/T_G^M\right)
\exp\left(-\lambda_G^M\Delta t_i\right)
}{
\sum_{i:a_i=a}
\exp\left(-d_G(s,s_i)/T_G^M\right)
\exp\left(-\lambda_G^M\Delta t_i\right)
}
```

$d_G$ 使用 Genome 定义的 feature weights；因此“记什么、哪些状态算相似、多久衰减”都在 Harness surface 内。

### 09 · Adaptive counterfactual budget

```math
\begin{aligned}
W_G(s)&=\mathrm{clip}(b_W+u\,k_W+\rho\,r_W,1,W_{\max})\\
H_G(s)&=\mathrm{clip}(b_H+u\,k_H,1,H_{\max})\\
N_G(s)&=\mathrm{clip}(b_N+u\,k_N,1,N_{\max})
\end{aligned}
```

调用方只提供硬资源上限 $W_{\max},H_{\max},N_{\max}$；实际搜索预算由 Genome 按当前 uncertainty / threat 分配。API 的 `RunConfig` 另外有请求级硬上限，避免单个请求把步数无限放大。

### 10 · Risk-adjusted branch utility

```math
Q_G(a)
=
w_{\mu,G}\mathbb E[U]
-w_{\sigma,G}\mathrm{Std}(U)
+w_{d,G}\min(U)
+w_{p,G}p_{\mathrm{success}}
```

### 11 · Self-referential mutation policy

```math
p_G(o)
=
(1-\epsilon_G)
\frac{\exp(\ell_{G,o}/T_G^o)}
{\sum_{o'}\exp(\ell_{G,o'}/T_G^o)}
+
\frac{\epsilon_G}{|\mathcal O|}
```

变异算子集合当前覆盖 parameter / gate / Skill / Memory / topology split-prune / recombination / meta-mutation；operator logits 自身也属于 Genome。

### 12 · True antithetic edit pair

```math
G^{+},G^{-}
=
\mathrm{Mutate}(G,o,\pm\sigma_G;\,\omega)
```

$\omega$ 是同一个采样 edit plan。正负候选共享编辑位置，只反转数值方向，减少随机搜索噪声。

### 13 · Behavior-plateau adaptation

```math
\sigma_{t+1}
=
\min
\left(
\sigma_{\max},
\gamma\sigma_t
\right)
\quad
\text{if}\quad
\frac{|\{G':\Pi(G')\ne\Pi(G)\}|}{|\mathcal P_t|}<\eta
```

如果很多 Genome 数值变了但最终行为 phenotype $\Pi(G)$ 完全没变，搜索会主动扩大步长跨过离散 argmax 平台，而不是继续浪费候选。

### 14 · Stable train selection

```math
J_{\mathrm{stable}}(G)
=
\bar J_{\mathrm{train}}(G)
-\lambda_{\mathrm{stab}}
\mathrm{Std}
\left(
J_{\mathrm{train}}^{(1)}(G),\ldots,J_{\mathrm{train}}^{(K)}(G)
\right)
```

不是只追平均分；跨场景 / seed 不稳定的候选会降低 elite 排名。

### 15 · Minimum Effective Edit

```math
\alpha^\star
=
\inf
\left\{
\alpha\in(0,1]:
J_{\mathrm{train}}(G_\alpha)
\ge
J_{\mathrm{train}}(G)+\delta
\;\land\;
\Pi(G_\alpha)\ne\Pi(G)
\right\}
```

其中 $G_\alpha$ 是 baseline 与候选之间的 trust-region 插值。二分搜索只使用 train，目的是找到**刚好改变行为且有效的最小 Harness 编辑**，降低跨 seed 过冲。

### 16 · Semantic QD + Pareto preservation

```math
\begin{aligned}
c(G)&=(\mathrm{WHERE},\mathrm{WHY})\\
G_1\succ G_2
&\iff
m_k(G_1)\ge m_k(G_2)\;\forall k
\;\land\;
\exists k:m_k(G_1)>m_k(G_2)
\end{aligned}
```

Archive 不只保存一个全局 champion，而是按病理类型保留互补 elite；quality / safety / efficiency / novelty 共同参与选择。

### 17 · Paired-bootstrap held-out credit

```math
\mathrm{LCB}_q(G')
=
Q_q
\left(
\frac{1}{N}
\sum_i
\left[
J_i^{(b)}(G')-J_i^{(b)}(G)
\right]
\right)
```

候选生成、elite selection、refinement、trust-region 和 boundary search 完成后，搜索轨迹先冻结，再第一次打开 held-out。

### 18 · Atomic promotion gate

```math
\mathrm{Promote}(G')
=
\mathbb 1
\left[
\Delta_{\mathrm{train}}\ge\delta
\land
\Delta_{\mathrm{heldout}}\ge0
\land
\mathrm{LCB}_q\ge0
\land
\Delta_{\mathrm{safety}}\ge-\eta_s
\land
\Delta_{\mathrm{quality}}\ge0
\land
\mathrm{cost}(G')\le\kappa\,\mathrm{cost}(G)
\right]
```

被晋升的对象就是被 held-out 评估过的同一个 Genome；评估以后不再偷偷改 mutation policy 或任何参数。

---

# Evidence · Harness 真的进化过，而不是“代码里有 evolve()”

CI 中有独立命令：

```bash
python scripts/harness_evolution_benchmark.py
```

它从干净进程、bootstrap Genome 开始，对 4 个 BalanceLab 研发场景使用独立 train / held-out seeds。held-out **不参与候选生成**；只有最后 promotion credit 才能看到。

当前 `sealed-heldout-game-harness-2026-08` 记录的仓库基准结果：

| Metric | Result |
|---|---:|
| Candidate genomes | **36** |
| Passed promotion gate | **6** |
| Baseline generation | **1** |
| Promoted generation | **2** |
| Train objective gain | **+0.004712** |
| Sealed held-out objective gain | **+0.000559** |
| Paired-bootstrap lower bound | **0.000000** |
| Promoted held-out quality | **0.612886** |
| Promoted held-out safety | **0.966518** |
| Promoted held-out efficiency | **0.730917** |
| Promoted held-out operations | **23.25** |
| Winning lineage | **Gate mutation → minimum-effective boundary at α = 0.6406** |

这组数据的意义是**仓库内 BalanceLab 机制证明，不是外部游戏产品成绩，也不是通用 SOTA 宣称**。held-out 增益很小，真正重要的是：候选在看不到 held-out 的情况下改变 Harness，随后仍能通过独立 quality / safety / efficiency / bootstrap credit，并产生新的持久化 generation。

场景覆盖 Boss 爆发窗口、经济陷阱、玻璃大炮极端 Build、奖励循环漏洞回归。`tests/test_harness_promotion.py` 会在 pytest 内再跑 promotion regression，独立 benchmark 则用于隔离测试进程全局状态。

---

# 为什么这不是“把常数搬进 JSON”

如果只是把 `0.45` 从 Python 移到配置文件，仍然不是 Harness Evolution。当前实现额外具备：

- **结构搜索**：Specialist 可以 split / prune / recombine，不只调权重；
- **Skill / Memory evolution**：可改变 action bias、gate、reliability、相似度特征、温度与衰减；
- **Meta-mutation**：mutation operator 的选择策略本身会获得 credit；
- **Behavior-aware search**：发现参数变了但动作轨迹没变时自动跨越行为平台；
- **Minimum Effective Edit**：不是“越大越好”，而是寻找刚刚足以改变 phenotype 的最小有效更新；
- **Semantic Quality-Diversity**：不同 WHERE × WHY 病理保留不同 elite；
- **Sealed credit**：搜索者不能用 held-out 指导自己，再宣布自己成功；
- **Frozen judge**：Verifier、canonical ownership、evaluation、promotion 不能进入 Genome。

这也是灵境和普通“Prompt 自优化”“训练一个 policy”“失败后 bias +0.5”的根本区别。

---

<details>
<summary><strong>Research provenance · verified public references (checked 2026-08-21)</strong></summary>

这些链接用于说明公开研究/工程中的相关机制；README 不声称逐仓库复制其实现。外部引用本身与灵境的代码正确性、benchmark 结果相互独立。

| Source | Status | 相关机制 | 灵境中的对应方向 |
|---|---|---|---|
| [Adaptive Auto-Harness](https://arxiv.org/abs/2606.01770) · [code](https://github.com/A-EVO-Lab/AdaptiveHarness) | **2026 public research + open source** | open-ended task streams、stateful evolution、harness routing / adaptation | Genome / archive / stateful evolution 的研究参考 |
| [Self-Evolving Agent Harnesses via Gated Semantic Quality-Diversity](https://arxiv.org/abs/2607.13683) | **2026 public research** | proposal-credit separation、WHERE×WHY categorical QD、sealed test | pathology archive + sealed held-out promotion 的研究参考 |
| [Promptbreeder](https://proceedings.mlr.press/v235/fernando24a.html) | **ICML 2024** | self-referential prompt mutation | mutation policy 自身获得 credit 的研究参考 |
| [Pi](https://github.com/badlogic/pi-mono) | **open-source engineering baseline** | minimal coding harness、sessions、skills / extensions | Harness 工程纪律参考；不是灵境的上游代码 |
| [DeerFlow](https://github.com/bytedance/deer-flow) | **ByteDance open-source engineering baseline** | subagents、memory、sandbox、skills、long-horizon harness | 长任务 Harness 工程能力参考；不是灵境 benchmark 对手 |
| [Awesome DeepSeek Agent](https://github.com/deepseek-ai/awesome-deepseek-agent) | **DeepSeek organization curated integration guides** | DeepSeek 模型在 Agent / coding harness 中的接入方式 | 模型生态兼容性参考；不把第三方 Harness 冒充 DeepSeek 官方统一 Runtime |

当前仓库算法组合可以概括为：

> **Evidence-linked Genome Search + Antithetic Adaptive ES + Behavior Plateau Escalation + Stable-Elite Refinement + Minimum Effective Edit + Semantic Pareto-QD + Sealed Held-out Promotion**

这是对仓库实现的组合性描述，不代表上述论文作者为该组合背书，也不代表灵境复现了它们的全部实验结论。

</details>

---

<details>
<summary><strong>Engineering map · 代码对应关系</strong></summary>

| Capability | Implementation |
|---|---|
| Frozen canonical execution kernel | `worldforge/runtime/engine.py` |
| Product self-evolving wrapper | `worldforge/runtime/self_evolving_engine.py` |
| Harness schema / persistent generation | `worldforge/runtime/harness_genome.py` |
| Bootstrap prior only | `worldforge/runtime/default_harness_genome.json` |
| Policy-agnostic trace reflection | `worldforge/runtime/harness_reflection.py` |
| Generic evolution primitives / QD archive / bootstrap credit | `worldforge/runtime/harness_evolution.py` |
| Game-adapted search / antithetic ES / plateau / trust region | `worldforge/runtime/harness_search.py` |
| Frozen game R&D shadow evaluator | `worldforge/runtime/game_harness_evaluator.py` |
| Genome-interpreting Planner | `worldforge/runtime/planner.py` |
| Genome-derived dynamic specialists | `worldforge/runtime/recursive.py` |
| Genome-controlled Skills | `worldforge/runtime/skill_bank.py` |
| Continuous evolvable Memory kernel | `worldforge/runtime/memory.py` |
| Genome-budgeted counterfactual search | `worldforge/runtime/counterfactual.py` |
| Frozen invariant / finding verifier | `worldforge/runtime/verifier.py` |
| Promotion regression | `tests/test_harness_promotion.py` |
| Stress / hallucination regressions | `tests/test_stress_hardening.py` |
| Standalone sealed benchmark | `scripts/harness_evolution_benchmark.py` |

</details>

---

## Multimodal R&D Context

任务可以持续携带图片、视频、音频、日志、配置与文档。素材不是一次性附件：资产、消息、任务事件和反馈都进入 workspace 生命周期，后续追问继续继承同一个研发上下文。媒体文件会做格式探测；视频会提取关键帧；真正的语义理解仍取决于所配置 Provider 的能力。

## 适合的工作

- 战斗 / Boss 录像、截图、日志的证据整理、分析与复现计划；
- 经济、成长、掉落、奖励循环等数值问题的证据分析与验证计划；
- 截图 / 视频 / 日志 / 配置的跨模态证据汇总；
- 产品任务的停止、重试、归档、协作、反馈与审批删除；
- BalanceLab 内可验证 Runtime 的 counterfactual、rollback / replan、Verifier 与 Harness evolution；
- 结构化风险清单、回归清单和 evidence pack。

如果目标是“系统自己启动某个真实游戏版本、操控角色、复现 bug、采集引擎 telemetry 并判定修复”，需要实现并接入对应游戏 adapter；当前仓库没有一个能对任意用户游戏自动完成上述动作的通用 adapter。

## Product Boundary

灵境不会把“Agent 自主”理解成无限权限：永久删除任务需要审批；工作空间角色限制写操作；Harness promotion 有独立门禁；候选 Genome 不能通过修改 Verifier 或 held-out protocol 获得晋升。

同时，**产品工作台的用户素材结论与 BalanceLab Runtime 是两条不同的证据边界**：BalanceLab 可以证明 Harness 机制和内置环境中的执行/恢复能力，但不能证明某个用户游戏 bug 已被真实复现。没有可用推理 Provider 时，工作台只会保留素材与验证计划，不再输出固定场景结论。

---

## Quick Start

运行产品只需要 runtime 依赖：

```bash
git clone https://github.com/jiaweine/lingjing-game-studio.git
cd lingjing-game-studio
pip install -r requirements.txt
uvicorn worldforge.api.app:app --reload
```

打开 `http://127.0.0.1:8000`。

开发、测试和完整 Harness 验证使用 development 依赖：

```bash
pip install -r requirements-dev.txt
pytest -q
python scripts/harness_evolution_benchmark.py
python scripts/product_backend_e2e.py
```

浏览器 E2E 与 README 图片加载检查由 GitHub Actions 运行。

## Runtime / Product API

Runtime run API 实际挂在 `/api` 前缀下：

- `POST /api/runs`：启动 BalanceLab / WorldForge Runtime run；请求中的 `max_steps`、branch width、rollout horizon / count 都有硬上限；
- `GET /api/runs/{id}`：查询状态；
- `GET /api/runs/{id}/events?after_seq=N`：读取持久事件链；
- `GET /api/runs/{id}/verify`：验证该 run 的事件 hash chain；
- `GET /api/runs/{id}/report`：读取 run report；
- `POST /api/runs/{id}/cancel`：停止当前进程中仍在执行的 Runtime run。

当前**没有** `GET /runs/{id}/stream` 这条 SSE API。产品任务的实时事件使用：

- `WS /ws/conversations/{conversation_id}?after_id=N`：订阅工作台任务事件；断线后可用 `after_id` 从持久事件补放。

主要产品 API 还包括 `/api/conversations`、`/api/assets`、`/api/jobs/{id}`、`/api/messages/{id}/feedback`、`/api/workspace/*`、`/api/metrics`。生产环境默认要求认证，并建议配合外部反向代理 / 分布式限流与外部队列使用。

## Load / safety notes

- `RunConfig.max_steps` 有请求级硬上限；benchmark scenario fan-out 也有限制，防止单请求无限放大工作量。
- 进程内 `RunManager` 只保留有界数量的完成 summary，并释放完成的 `asyncio.Task` 和空订阅队列；持久事件仍是历史状态来源。
- 进程内滑动窗口限流器对跟踪 key 数量设置硬上限，避免高基数身份/IP 造成无限内存增长。
- 这些是单进程保护，不替代生产级网关限流、worker concurrency、队列 backpressure、数据库连接池和容量规划。

## Repository

```text
frontend/                       产品工作台
worldforge/api/                 Runtime / Product API
worldforge/product/             工作空间、协作、任务与证据生命周期
worldforge/runtime/             Frozen Kernel + Self-Evolving Harness
worldforge/envs/                可验证游戏环境 / BalanceLab
scripts/                        E2E 与独立 Harness benchmark
tests/                          Runtime / Product / Promotion / Stress 回归
docs/                           架构、运行与研究说明
```

---

<div align="center">

**CONTROL THE EXECUTION · VERIFY THE RESULT · EVOLVE THE HARNESS**

</div>
