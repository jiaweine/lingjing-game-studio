# 灵境游戏工作台 v2.0 · 构建与验收报告

## 目标

本轮把上一版“可演示的对话式分析工作台”升级为可部署的 SaaS 基础架构，同时继续保持无 API Key 的本地开发体验。

## 新增生产能力

- Argon2 密码哈希、HttpOnly Session / Bearer JWT。
- Workspace 多租户数据边界与跨租户 404 防越权。
- SQLAlchemy 产品数据层，开发 SQLite / 生产 PostgreSQL。
- Alembic schema migration。
- Local / S3(MinIO) Object Storage。
- Durable `analysis_jobs` 与独立 Worker。
- DB-backed task events，使 API / Worker 可跨进程部署。
- Audit Trail、Request ID、CSP、安全响应头、Trusted Host、CORS 白名单和基础限流。
- Liveness / Readiness；Readiness 实际检查数据库与对象存储。
- Production Auth UI 与 Workspace 身份展示。
- Production Dockerfile 与 PostgreSQL + MinIO + Migration + API + Worker Compose 拓扑。

## 验收结果

```text
pytest                           16 passed
node --check frontend/app.js     PASS
python -m compileall worldforge  PASS
Alembic fresh DB migration       PASS
Production auth smoke            PASS
Backend product E2E              7 / 7 PASS
Browser product UI E2E           7 / 7 PASS, 0 page errors
Responsive viewport audit        7 / 7 PASS, 0 px overflow
README local image references    5 / 5 PASS
```

Production smoke 使用 `WORLDFORGE_ENV=production`、`WORLDFORGE_AUTH_MODE=required` 和迁移后的全新数据库实际启动服务，并验证：

- 未登录 `/api/conversations` → 401；
- 注册 → 创建真实 User + Workspace + owner membership；
- HttpOnly Session 后可创建 Workspace-scoped Conversation；
- `/api/health/ready` → database / object_storage 均通过。


## UI v3 · Taste redesign

本轮继续对产品界面和 README 做视觉重构，采用 `design-taste-frontend` 的 redesign audit 思路，但针对本项目属于高密度工作台而不是营销 landing page 做了适配。

设计读数：

```text
Design variance   6 / 10
Motion intensity  4 / 10
Visual density    7 / 10
```

主要变化：

- 保留现有信息架构、DOM ID 和 API 交互，不为换视觉破坏产品行为；
- 紫色仅作为品牌、选中态和进度强调，移除大面积通用 AI 渐变；
- 欢迎页从等权 2×2 卡片改为有主次的不对称任务入口；
- 左侧任务导航、中间对话画布、右侧证据检查器重新拉开视觉层级；
- Auth Gate 改为冷中性工具型品牌面板；
- 统一圆角、边框、阴影和交互反馈；
- 增加 `prefers-reduced-motion` 处理；
- 小字号关键文本做对比度提升；
- README 移除 Mermaid / sequence diagram，使用当前代码真实截图作为主要视觉内容。

响应式额外验证了 1440、1200、1160、1024、768、720、390 px 七组 viewport，横向溢出均为 0 px。测试过程中发现并修复了约 1024 px 宽度下右侧检查器导致主画布被裁切的问题。

## 仍属于部署平台职责的部分

企业 SSO/MFA、邮件验证、WAF/DDoS、共享全局限流、Prometheus/OTel、备份/PITR、对象存储生命周期、上传文件病毒扫描、合规删除/导出策略仍需要按实际运行环境接入。README 已明确列入 Production Gate，不把这些能力虚构为仓库内置。
