<div align="center">

# 灵境 · Lingjing

### Verifiable, stateful game R&D agent runtime

**让 Agent 在同一个研发工作空间里接收目标与多模态素材，保持长期上下文与受治理的项目记忆，执行任务、验证结果、保留证据，并在受控边界内改进自己的 Harness。**

`STATEFUL RUNTIME` · `MULTIMODAL` · `GOVERNED MEMORY` · `VERIFIABLE` · `RECOVERABLE` · `SELF-EVOLVING HARNESS`

<p>
  <a href="#-快速开始"><b>快速开始</b></a> ·
  <a href="#-产品能力"><b>产品能力</b></a> ·
  <a href="#-系统架构"><b>系统架构</b></a> ·
  <a href="#-工作台"><b>工作台</b></a> ·
  <a href="#-文档"><b>文档</b></a>
</p>

![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-Runtime-009688?logo=fastapi&logoColor=white)
![Version](https://img.shields.io/badge/version-1.0.0-6C63FF)
![License](https://img.shields.io/badge/license-Apache--2.0-blue)

</div>

![灵境游戏研发执行工作台总览](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/cover.png)

---

## 🌌 灵境是什么

灵境是一个面向游戏研发任务的 Agent Runtime 与协作工作台。它把**研发目标、素材、长期任务状态、项目记忆、世界状态、执行事件、验证结果、证据与恢复路径**放进同一条持久任务轨迹，让一次任务可以被持续观察、停止、恢复、复核和迭代。

核心执行链路：

> **Goal → Assets → ContextOS / Project Memory → State → Agent Decision → Counterfactual Search → Canonical Execution → Verification → Evidence → Delivery → Harness Evolution**

模型负责推理；Product Control Plane 负责身份、任务、ContextOS、Project Memory 与治理；Harness 负责把 Skill、Memory、Specialist、Planner 与搜索策略组织成可运行的研发流程；Frozen Kernel 负责真实状态、执行、验证与晋升边界。

---

## ✨ 产品能力

| 能力 | 说明 |
|---|---|
| **有状态执行** | 任务状态与事件持续保存，支持停止、恢复、rollback 与 replan |
| **长期 ContextOS** | 将 TaskState、历史检索、Project Memory 与 EvidenceControl 编译成 bounded context，避免长期任务退化成简单 last-N 对话 |
| **受治理的项目记忆** | Project ↔ Conversation 显式绑定；proposal、revision、scope、撤回、争议与审批全部可审计，未批准建议不会自动成为项目事实 |
| **多模态研发上下文** | 图片、视频、音频、日志、配置和文档可以进入同一个 workspace，并被后续任务继续使用 |
| **可验证结果** | 关键结论可以回到截图、关键帧、日志、finding 与 Verifier 结果；memory/retrieval 不会自动升级为验证真相 |
| **反事实搜索** | 在 clone world 中比较候选路径，再选择进入 canonical execution 的动作 |
| **可进化 Harness** | Representation、Skill、runtime Memory、Specialist topology、Planner fusion、搜索预算与 mutation policy 都可以进入 Genome |
| **受控外部引擎边界** | Unity/Unreal/custom bridge 通过短时 ticket 与 replay protection 接入，engine observation 仍需独立 Frozen Kernel verification |
| **完整任务生命周期** | 身份、协作、搜索、归档、审批删除、反馈、任务事件和 memory governance 统一进入产品层 |

---

## 🧭 系统架构

灵境保持三个明确的权威边界：

1. **Product Control Plane**：workspace、任务、消息、素材、ContextOS、Project Memory、审批与审计；
2. **Evolvable Harness**：决定如何组织 Skill、runtime Memory、Specialist、Planner 与搜索策略；
3. **Frozen Kernel**：拥有 canonical state、安全、Verifier、真实动作提交和 Harness promotion gate。

```mermaid
flowchart TB
    UI["Browser Workspace"] --> CONTROL["Product Control Plane"]
    CONTROL --> STORE["Messages / Assets / Jobs / Projects / Governed Memory"]
    STORE --> CONTEXT["ContextOS + Multimodal Evidence"]
    CONTEXT --> ENGINE["SelfEvolvingWorldForgeEngine"]

    subgraph HARNESS["EVOLVABLE HARNESS"]
        REP["Representation"]
        SKILL["Skills"]
        MEM["Runtime Memory"]
        SPEC["Specialist Topology"]
        PLAN["Planner Fusion"]
        SEARCH["Counterfactual Search Policy"]
        MUT["Mutation Policy"]
    end

    subgraph KERNEL["FROZEN KERNEL"]
        CHECK["Checkpoint + State Ownership"]
        SANDBOX["Sandbox"]
        CANON["Canonical Environment"]
        VERIFY["Independent Verifier"]
        ADAPTER["Governed GameAdapter Boundary"]
        EVENTS["Audit / Event Chain"]
        PROMOTE["Sealed Evaluation + Promotion"]
    end

    ENGINE --> REP
    REP --> SKILL
    REP --> MEM
    SKILL --> SPEC
    MEM --> PLAN
    SPEC --> PLAN
    PLAN --> SEARCH
    SEARCH --> CHECK
    CHECK --> SANDBOX --> CANON --> VERIFY --> EVENTS
    CHECK --> ADAPTER
    ADAPTER --> VERIFY
    VERIFY -->|continue / rollback / replan| ENGINE

    EVENTS --> REFLECT["Trace Reflection"]
    REFLECT --> EVOLVE["Candidate Search + Refinement"]
    MUT --> EVOLVE
    EVOLVE --> PROMOTE
    PROMOTE -->|accepted generation| HARNESS
```

### 权威边界

| Product / ContextOS | Evolvable Harness | Frozen Kernel |
|---|---|---|
| Workspace authorization | Feature representation / normalization | Canonical state ownership |
| Task / message / asset lifecycle | Belief 与 uncertainty 参数 | Checkpoint / rollback |
| Governed Project Memory | Skill gate / bias / reliability | Sandbox 与 invariant verification |
| Bounded long-horizon context | Runtime memory kernel / recency | Independent Verifier |
| Multimodal retrieval / evidence planning | Specialist topology / action features | Real action submission |
| Proposal / approval / audit | Planner fusion / epistemic control | Sealed evaluation / promotion |
| Provider-aware context budget | Counterfactual budget / risk utility | Atomic promotion / lineage |

Harness 可以改变**怎么工作**；ContextOS 可以改变**模型看到哪些经过治理的上下文**；但二者都不能改写**谁拥有真实状态、什么算安全、谁负责验证、什么条件允许晋升**。

更完整的实现说明见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。

---

## 🖥️ 工作台

下面的截图来自实际产品界面，覆盖从身份入口、素材输入到执行、证据与结果的完整路径。

| 身份与新任务 | 素材与执行 |
|---|---|
| **01 · 身份入口**<br><br>![登录与工作空间入口](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/auth.png) | **02 · 新任务**<br><br>![空任务工作台](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/workspace-empty.png) |
| **03 · 多模态素材**<br><br>![多模态素材上传](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/upload.png) | **04 · 执行中**<br><br>![任务执行状态](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/task-running.png) |
| **05 · 任务结果**<br><br>![任务结果工作台](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/workspace.png) | **06 · 证据核验**<br><br>![证据核验](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/evidence.png) |
| **07 · 持续上下文**<br><br>![多模态任务上下文](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/multimodal.png) | **08 · 产品总览**<br><br>![产品总览](https://github.com/jiaweine/lingjing-game-studio/releases/download/readme-gallery-assets/cover.png) |

前端结构与页面说明见 [`docs/FRONTEND.md`](docs/FRONTEND.md)。

---

## 🧬 Harness Evolution

Harness Evolution 使用运行轨迹中的 evidence 产生候选 Genome，在独立 shadow arena 中搜索与比较，并通过 held-out gate 决定是否晋升新的 generation。

```mermaid
flowchart LR
    TRACE["Verified Traces"] --> REFLECT["Reflection"]
    REFLECT --> ARCHIVE["WHERE × WHY Archive"]
    ARCHIVE --> CANDIDATE["Candidate Genomes"]
    CANDIDATE --> TRAIN["Train-only Shadow Arena"]
    TRAIN --> REFINE["Search / Refinement"]
    REFINE --> FREEZE["Freeze Candidate"]
    FREEZE --> HELDOUT["Sealed Held-out Evaluation"]
    HELDOUT -->|pass| PROMOTE["Atomic Promotion"]
    HELDOUT -->|reject| ARCHIVE
```

任务开始时固定一个明确 Harness generation；即使其他 worker 在任务期间完成新的 promotion，当前任务也不会中途切换 phenotype。

README 只保留机制概览；算法、评估协议与研究背景分别见：

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- [`docs/BENCHMARKING.md`](docs/BENCHMARKING.md)
- [`docs/RESEARCH_NOTES.md`](docs/RESEARCH_NOTES.md)

---

## 🎮 适合的工作

- 战斗与 Boss 机制复现、极端 Build 风险检查；
- 经济、成长、掉落与奖励循环异常分析；
- 截图 / 视频 / 日志 / 配置的跨模态证据汇总；
- 长任务执行、停止、重试、rollback 与 replan；
- 跨 Conversation 的 Project Memory、状态更新、版本隔离与工作流持续上下文；
- 结构化复现卡、风险清单、回归清单和 evidence pack；
- 基于真实失败轨迹搜索更合适的 Skill、runtime Memory、Specialist 与反事实策略。

---

## 🚀 快速开始

### 1. 启动产品

```bash
git clone https://github.com/jiaweine/lingjing-game-studio.git
cd lingjing-game-studio
pip install -r requirements.txt
uvicorn worldforge.api.app:app --reload
```

打开：`http://127.0.0.1:8000`

> 运行环境要求 Python `>= 3.11`。

### 2. 开发与测试

```bash
pip install -r requirements-dev.txt
pytest -q
```

### 3. 运行独立验证

```bash
python scripts/harness_evolution_benchmark.py
python scripts/memory_benchmark.py
python scripts/memory_identity_benchmark.py
python scripts/context_adversarial_benchmark.py
python scripts/product_backend_e2e.py
```

Synthetic / deterministic benchmark 只证明对应 correctness/protocol floor。真实 held-out、live retrieval、PostgreSQL、provider latency/cost 或 Unity/Unreal 项目证据按 [`docs/EXTERNAL_EVIDENCE_RUNBOOK.md`](docs/EXTERNAL_EVIDENCE_RUNBOOK.md) 单独执行。

---

## 🔌 Runtime API

| Endpoint | 用途 |
|---|---|
| `POST /runs` | 启动 WorldForge 任务 |
| `GET /runs/{id}` | 查询任务状态 |
| `GET /runs/{id}/events` | 读取持久事件链 |
| `GET /runs/{id}/stream` | 通过 SSE 订阅实时事件 |
| `POST /runs/{id}/cancel` | 停止运行中的任务 |

Product workspace、Project Memory 与治理 API 由同一 FastAPI control plane 提供，并始终进行 workspace/actor authorization；它们不是 Frozen Runtime API 的旁路写入口。

---

## 🗂️ Repository Map

```text
frontend/                       产品工作台与 Memory governance UI
worldforge/api/                 Runtime / Product API
worldforge/product/             工作空间、协作、任务、素材与产品生命周期
worldforge/context/             ContextOS、Project Memory、identity、evidence 与 token budget
worldforge/integrations/        Governed external integration boundaries / GameAdapter
worldforge/runtime/             Frozen Kernel + Evolvable Harness
worldforge/providers/           Provider routing / native token safety
worldforge/envs/                可验证游戏环境 / BalanceLab
services/multimodal_retriever/  Optional semantic multimodal coordinator/workers
migrations/                     Production schema evolution
benchmarks/                     Frozen benchmark contracts / private-corpus scaffolds
scripts/                        E2E、benchmark、corpus 与 conformance entrypoints
tests/                          Runtime / Product / Context / Memory / integration 回归
docs/                           架构、前端、评估、协议、运维与研究说明
```

---

## 📚 文档

| 文档 | 内容 |
|---|---|
| [`ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Product Control Plane、ContextOS、Memory、Harness、Frozen Kernel 与 GameAdapter 权威边界 |
| [`BENCHMARKING.md`](docs/BENCHMARKING.md) | Harness / Memory / Identity / Context / Multimodal benchmark 与证据分层 |
| [`LONG_HORIZON_MODEL_BENCHMARK.md`](docs/LONG_HORIZON_MODEL_BENCHMARK.md) | baseline-last8 ↔ ContextOS 模型级 held-out 协议 |
| [`MULTIMODAL_QUALITY_BENCHMARK.md`](docs/MULTIMODAL_QUALITY_BENCHMARK.md) | game-rd-mm-v1 corpus 与 live retrieval quality protocol |
| [`GAME_ADAPTER_PROTOCOL.md`](docs/GAME_ADAPTER_PROTOCOL.md) | Unity / Unreal / custom bridge 的 Frozen-Kernel-governed contract |
| [`EXTERNAL_EVIDENCE_RUNBOOK.md`](docs/EXTERNAL_EVIDENCE_RUNBOOK.md) | 真实 held-out / GPU / PostgreSQL / provider / engine 证据执行与升级规则 |
| [`FRONTEND.md`](docs/FRONTEND.md) | 工作台结构、交互与前端实现 |
| [`RUNBOOK.md`](docs/RUNBOOK.md) | 本地运行、部署与运维说明 |
| [`RESEARCH_NOTES.md`](docs/RESEARCH_NOTES.md) | 研究背景、参考方法与实现映射 |

---

## 📦 当前版本

当前仓库版本为 **v1.0.0**。README 聚焦第一版产品当前已经实现的能力；synthetic correctness 分数、真实外部实验、研究出处与工程细节统一放在 `docs/`，避免首页把 CI smoke 写成产品质量或 SOTA 声明。

项目使用 **Apache License 2.0**，见 [`LICENSE`](LICENSE)。

---

<div align="center">

**CONTROL THE EXECUTION · VERIFY THE RESULT · EVOLVE THE HARNESS**

</div>
