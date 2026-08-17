# Benchmarking

## 1. 目的

仓库内 benchmark 用于验证 WorldForge Runtime 的工程增益与回归，不作为客户工作台的营销榜单。

评测时应冻结同一环境、同一策略先验、同一 observation、同一 action schema 和同一预算，避免通过更换底座能力制造“Runtime 更强”的假结论。

当前本地消融包含：

1. `Policy`
2. `Policy + Planner`
3. `Policy + Verification`
4. `WorldForge Runtime`

完整 Runtime 在同一策略先验基础上增加候选规划、反事实分支、验证与恢复能力。

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

`decision_ops` 用来显式记录额外规划/试演成本，避免只展示成功率而隐藏计算代价。

## 2. 可复现结果

benchmark 结果应以实际生成的输出文件为准，不在 README 或客户界面里硬编码成长期宣传数字。代码、场景或策略变化后需要重新运行，不把旧结果继续当成当前事实。

## 3. 对外比较规则

如果要与任何外部 agent / harness / workflow 系统比较性能，至少冻结：

1. 同一模型或策略 checkpoint；
2. 同一系统提示与策略提示；
3. 同一游戏环境版本；
4. 同一 observation；
5. 同一 action / tool schema；
6. 同一权限；
7. 同一 token / compute / tool budget；
8. 同一最大环境步数；
9. 同一 seeds；
10. 同一 timeout / retry policy；
11. 同一评分代码。

没有 controlled protocol，就不声称“性能超过某外部产品”。

客户工作台也不展示内部消融名称、竞争产品名称或模型供应商名称；benchmark 属于工程验证材料。

## 4. 产品指标不是 Runtime Benchmark

产品层另外通过 `product_events` 观察真实任务闭环，例如：

- 首次任务完成率；
- 首次交付耗时；
- 中断率；
- 失败率；
- 恢复率；
- 继续执行率；
- 人工介入率；
- 证据打开率；
- 结果采纳率；
- 人工验证反馈率。

这些指标回答“产品是否帮助研发任务完成”，不能和 Runtime 环境成功率混成同一套分数。
