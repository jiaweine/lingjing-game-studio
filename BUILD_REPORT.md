# 灵境 · 当前构建与验收报告

## 当前产品状态

灵境当前是一套面向游戏研发任务的执行工作台：用户以工作空间为边界提交研发目标和多模态素材，系统持久化任务、执行、证据、交付、人工反馈、审批与审计状态，并支持停止、重试、交接、归档和受治理的永久删除。

这份报告只描述当前主线能力，不再使用历史阶段版本名作为产品能力基线。

## 已落地的产品能力

- 工作空间身份与多租户数据隔离。
- 注册、登录、工作空间切换、邀请、成员角色与成员管理。
- 任务搜索、重命名、置顶、归档/恢复、负责人交接与深链接。
- Durable Job、真实停止、失败/停止后的安全重试与刷新恢复。
- 图片、视频、音频、日志、配置、文本/文档等多模态素材。
- 后续追问自动继承当前任务已有素材上下文。
- 结构化研发交付：复现卡、回归清单、风险清单、验证方案和证据包。
- 结果反馈与人工质量门：正确性、证据价值、人工验证和备注。
- 永久删除持久化审批；审批期间任务锁定，拒绝时恢复原状态，失败时保留可重试审批状态。
- 产品事件与产品指标：首次交付、中断、失败、恢复、继续执行、人工介入、证据打开、结果采纳与人工验证等。
- 服务端权限、审计记录、Request ID、安全响应头、对象存储边界与健康检查。
- PostgreSQL / SQLite、Local / S3-compatible Object Storage、进程内 / External Worker 部署模式。

## 一致性与事务保障

任务接收阶段将用户消息、排队 Job 与 `message.accepted` 事件在同一事务中提交。

任务完成阶段将 Job 完成状态、最终 assistant 交付与 `answer.ready` 事件在同一事务中提交。

任务停止、重试和永久删除审批都使用持久化状态作为事实源；迟到的失败/成功事件不能覆盖更新的执行状态。永久删除在对象存储清理成功后，将审批校验、数据库删除与删除审计放在同一数据库事务中。

## 当前验收结果

最近一轮完整产品验收结果：

```text
pytest                              45 passed
Python compile check                PASS
node --check frontend/app.js        PASS
Backend product E2E                 PASS
Browser product UI E2E              25 / 25 PASS, errors=[]
README Gallery                      8 / 8 PNG, 3840×2400
Official pull-request CI            PASS
Official main CI                    PASS
README Gallery workflow             PASS
```

产品 E2E 脚本已经带硬失败保护：任何关键检查为 false 时脚本都会返回非零退出码，避免“报告里失败但 CI 仍然绿色”。

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

README Gallery workflow 会验证每张图片存在、可读取、格式为 PNG，分辨率为 3840×2400；界面或截图脚本变化时自动刷新图库。

## 部署边界

仓库已经提供应用级认证、权限、审计、健康检查、数据库迁移、对象存储和 Worker 拓扑，但以下能力仍属于真实生产平台或组织治理职责：

- TLS / ingress / WAF / DDoS；
- centralized rate limiting；
- metrics / tracing / error reporting；
- secret management；
- PostgreSQL backup / PITR；
- object-storage lifecycle / encryption / versioning；
- malware scanning；
- enterprise SSO / MFA / email verification；
- 合规数据保留、导出与组织级删除策略。

这些能力不在产品文档中伪装成仓库已经内置。
