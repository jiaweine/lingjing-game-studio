# 灵境客户工作台

前端是零构建依赖的单页游戏研发执行工作台。产品界面围绕**任务、执行、证据、交付、素材、项目记忆和团队**组织，不暴露内部模型选择、供应商选择、算法页面或历史阶段演示页。

## 信息架构

### 顶部

- 当前工作空间与工作空间切换；
- 服务连接状态；
- 素材入口；
- 当前用户入口。

### 左侧

- 新建研发任务；
- 战斗问题复现、数值风险检查、版本回归验证、角色行为检查、多素材交叉核对等任务模板；
- 任务搜索；
- 进行中 / 已归档任务列表。

### 中间

- 当前任务标题和状态；
- 重新执行、重命名、置顶、归档/恢复、永久删除申请、复制任务深链接；
- 持久化审批状态；
- 用户输入、assistant 交付与执行轨迹；
- 真实运行进度和停止控制；
- 多模态任务指令输入。

### 右侧

- **执行**：当前状态、进度、执行记录和后续动作；
- **证据**：截图、关键帧、日志摘录等证据；
- **交付**：复现卡、回归清单、风险清单、验证方案和证据包；
- **素材**：当前任务的持续多模态上下文；
- **记忆**：显式 Project 绑定、待确认 Memory Proposal、跨 build / branch / commit / environment 的当前 Memory Heads、revision history、provenance 与 active / disputed / retracted 治理；
- **团队**：成员、邀请、负责人、质量门和产品指标等协作/治理信息。

## 项目记忆治理

Project Memory 是跨任务连续性先验，不是本轮 Verifier 证据。前端因此把“系统提议记住什么”和“已经成为项目事实/约束什么”明确分开：

- Conversation 未绑定 Project 时显示显式未绑定状态；系统不会根据任务标题、工作空间或相似文本自动猜项目；
- Editor 可以创建 Project 或显式绑定已有 Project；Viewer 只能读取；
- 用户原文满足高精度 durable-memory 规则后，只生成 **pending proposal**；
- pending proposal 在人工批准前不会进入 Project Memory truth store；
- 批准 proposal 时可以选择已有 `memory_key`，从而把“同一事实的新值”形成下一 revision，而不是生成近义重复 key；
- 当前治理视图使用 scope-complete `memory-heads`，会显示 general 与 build / branch / commit / environment 专属 heads，也会显示 disputed / retracted heads；
- 推理检索仍使用 scope shadowing，两种视图语义刻意分离；
- 每条 memory 展示 key、revision、kind、scope、provenance、confidence / importance 和当前 state；
- 用户可以查看精确 scope 下的 revision history；
- active memory 可以标记 disputed 或 retracted；这些状态变化会生成新的 revision，而不是原地覆盖历史；
- 工作空间成员角色可能在页面生命周期中改变，因此 Memory 面板每次刷新都会重新读取当前 principal，不缓存旧 workspace role。

Memory UI 不提供“模型自动记住全部内容”的开关。自动 proposal 和 authoritative memory 是两个不同阶段，后者始终需要明确治理动作。

## 权限语义

前端不会把“隐藏按钮”当成权限控制。服务端会重新校验工作空间成员角色：

- Owner / Admin 可以执行管理和治理动作；
- Member 可以在权限范围内创建和推进任务；
- Viewer 是服务端只读角色，写接口会被拒绝。

成员角色变化会在会话解析时重新从 membership 状态读取，Memory 治理面板刷新时也重新请求当前 principal，避免依赖旧 JWT/UI role cache。

## 任务生命周期

任务状态包含 active、review、waiting_approval、blocked、verified、stopped 等产品语义。

- 执行中可以真实停止；
- 最近一次失败或停止的执行可以安全重试；
- 完成交付后进入待复核；
- 最新交付被人工确认正确且无错误反馈后进入已验证；
- 错误反馈会进入需修正状态；
- 永久删除先进入持久化审批，并锁定任务突变操作；
- 审批拒绝时恢复进入审批前的任务状态。

产品不提供伪造的“任意点暂停/续跑”。如果底层外部推理调用不能保证从任意指令点无损恢复，客户界面只提供真实可保证的停止与安全重试。

## 多模态输入

输入支持图片、视频、音频、日志、配置和通用文件。后续追问即使不重复选择附件，执行上下文仍会继承当前任务已经存在的多模态素材。

客户工作台不会展示供应商/模型选择器。推理资源由服务端根据输入能力需求自动路由。

## 前后端分离开发

后端：

```bash
uvicorn worldforge.api.app:app --host 0.0.0.0 --port 8765
```

前端也可以单独静态托管：

```bash
python -m http.server 5173 --directory frontend
```

访问：

```text
http://127.0.0.1:5173/?api=http://127.0.0.1:8765
```

主任务工作台由 `app.js` 管理；Project Memory 治理由独立的 `memory_panel.js` 管理。两者都使用同源 REST；生产环境应使用明确的 CORS origins、trusted hosts 和 TLS 入口。

## 视觉与交互约束

- light-first 高密度工作台；
- 动效只用于执行、进度、证据和完成等语义反馈；
- 尊重 `prefers-reduced-motion`；
- 不保留无行为的设置、更多、暂停、供应商切换等假控件；
- README 产品截图来自真实浏览器 E2E，不使用手工绘制的伪 UI。

## 浏览器 E2E

主产品闭环：

```bash
python scripts/product_ui_e2e.py
```

覆盖身份入口、工作空间、任务生命周期、上传、运行、停止/重试、实时结果、证据、结构化交付、反馈、团队协作、邀请、产品指标、多模态上下文、归档保护和永久删除审批。

Project Memory 治理闭环：

```bash
python scripts/memory_ui_e2e.py
```

覆盖第六个 Memory tab、未绑定时拒绝项目猜测、显式 Project 绑定、scope-complete heads、pending proposal 批准/拒绝、归入已有 key 形成新 revision、disputed/retracted 状态治理、revision history，以及 owner → viewer 角色变化后的重新授权与写控件移除。

关键检查失败时脚本返回非零退出码。主产品截图使用 1920×1200 viewport 和 device scale 2，生成 3840×2400 PNG，并由 README Gallery workflow 再次校验。
