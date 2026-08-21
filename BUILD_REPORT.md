# 灵境 · 当前构建与验收报告

## 当前产品状态

灵境是一套面向游戏研发任务的 **Self-Evolving Agent Harness Runtime + 执行工作台**。用户以工作空间为边界提交研发目标和多模态素材，系统持久化任务、执行、证据、交付、人工反馈、审批与审计状态；WorldForge 负责可恢复执行，并在 Frozen Kernel 之外让 Harness 自己搜索更合适的工作方式。

这份报告只描述当前主线/候选主线能力，不使用历史阶段版本名作为产品能力基线。

## Harness 架构

产品 Runtime 分成：

- **Frozen Kernel**：canonical state、checkpoint、Sandbox、Verifier、rollback / replan、事件链、sealed evaluation 与 atomic promotion；
- **Evolvable Harness Genome**：representation、Belief、Memory、Skill、Specialist topology、Planner fusion、counterfactual budget、risk utility、mutation policy；
- **Product Control Plane**：workspace、job、asset、evidence、deliverable、approval、feedback、audit 与 product events。

Bootstrap 行为 prior 只存在 `default_harness_genome.json`。Planner / Specialist / Skill / Memory / Counterfactual 通过统一 Genome 解释执行，不再维护失败类别 → 固定 Skill delta、特定标签 → 固定 probe、低血量 → 固定 Specialist 等 Python 任务策略表。

## Harness self-evolution

当前搜索链：

```text
verified trace
→ policy-agnostic reflection
→ WHERE × WHY semantic archive
→ true antithetic mutation
→ behavior plateau detection
→ stable train elites
→ topology / gate / skill / memory / parameter refinement
→ minimum effective edit
→ freeze search
→ sealed held-out judge
→ Pareto / QD credit
→ atomic promotion / reject
```

进化器不能修改 Verifier、held-out split、evaluation protocol、quality/safety floor 或 promotion transaction。

## 独立 promotion 验证

永久命令：

```bash
python scripts/harness_evolution_benchmark.py
```

协议：`runtime-trace-sealed-heldout-game-harness-2026-08`。机器可校验摘要：`docs/benchmark-results.json`。

已验证的干净进程结果：

```text
Candidate genomes                  52
Passed promotion gate               6
Baseline generation                 1
Promoted generation                 4
Train objective gain          +0.004712
Sealed held-out gain          +0.044598
Paired-bootstrap LCB            0.000000
Held-out quality                0.686283
Held-out safety                 0.966518
Held-out efficiency             0.730917
Held-out operations                23.25
Winning lineage      Parameter jitter
                     → refinement
                     → minimum boundary α=0.2188
```

这些数字是 self-evolution mechanism proof，不是跨产品 SOTA 宣称。README 明确保留这个边界。

## 已落地的产品能力

- 工作空间身份与多租户数据隔离；
- 注册、登录、工作空间切换、邀请、角色与成员管理；
- 任务搜索、重命名、置顶、归档/恢复、负责人交接与深链接；
- Durable Job、可续约 lease / heartbeat、崩溃自动回队列、fencing token 防陈旧写入、真实停止与安全重试；
- 图片、视频、音频、日志、配置、文本/文档等多模态素材；
- 后续追问继承当前任务已有素材上下文；
- 结构化复现卡、回归清单、风险清单、验证方案和 evidence pack；
- observed / synthetic / reproduced 来源分层，演示输出与真实复现结论显式隔离；
- 结果反馈与人工质量门；
- 永久删除持久化审批与任务锁定；
- 产品事件与闭环指标；
- 服务端权限、审计、Request ID、安全响应头、对象存储与健康检查；
- PostgreSQL / SQLite、Local / S3-compatible Object Storage、进程内 / External Worker 部署模式。

## 一致性与事务保障

任务接收阶段将用户消息、queued Job 与 `message.accepted` 同事务提交。任务完成阶段将 Job 完成状态、assistant 交付与 `answer.ready` 同事务提交。

停止、重试和永久删除审批都使用持久化状态作为事实源；Job 每次领取生成唯一 fencing token，Worker 以 heartbeat 续约，过期后自动回队列，迟到的旧失败/成功事件不能覆盖新 attempt 或更新状态。永久删除在对象存储清理成功后，将审批校验、数据库删除与删除审计置于同一数据库事务。

用户素材、内部模拟和真实游戏适配器输出分别标为 observed、synthetic 与 reproduced。无模型回退明确标记为 demo / hypothesis_only；模型生成也不会绕过真实环境复现断言而升级为 verified。人工验证待验证假设时必须填写真实 Build、条件与结果说明，前端与服务端均执行该门禁。

Harness promotion 同样是持久化原子替换：只有被 sealed held-out 评估过的同一个 Genome 才能成为 active generation。

## 验收门禁

正式 CI 当前要求：

```text
Python compile                              PASS required
Full pytest                                 PASS required
Alembic upgrade/downgrade round trip         PASS required
PostgreSQL multi-worker lease integration    PASS required
Production Compose contract                  PASS required
Standalone Harness self-evolution benchmark PASS required
JavaScript syntax                           PASS required
Backend product E2E                         PASS required
Browser full-stack API/Job/WebSocket E2E    PASS required
Browser gallery interaction E2E             PASS required on gallery refresh
README repository consistency               PASS required
README real image loading                    9 / 9 required
README display math → MathML                18 / 18 required
README MathJax macro / parse errors          0 required
```

`product_backend_e2e.py` 与 `product_ui_e2e.py` 对关键 false 结果硬失败，避免“日志显示失败但进程返回 0”。Harness benchmark 在没有 candidate 晋升、held-out gain 为负或 bootstrap LCB 为负时同样返回非零。

## README Gallery

当前 README 使用 8 张真实浏览器产品截图：

- `auth.png`
- `workspace-empty.png`
- `upload.png`
- `task-running.png`
- `workspace.png`
- `evidence.png`
- `multimodal.png`
- `cover.png`

`cover.png` 在 README 顶部和图库各出现一次，因此浏览器门禁检查 9 个 image node。每个 Release asset 与仓库副本都是 3840×2400 PNG。Gallery workflow 从真实产品状态采集、逐个发布 stable Release asset，并重新打开 GitHub README 验证实际像素加载。

## 部署边界

仓库提供应用级认证、权限、审计、健康检查、数据库迁移、对象存储、带租约的 Worker 拓扑和单机 Compose 共享 Runtime volume，但没有把以下真实生产平台职责伪装成已经完成：

- TLS / ingress / WAF / DDoS；
- centralized rate limiting；
- metrics / tracing / error reporting；
- secret management；
- PostgreSQL backup / PITR；
- 多主机共享 Runtime / Harness Store；
- object-storage lifecycle / encryption / versioning；
- malware scanning；
- enterprise SSO / MFA / email verification；
- 合规数据保留、导出与组织级删除策略。

当前仓库验收不等于生产部署验收。
