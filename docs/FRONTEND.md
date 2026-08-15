# 中文展示控制台

前端零构建依赖，面向客户演示和技术面试两种场景设计。

## 页面

1. **指挥中心**：业务任务、世界状态、自研模型先验、候选未来、递归专家、Verifier、QA 发现。
2. **决策空间**：Model → Planner → Branch → Verifier → Canonical Commit 决策瀑布，候选动作分数与未来树。
3. **轨迹回放**：事件搜索、payload inspector、hash、checkpoint time-travel。
4. **策略演进**：Failure Attribution、Skill Patch、Regression Gate、Versioned Skill Bank、Population Self-Play。
5. **评测报告**：同 WorldForge-M1 的 Harness 消融，不混用不同模型。
6. **技术架构**：模型、Runtime、安全、环境四层，以及行业能力面定位矩阵。

## 前后端分离

后端：

```bash
uvicorn worldforge.api.app:app --host 0.0.0.0 --port 8765
```

前端：

```bash
python -m http.server 5173 --directory frontend
```

访问：

```text
http://127.0.0.1:5173/?api=http://127.0.0.1:8765
```

`app.js` 会自动把 REST 和 WebSocket 指向 `api` 参数指定的后端；FastAPI 已启用演示环境 CORS。

## 离线交互演示

`scripts/ui_e2e_pro.py` 会从**真实 Runtime 运行轨迹**生成 `outputs/WorldForge_Interactive_Demo.html`。这个 HTML 不依赖服务器，可用于直接查看完整交互和演示故事线。
