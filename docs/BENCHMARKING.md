# Benchmarking

## 1. 本地同模型 Harness 消融

`worldforge.benchmarks.game_eval` 用 BalanceLab 做 Runtime 回归测试。四个方案**全部固定同一个 WorldForge-M1**：

1. `M1 直接策略`
2. `M1 + Planner`
3. `M1 + Verifier`
4. `WorldForge Harness`

完整 Harness 只增加 Counterfactual Search 与已有的验证恢复能力，不通过更换底座模型制造优势。

```bash
python -m worldforge.cli benchmark --seeds 12
```

记录：

- success rate
- environment score
- average steps
- invalid action rate
- recovery rate
- decision operations

`decision_ops` 用来提醒读者：更高成功率不是免费的，反事实试演会增加推理/模拟成本。

## 2. 当前可复现实测

仓库中的 `outputs/local_benchmark.json` 使用 4 个 BalanceLab 场景、每场景 12 个 seed，共 48 episode / variant。结果以文件内容为准，不在文档里硬编码成对外宣传数字。

## 3. 与市场 Harness 的正确对标方式

产品能力面可以比较：插件、工具循环、会话历史、Sandbox、Sub-Agent、World State、环境 Fork、QA Probe、Self-Play、Evolution Gate 等是否为原生能力。

性能则必须冻结：

1. 同一模型及 checkpoint；
2. 同一系统提示与策略提示；
3. 同一游戏环境版本；
4. 同一 observation；
5. 同一 action/tool schema；
6. 同一权限；
7. 同一 token / compute / tool budget；
8. 同一最大环境步数；
9. 同一 seeds；
10. 同一 timeout / retry policy；
11. 同一评分代码。

没有这种 controlled protocol，就不在简历或演示里声称“性能超过某外部产品”。WorldForge 当前前端的“行业能力面定位”也明确标注为**能力定位，不是性能榜单**。
