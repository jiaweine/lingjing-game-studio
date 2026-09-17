# GitHub CI 自动修复验证

灵境可以在一个 Bug/回归任务已经关联 GitHub Issue 和 PR/Commit 后，监听指定 GitHub Actions workflow 的成功事件，并自动创建一轮正常的“修复验证”执行。

这个能力默认关闭。它不会自动关闭 GitHub Issue、修改 label/assignee，也不会绕过灵境的 Verifier。

## 前置条件

1. 在灵境任务中绑定 GitHub Issue。
2. 为同一关联补充 PR 或 Commit 上下文。
3. 服务端配置 `WORLDFORGE_GITHUB_TOKEN`，用于读取 GitHub PR/Commit 以及显式 Push 评论。
4. 服务端配置一个独立的高熵 `WORLDFORGE_GITHUB_WEBHOOK_SECRET`。
5. 在 GitHub 仓库 Webhook 设置中，将 Payload URL 指向：

   `https://<your-lingjing-host>/integrations/github/webhook`

6. Content type 使用 `application/json`，Secret 使用与 `WORLDFORGE_GITHUB_WEBHOOK_SECRET` 完全相同的值。
7. 只订阅 `Workflow runs` 事件即可。

## 在灵境中开启

在任务右侧 GitHub Issue 卡中：

1. 先确认已经显示 PR 或 Commit。
2. 在 `CI 自动验证` 区域填写 **exact workflow name**，例如 `Build Game`。
3. 点击“开启”。

灵境只会在以下条件全部成立时创建自动验证任务：

- Webhook HMAC 签名有效；
- GitHub event 为 `workflow_run`；
- action 为 `completed`；
- conclusion 为 `success`；
- repository 与绑定 Issue 的 repository 一致；
- workflow name 与任务中配置的 exact name 完全一致；
- workflow `head_sha` 与绑定 PR head SHA 或选定 Commit SHA 完全一致；
- 该关联仍处于 CI 自动验证开启状态；
- 开启自动验证的成员仍具有编辑权限；
- 任务未归档，且没有另一轮执行正在进行。

## 自动执行会复用什么

自动验证通过现有产品 job 模型创建，不使用另一套执行器。它会：

- 复用上一轮 provider；
- 复用上一轮素材上下文；
- 刷新当前会话 history snapshot；
- 复用 project context；
- 将 `commit_ref` 替换为 CI event 的完整 40 位 `head_sha`；
- PR 路由存在时同步 `branch_ref`；
- 在 job payload、task event 和 audit 中记录 GitHub workflow/run/SHA/delivery 来源。

## 不会发生的事情

CI 成功本身 **不等于 Bug 已修复**。

自动执行产出的结论仍受 issue-lifecycle outcome 和项目级 Verifier 约束。没有真实项目证据或独立验证时，灵境不能仅因为 GitHub Actions 是绿色就把结果升级为 `verified`。

灵境也不会因为自动验证成功而自动关闭 GitHub Issue。最终向 GitHub Push 验证结论仍是显式动作。

## 幂等与故障边界

- GitHub Webhook `X-GitHub-Delivery` 会持久化去重，同一个 delivery 不会重复排队。
- GitHub Issue 评论 Push 使用 Lingjing 隐藏 marker；如果 GitHub 已成功写评论但本地 comment id 写入失败，重试会先恢复并 PATCH 自己原来的评论，而不是再 POST 一条。
- Webhook 原始 payload、GitHub token 和 webhook secret 都不会写入任务数据。

## 当前边界

当前版本只支持 GitHub.com，并固定使用 GitHub 官方 API。GitHub Enterprise、Jira/Linear、CI artifact 自动注入和自动关闭 Issue 不在这一片实现范围内。
