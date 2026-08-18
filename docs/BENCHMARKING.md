# Benchmarking

## 1. 目的

仓库内 benchmark 用于回答两个不同问题：

1. **Runtime 是否仍然可靠**：执行、验证、恢复、产品生命周期有没有回归；
2. **Harness 是否真的还能自进化**：不是只会生成 candidate，而是能在独立 held-out credit 下晋升新的 generation。

benchmark 是工程验证，不是客户工作台营销榜单。没有 controlled protocol，不声称“性能超过某外部产品”。

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

### 当前独立进程结果

```text
candidate genomes                 36
accepted candidates                4
baseline generation                1
promoted generation                3
train objective gain         +0.004712
sealed held-out gain         +0.000559
paired-bootstrap LCB           0.000000
held-out quality               0.612886
held-out safety                0.966518
held-out efficiency            0.730917
held-out operations               23.25
winning lineage     memory mutation
                    → elite refinement
                    → trust-region minimum edit
```

这组结果只证明：从干净进程、bootstrap Genome 出发，搜索器能够产生被 sealed judge 接受的下一代 Harness。增益很小，因此不能据此宣称通用 SOTA 或大幅性能领先。

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

## 5. Runtime / 产品回归

除了 Harness promotion，CI 继续运行：

- Python compile；
- 完整 pytest；
- JavaScript syntax；
- Backend product E2E；
- Browser product E2E；
- README repository consistency；
- GitHub README 真实浏览器图片加载；
- GitHub MathJax / MathML 公式渲染。

其中 Browser E2E 与 standalone Harness benchmark 是不同证据：前者证明产品交互闭环没有被算法改造破坏；后者证明 Harness generation 机制在干净进程中仍可成功。

## 6. 外部比较规则

如果与任何 Agent / Harness / workflow 系统比较，至少冻结：

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
12. 同一 train / held-out 隔离协议。

Pi、DeerFlow、DeepSeek Agent 生态等可以作为 Harness engineering / integration reference，但不能在不同底座、不同预算或不同环境下拿 README 数字直接横向宣称性能领先。

## 7. 产品指标不是 Runtime Benchmark

产品层另外通过 `product_events` 观察真实任务闭环，例如首次任务完成率、首次交付耗时、中断率、失败率、恢复率、继续执行率、人工介入率、证据打开率、结果采纳率和人工验证反馈率。

这些指标回答“产品是否帮助研发任务完成”，不能和 shadow-arena Harness objective 混成同一套分数。
