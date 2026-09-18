# 灵境产品策略：从 Agent Runtime 到游戏 Debug Agent

## 1. 当前阶段判断

灵境的 Runtime、治理、证据、记忆和任务生命周期已经超过早期 MVP 所需的底层能力。下一阶段的主要风险不是“功能不够”，而是用户价值没有被压缩成一个足够尖锐、可验证、可量化的购买理由。

因此产品主线从“通用游戏研发 Agent 工作台”收敛为：

> **游戏 Bug 复现、修复验证与回归交付 Agent。**

底层仍保留数值分析、NPC 行为检查、多素材交叉核对、Project Memory、Harness Evolution 等能力，但不再要求新用户先理解这些概念。

## 2. 首要 ICP

### 核心用户

- 游戏 QA / 测试负责人；
- Gameplay / Combat / AI 程序；
- Technical Designer；
- 中小型游戏团队中的研发负责人。

### 高频痛点

1. 偶发 Bug 难稳定复现；
2. 视频、日志、配置、Build 信息分散，人工关联成本高；
3. 修复后需要重新验证，容易漏掉回归路径；
4. Bug 结论缺少可审计证据，研发和 QA 来回沟通；
5. 一个问题跨多个版本、分支和会话后，上下文丢失。

## 3. 核心产品承诺

用户不需要理解 ContextOS、Genome、Frozen Kernel、Counterfactual Search 等内部机制。默认产品体验只承诺三件事：

1. **能复现**：把问题从“偶发”推进到稳定触发条件或明确不可复现边界；
2. **有证据**：关键结论绑定录像片段、截图、日志、配置或验证结果；
3. **能验证**：修复后可按同一条件重新执行并生成回归结果。

对外推荐的一句话描述：

> 上传录像、日志和 Build，让灵境自动复现游戏问题、定位触发条件、验证修复并生成回归清单。

## 4. 默认核心流程

```text
发现 Bug
→ 上传录像 / 日志 / 配置 / Build 信息
→ 自动整理复现目标
→ 搜索稳定触发条件
→ 输出复现卡 + Evidence
→ 人工确认
→ 关联修复版本 / 分支
→ 自动重跑验证
→ 输出前后对比 + 回归清单
→ 关闭或继续修正
```

### 默认用户状态

底层仍可保留完整状态机，但客户端默认只展示四类语义：

- **执行中**：系统正在复现、分析或验证；
- **需要确认**：需要用户确认问题、证据或修复版本；
- **已验证**：目标已经经过明确验证；
- **需处理**：执行失败、证据不足、修复无效或存在阻塞。

高级治理状态继续保留在审计和管理视图中。

## 5. Activation 目标

新用户第一次进入产品后，目标是在 5 分钟内完成：

1. 创建工作空间；
2. 上传一个视频、日志或截图；
3. 使用“复现 Bug”模板启动任务；
4. 看见第一条结构化执行进度；
5. 理解最终会拿到“复现条件 + Evidence + 回归清单”。

如果没有真实 Engine Adapter，也必须允许用户先通过静态多模态分析体验这条流程，并清晰标注“尚未进入引擎执行”。

## 6. 产品信息架构原则

### 默认层

新用户默认只需要看到：

- 任务；
- 执行进度；
- 证据；
- 结果；
- 素材。

### 高级层

以下能力不作为第一屏概念：

- Project Memory proposal / revision / identity suggestion；
- Harness Genome / promotion；
- Frozen Kernel 内部状态；
- provider / model routing；
- 详细审计与权限治理。

它们应该由管理员、研发负责人或高级用户按需展开。

## 7. Value Proof

产品进入对外推广前，至少需要 3 个真实游戏项目 Case Study。每个案例都必须同时记录人工 baseline 和灵境结果。

建议指标：

| 指标 | 定义 |
|---|---|
| Time to first reproducible condition | 从素材提交到第一条可执行复现条件的时间 |
| Reproduction success rate | 已知可复现问题中，系统成功找到稳定条件的比例 |
| Evidence acceptance rate | QA / 开发认可证据足以支撑结论的比例 |
| Fix verification time | 修复版本提交到验证结论的时间 |
| Regression coverage | 自动生成并实际执行/确认的回归路径覆盖情况 |
| Human touch time | 人工真正投入的操作和分析时间 |
| Cost per verified issue | 每个已验证问题的模型、执行和基础设施成本 |

禁止用 synthetic benchmark 替代真实项目 ROI 表述。Synthetic benchmark 继续作为机制和 correctness 证据。

## 8. P0 Roadmap

### P0-A：真实 Engine Activation

目标：Unity 用户可以在不理解协议的情况下接入。

验收：

- Unity Package / SDK；
- 登录或项目绑定；
- Test Connection；
- capability 检查；
- dry-run；
- 截图 / 日志 / snapshot evidence 回传；
- README 中有真实项目接入视频或截图；
- 至少一个真实项目 E2E。

Unreal 在 Unity 完成真实验证后再复制产品模式，避免两条集成线同时摊薄资源。

### P0-B：Bug → Fix → Regression 闭环

验收：

- 任务可标记为 Bug；
- 可绑定 build / branch / commit；
- 结果明确区分“复现成功 / 证据不足 / 无法复现”；
- 用户可提交“修复版本”；
- 一键按原复现条件重跑；
- 输出 before / after evidence；
- 自动生成回归清单。

### P0-C：真实 ROI Telemetry

每次任务记录：

- wall-clock duration；
- model/provider cost；
- engine action count；
- retry / rollback 次数；
- human intervention time（可人工补录）；
- 最终验证结果。

产品层提供 workspace 聚合指标，不把内部 Harness 指标当客户价值指标。

## 9. P1 Roadmap

### 工作流集成

优先级：

1. GitHub Issue / PR；
2. Jira 或 Linear；
3. CI build webhook；
4. Slack / 飞书通知。

目标不是“多一个连接器”，而是让结果回到团队真正关闭 Bug 的地方。

### 运行预算与可控性

任务启动前显示：

- 预计时间区间；
- 预计执行深度；
- 预计成本区间；
- 是否允许真实引擎写操作。

运行时提供：

- 当前消耗；
- 剩余预算；
- 快速结束；
- 继续深挖。

## 10. 商业化准备

建议未来包装：

- **Community**：本地 / 单人 / 自带 provider；
- **Team**：共享 workspace、长期任务、Evidence、协作、基础集成；
- **Enterprise**：SSO/MFA、审计导出、数据保留、私有部署、集中策略、合规能力。

商业化之前不应通过增加 Runtime feature 来掩盖 SSO、备份、可观测性、限流和数据治理等生产平台缺口。

## 11. 明确非目标

下一阶段默认不做：

- 为展示技术先进性增加新的 Harness 概念；
- 同时覆盖所有游戏研发角色；
- 在没有真实项目数据时宣称效率提升百分比；
- 把 Memory governance 暴露给所有普通用户；
- 把 synthetic benchmark 包装成客户生产效果。

## 12. 北极星指标

建议北极星指标：

> **Weekly Verified Issues（每周完成明确复现或修复验证、并带可接受 Evidence 的问题数）**

辅助指标：

- 首次成功任务率；
- Time to Verified Issue；
- 7/28 天团队留存；
- 重复使用同一 Project 的比例；
- 每个 Verified Issue 的人工时间与系统成本；
- 从任务结果进入外部 Issue/PR/CI 闭环的比例。

这套指标能迫使产品围绕“真实研发问题是否被更快关闭”优化，而不是围绕功能数量或 Agent 内部复杂度优化。
