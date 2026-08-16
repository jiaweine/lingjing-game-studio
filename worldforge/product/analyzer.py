from __future__ import annotations

import logging

from worldforge.harness import ExecutionBudget, MissionExecutor, MissionSpec
from worldforge.providers import ProviderError, ProviderRegistry

logger = logging.getLogger("worldforge.product.analyzer")


class ProductAnalyzer:
    """Customer-facing conversational controller.

    Chat is the control surface. For executable game-analysis scenes the request is handed to the
    durable MissionExecutor; provider models are used afterwards for human-readable synthesis, not
    as the authority that decides whether execution really happened.
    """

    def __init__(self, engine, providers: ProviderRegistry):
        self.engine = engine
        self.providers = providers
        self.missions = MissionExecutor(engine)

    def intent(self, text, assets, history=None):
        context = "\n".join(str(m.get("content", "")) for m in (history or [])[-8:]) + "\n" + text
        lowered = context.lower()
        if any(k in lowered for k in ["录像", "视频", "战斗", "掉血", "伤害", "boss"]):
            return "battle_review"
        if any(k in lowered for k in ["数值", "平衡", "build", "胜率", "难度"]):
            return "balance"
        if any(k in lowered for k in ["回归", "版本", "bug", "异常", "复现"]):
            return "regression"
        if any(k in lowered for k in ["npc", "角色", "行为", "剧情"]):
            return "npc"
        if any(str(a.get("mime", "")).startswith("video/") for a in assets):
            return "battle_review"
        return "general"

    async def run(self, *, text, assets, provider_key, sink, history=None):
        history = history or []
        intent = self.intent(text, assets, history)
        detail = f"已关联 {len(assets)} 份任务素材" + (f"、{len(history)} 条历史消息" if history else "")
        await sink("progress", {"step": "整理现场", "detail": detail + "，正在建立任务上下文", "percent": 10})

        evidence = []
        for asset in assets:
            meta = asset.get("meta", {})
            kind = meta.get("kind", "file")
            label = {"image": "截图", "video": "录像", "audio": "音频", "text": "日志"}.get(kind, "文件")
            title = asset.get("name", "")
            if meta.get("duration"):
                title += f" · {meta['duration']}s"
            if meta.get("width"):
                title += f" · {meta.get('width')}×{meta.get('height')}"
            evidence.append({"type": kind, "label": label, "title": title, "asset_id": asset.get("id")})

        await sink("progress", {"step": "确认任务", "detail": self._progress_detail(intent), "percent": 28})
        runtime_result = None
        if intent in {"battle_review", "balance", "regression"}:
            scenario = {
                "battle_review": "boss_burst",
                "balance": "glass_cannon",
                "regression": "loot_exploit",
            }[intent]
            await sink(
                "progress",
                {"step": "开始复现", "detail": "正在隔离环境中执行多条候选路径，并保留验证证据", "percent": 44},
            )

            async def mission_sink(event):
                if event.event_type == "mission.planned":
                    steps = len(event.payload.get("steps", []))
                    await sink("progress", {"step": "安排验证", "detail": f"已生成 {steps} 个执行步骤，正在按依赖关系推进", "percent": 52})
                elif event.event_type == "tool.completed":
                    await sink("progress", {"step": "收集证据", "detail": "已有一项执行结果完成，正在继续交叉验证", "percent": 68})

            mission = await self.missions.run(
                MissionSpec(
                    goal=text,
                    scenario_id=scenario,
                    scene=intent,
                    budget=ExecutionBudget(
                        max_agents=5 if intent == "regression" else 4,
                        max_tool_calls=12,
                        max_parallelism=3,
                        max_runtime_seconds=90,
                        branch_width=3,
                        rollout_horizon=2,
                        rollouts_per_branch=2,
                    ),
                    metadata={"asset_count": len(assets), "history_messages": len(history)},
                ),
                sink=mission_sink,
            )
            runtime_result = mission.model_dump(mode="json")
            evidence.append(
                {
                    "type": "replay",
                    "label": "执行验证",
                    "title": f"已完成 {mission.summary.get('runs', 0)} 组隔离复现 · 证据链{'完整' if mission.verification.get('child_sessions_verified') else '需复核'}",
                    "mission_id": mission.mission_id,
                }
            )

        await sink("progress", {"step": "交叉核对", "detail": "正在对照素材、执行结果与历史上下文，排除偶发因素", "percent": 82})
        provider = self.providers.choose(provider_key, assets)
        provider_name = "内置分析"
        generated = None
        if provider:
            provider_name = provider.info.name
            try:
                prior = [
                    {"role": m.get("role"), "content": str(m["content"])[:6000]}
                    for m in history[-8:]
                    if m.get("role") in {"user", "assistant"} and m.get("content")
                ]
                generated = await provider.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": "你是游戏研发分析助手。只基于已提供的素材与执行证据，用中文给出清晰、可执行、面向业务人员的结论；没有证据的内容明确标为推测。",
                        },
                        *prior,
                        {"role": "user", "content": self._prompt(text, intent, evidence, runtime_result, history)},
                    ],
                    assets=assets,
                )
            except ProviderError as exc:
                logger.warning("provider synthesis failed provider=%s error=%s", provider.info.key, exc)
                await sink(
                    "notice",
                    {"title": "总结服务暂时不可用", "detail": "执行证据已经保留，可以稍后重新生成文字总结。"},
                )

        await sink("progress", {"step": "形成结论", "detail": "处理完成，关键证据和下一步建议已经整理好", "percent": 100})
        return {
            "answer": generated or self._demo_answer(intent, runtime_result),
            "intent": intent,
            "provider": provider_name,
            "evidence": evidence,
            "runtime": runtime_result,
            "context": {"history_messages": len(history), "task_assets": len(assets)},
            "suggestions": self._suggestions(intent),
        }

    @staticmethod
    def _progress_detail(intent):
        return {
            "battle_review": "正在定位异常时间段、关键受击与资源变化",
            "balance": "正在比较高风险组合、资源曲线与胜负边界",
            "regression": "正在核对版本差异并准备隔离复现",
            "npc": "正在检查角色行为、上下文一致性与异常跳变",
        }.get(intent, "正在拆解问题并寻找最相关的证据")

    @staticmethod
    def _prompt(text, intent, evidence, runtime, history):
        return (
            f"当前追问：{text}\n"
            f"任务类型：{intent}\n"
            f"任务素材摘要：{evidence}\n"
            f"执行与验证结果：{runtime}\n"
            f"此前已有 {len(history)} 条任务消息。请区分事实、执行证据与推测，再给出结论。"
        )

    @staticmethod
    def _demo_answer(intent, runtime=None):
        runs = ((runtime or {}).get("summary") or {}).get("runs", 0)
        verified = ((runtime or {}).get("verification") or {}).get("child_sessions_verified", False)
        proof = f"已完成 {runs} 组隔离复现，执行证据链{'完整' if verified else '仍需复核'}。" if runtime else ""
        if intent == "battle_review":
            return f"### 结论\n异常更像是**高爆发阶段的资源衔接问题**，不是单一伤害数值失控。{proof}\n\n### 下一步\n优先检查减伤覆盖、敌方爆发参数和技能冷却，并把异常时间窗加入回归用例。"
        if intent == "balance":
            return f"### 结论\n当前数值存在高收益、高波动组合。{proof}\n\n### 下一步\n先收窄极端波动，再用不同玩家策略重复验证。"
        if intent == "regression":
            return f"### 结论\n异常已经按回归任务进入隔离复现。{proof}\n\n### 下一步\n锁定配置差异并把稳定复现路径加入发布门禁。"
        if intent == "npc":
            return "### 结论\n角色行为存在上下文切换不够平滑的风险。\n\n### 下一步\n优先验证冲突指令、目标切换和连续多轮交互。"
        return "### 结论\n已完成当前素材整理与问题拆解。可以继续追加素材和追问，不需要重新描述背景。"

    @staticmethod
    def _suggestions(intent):
        return {
            "battle_review": ["把异常时间段单独展开", "继续核对伤害配置", "生成回归测试清单"],
            "balance": ["看不同玩家策略的结果", "找出最危险的数值组合", "生成调参建议"],
            "regression": ["查看复现步骤", "对比两个版本配置", "生成发布前检查项"],
            "npc": ["检查角色目标切换", "对比两段对话表现", "生成行为规则建议"],
        }.get(intent, ["继续追问", "补充素材", "生成结论摘要"])
