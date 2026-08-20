from __future__ import annotations

import asyncio
from collections import Counter

from worldforge.models import RunConfig
from worldforge.providers import ProviderError


class ProductAnalyzer:
    def __init__(self, engine, providers):
        self.engine = engine
        self.providers = providers

    def intent(self, text, assets, history=None):
        context = "\n".join(
            str(message.get("content", "")) for message in (history or [])[-8:]
        ) + "\n" + text
        if any(key in context for key in ["录像", "视频", "战斗", "掉血", "伤害", "boss", "Boss"]):
            return "battle_review"
        if any(key in context for key in ["数值", "平衡", "build", "Build", "胜率", "难度"]):
            return "balance"
        if any(key in context for key in ["回归", "版本", "bug", "异常", "复现"]):
            return "regression"
        if any(key in context for key in ["npc", "NPC", "角色", "行为", "剧情"]):
            return "npc"
        if any(str(asset.get("mime", "")).startswith("video/") for asset in assets):
            return "battle_review"
        return "general"

    @staticmethod
    def _model_assets(assets):
        """Convert task assets into inference-ready evidence without changing originals."""
        rows = []
        for asset in assets:
            mime = str(asset.get("mime", ""))
            meta = asset.get("meta", {}) or {}
            if mime.startswith("image/"):
                rows.append(asset)
                continue
            if mime.startswith("audio/"):
                rows.append(asset)
                continue
            if mime.startswith("video/"):
                for index, frame in enumerate(meta.get("keyframes", [])[:8]):
                    rows.append({
                        "id": f"{asset.get('id', 'video')}:frame:{index}",
                        "name": f"{asset.get('name', '录像')} · 关键帧 {index + 1}",
                        "mime": "image/jpeg",
                        "path": frame,
                        "meta": {
                            "kind": "image",
                            "source_asset_id": asset.get("id"),
                            "source_kind": "video",
                        },
                    })
        return rows

    @staticmethod
    def _asset_context(assets):
        lines = []
        for index, asset in enumerate(assets[:12], start=1):
            meta = asset.get("meta", {}) or {}
            kind = meta.get("kind", "file")
            line = f"E{index} | {kind} | {asset.get('name', '未命名素材')}"
            if meta.get("duration"):
                line += f" | {meta['duration']}s"
            if meta.get("width") and meta.get("height"):
                line += f" | {meta['width']}x{meta['height']}"
            lines.append(line)
            preview = str(meta.get("preview", "")).strip()
            if preview:
                lines.append(f"E{index} 内容摘录:\n{preview[:3000]}")
        return "\n".join(lines) or "没有额外素材"

    @staticmethod
    def _evidence_confidence(evidence):
        """Describe evidence richness, not truth of the model's conclusion."""
        kinds = {item.get("type") for item in evidence}
        diversity = min(.24, max(0, len(kinds) - 1) * .06)
        volume = min(.18, len(evidence) * .035)
        return round(min(.60, .18 + diversity + volume), 2)

    async def run(self, *, text, assets, provider_key, sink, history=None, human_feedback_gate=False):
        history = history or []
        intent = self.intent(text, assets, history)
        detail = f"已关联 {len(assets)} 份任务素材"
        if history:
            detail += f"、{len(history)} 条历史消息"
        await sink("progress", {
            "step": "素材解析",
            "detail": detail + "，正在读取多模态上下文",
            "percent": 12,
        })
        await asyncio.sleep(.04)

        evidence = []
        for index, asset in enumerate(assets, start=1):
            meta = asset.get("meta", {}) or {}
            kind = meta.get("kind", "file")
            label = {
                "image": "截图",
                "video": "录像",
                "audio": "音频",
                "text": "日志 / 配置",
            }.get(kind, "文件")
            title = asset.get("name", "")
            if meta.get("duration"):
                title += f" · {meta['duration']}s"
            if meta.get("width") and meta.get("height"):
                title += f" · {meta['width']}×{meta['height']}"
            evidence.append({
                "id": f"E{index}",
                "type": kind,
                "label": label,
                "title": title,
                "asset_id": asset.get("id"),
            })

        await sink("progress", {
            "step": "线索对齐",
            "detail": self._progress_detail(intent),
            "percent": 34,
        })

        model_assets = self._model_assets(assets)
        provider = self.providers.choose(provider_key, model_assets)
        generated = None
        runtime_result = None

        # Demo mode may exercise the internal Harness, but that BalanceLab run is not a
        # replay of the user's game, so it must never become user evidence or increase
        # evidence confidence. It is also forbidden from evolving the global Harness.
        if provider_key == "demo" and intent in {"battle_review", "balance", "regression"}:
            scenario = {
                "battle_review": "boss_burst",
                "balance": "glass_cannon",
                "regression": "loot_exploit",
            }[intent]
            await sink("progress", {
                "step": "Harness 自检",
                "detail": "正在运行内置 BalanceLab 示例场景；该结果不会被当作用户素材复现证据",
                "percent": 58,
            })
            summary = await self.engine.run(
                RunConfig(
                    scenario_id=scenario,
                    seed=29,
                    max_steps=12,
                    branch_width=3,
                    rollout_horizon=2,
                    rollouts_per_branch=2,
                    enable_evolution=False,
                ),
                demo_delay=0,
                session_meta={
                    "source": "product_demo_self_check",
                    "user_evidence": False,
                    "human_feedback_gate": bool(human_feedback_gate),
                },
            )
            runtime_result = {
                **summary.model_dump(),
                "scope": "internal_balance_lab",
                "user_evidence": False,
            }

        await sink("progress", {
            "step": "交叉核对",
            "detail": (
                "正在对照图像、录像关键帧、声音和日志，寻找相互支持或冲突的证据"
                if provider
                else "已完成素材索引；当前没有可用推理资源，不会把元数据或内置示例场景写成事实结论"
            ),
            "percent": 78,
        })

        # If no full-capability route is available, keep useful visual evidence rather
        # than discarding the entire inference pass because one audio asset is present.
        if provider is None and provider_key != "demo" and any(
            str(asset.get("mime", "")).startswith("audio/") for asset in model_assets
        ):
            visual_assets = [
                asset for asset in model_assets
                if not str(asset.get("mime", "")).startswith("audio/")
            ]
            provider = self.providers.choose(provider_key, visual_assets)
            if provider:
                model_assets = visual_assets
                await sink("notice", {
                    "title": "部分声音内容暂未进入推理",
                    "detail": "当前仍会结合音频时长与其他素材完成分析；配置本地全模态推理后可直接理解声音内容。",
                })

        if provider:
            try:
                prior = [
                    {"role": message.get("role"), "content": str(message["content"])[:6000]}
                    for message in history[-8:]
                    if message.get("role") in {"user", "assistant"} and message.get("content")
                ]
                generated = await provider.chat(
                    messages=[
                        {
                            "role": "system",
                            "content": (
                                "你是游戏研发任务的证据分析器。只根据给定素材和任务历史形成结论；"
                                "区分观察、推断和待验证项；不要把猜测写成事实；优先给出可执行的下一步验证。"
                            ),
                        },
                        *prior,
                        {
                            "role": "user",
                            "content": self._prompt(
                                text, intent, evidence, history, assets
                            ),
                        },
                    ],
                    assets=model_assets,
                )
            except ProviderError as exc:
                await sink("notice", {
                    "title": "推理资源暂不可用",
                    "detail": str(exc),
                })

        grounded = bool(generated)
        confidence = self._evidence_confidence(evidence)
        modality_counts = Counter(item.get("type", "file") for item in evidence)
        await sink("progress", {
            "step": "形成结论",
            "detail": (
                "已完成基于当前素材的证据归并、冲突检查和下一步验证建议"
                if grounded
                else "已完成素材整理，但当前证据没有经过可用推理 Provider；结论保持为待验证"
            ),
            "percent": 100,
        })
        return {
            "answer": generated or self._unavailable_answer(
                intent, assets, provider_key, runtime_result
            ),
            "intent": intent,
            "evidence": evidence,
            "deliverables": self._deliverables(
                intent, evidence, runtime_result, grounded=grounded
            ),
            "runtime": runtime_result,
            "context": {
                "history_messages": len(history),
                "task_assets": len(assets),
                "evidence_confidence": confidence,
                "modality_counts": dict(modality_counts),
                "analysis_grounded": grounded,
                "provider_requested": provider_key,
            },
            "suggestions": self._suggestions(intent, grounded=grounded),
        }

    def _deliverables(self, intent, evidence, runtime_result, *, grounded=True):
        evidence_ids = [item.get("id") for item in evidence if item.get("id")]
        evidence_pack = {
            "type": "evidence_pack",
            "title": "证据包",
            "summary": "保留本次任务的素材索引，方便团队复查；内部 BalanceLab 自检不计入用户证据。",
            "items": [item.get("title", "") for item in evidence if item.get("title")],
            "evidence_ids": evidence_ids,
        }
        if not grounded:
            return [
                {
                    "type": "validation_plan",
                    "title": "待验证计划",
                    "summary": "当前没有可用的素材推理结论；先补齐推理能力或可执行复现环境，再决定问题性质。",
                    "items": [
                        "配置与素材类型匹配的推理 Provider",
                        "保留当前素材作为验证基线",
                        "对关键触发条件做可重复的真实环境验证",
                    ],
                    "evidence_ids": evidence_ids,
                },
                evidence_pack,
            ]
        if intent == "battle_review":
            return [
                {
                    "type": "reproduction_card",
                    "title": "问题复现卡",
                    "summary": "把当前异常窗口固化成可交接的复现入口。",
                    "items": ["使用当前素材作为复现基线", "锁定异常阶段与资源窗口", "修复后按同条件再次执行"],
                    "evidence_ids": evidence_ids,
                },
                {
                    "type": "regression_checklist",
                    "title": "回归检查清单",
                    "summary": "覆盖触发条件、邻近条件与修复后复核。",
                    "items": ["原触发条件不再复现", "相邻时间窗无新增异常", "资源与伤害变化符合预期"],
                    "evidence_ids": evidence_ids,
                },
                evidence_pack,
            ]
        if intent == "balance":
            return [
                {
                    "type": "risk_register",
                    "title": "数值风险清单",
                    "summary": "把高波动组合与需要继续验证的边界分开记录。",
                    "items": ["优先复核极端收益组合", "检查资源曲线断点", "覆盖不同玩家策略"],
                    "evidence_ids": evidence_ids,
                },
                {
                    "type": "tuning_plan",
                    "title": "调参验证方案",
                    "summary": "每次调整都保留前后对照和回归条件。",
                    "items": ["一次只改变一个主要变量", "保留调整前基线", "重新检查极端与常规打法"],
                    "evidence_ids": evidence_ids,
                },
                evidence_pack,
            ]
        if intent == "regression":
            return [
                {
                    "type": "reproduction_card",
                    "title": "回归复现卡",
                    "summary": "把历史异常、当前复现条件和验证基线放在同一交付物。",
                    "items": ["复用当前异常条件", "核对版本差异", "修复后重复同条件验证"],
                    "evidence_ids": evidence_ids,
                },
                {
                    "type": "release_checklist",
                    "title": "发布前检查项",
                    "summary": "只有关键路径重新通过后才适合关闭问题。",
                    "items": ["原问题不可复现", "关键邻接路径通过", "证据与结果已人工复核"],
                    "evidence_ids": evidence_ids,
                },
                evidence_pack,
            ]
        if intent == "npc":
            return [
                {
                    "type": "behavior_checklist",
                    "title": "角色行为检查表",
                    "summary": "将目标切换、连续交互和上下文一致性变成可重复检查项。",
                    "items": ["连续交互保持上下文", "冲突指令下行为可解释", "目标切换没有异常跳变"],
                    "evidence_ids": evidence_ids,
                },
                evidence_pack,
            ]
        return [
            {
                "type": "action_brief",
                "title": "研发行动摘要",
                "summary": "把当前结论转成可继续执行和复核的团队任务。",
                "items": ["保留当前基线", "补齐最高不确定性证据", "完成后再次核验结论"],
                "evidence_ids": evidence_ids,
            },
            evidence_pack,
        ]

    def _progress_detail(self, intent):
        return {
            "battle_review": "正在对齐异常时间段、关键受击、画面变化与资源变化",
            "balance": "正在比较高风险组合、资源曲线与胜负边界",
            "regression": "正在核对版本差异和可验证的复现条件",
            "npc": "正在检查角色行为、上下文一致性与异常跳变",
        }.get(intent, "正在拆解问题并寻找最相关的证据")

    def _prompt(self, text, intent, evidence, history, assets):
        return (
            f"当前研发目标：{text}\n"
            f"任务类型：{intent}\n"
            f"素材证据：\n{self._asset_context(assets)}\n"
            f"证据索引：{evidence}\n"
            f"此前已有 {len(history)} 条任务消息。\n\n"
            "输出要求：\n"
            "1. 先给结论，并明确置信度高/中/低。\n"
            "2. 用证据编号说明支持结论的依据；若素材互相冲突要指出。\n"
            "3. 给出最可能触发条件，不足以证明的内容标记为待验证。\n"
            "4. 最后给 2-4 个能最大幅度减少不确定性的下一步验证动作。"
        )

    def _unavailable_answer(self, intent, assets, provider_key, runtime_result):
        asset_note = (
            f"已登记并解析 {len(assets)} 份素材的可用元数据。"
            if assets
            else "当前任务没有附加素材。"
        )
        demo_note = ""
        if provider_key == "demo" and runtime_result:
            demo_note = (
                "\n\n### Harness 自检\n"
                "已运行内置 BalanceLab 示例场景，用于检查 Harness 流程是否能执行。"
                "这个结果不是你的游戏环境、不是同条件复现，也不作为用户素材证据。"
            )
        return (
            "### 当前结论\n"
            "**证据不足，暂不形成游戏事实结论。**\n\n"
            f"{asset_note} 当前没有可用、且与这些素材能力匹配的推理 Provider，"
            "因此系统不会根据任务类型自动写出 Boss 爆发、数值失衡、回归复现或 NPC 行为异常等具体判断。"
            f"{demo_note}\n\n"
            "### 下一步\n"
            "配置可用的文本/多模态 Provider，或接入能真实执行你的游戏版本与测试条件的复现环境后，再基于证据形成结论。"
        )

    def _suggestions(self, intent, *, grounded=True):
        if not grounded:
            return ["配置可用推理 Provider", "补充可验证的复现条件", "继续整理素材证据"]
        return {
            "battle_review": ["把异常时间段单独展开", "继续核对伤害配置", "生成回归测试清单"],
            "balance": ["看不同玩家策略的结果", "找出最危险的数值组合", "生成调参建议"],
            "regression": ["查看复现步骤", "对比两个版本配置", "生成发布前检查项"],
            "npc": ["检查角色目标切换", "对比两段对话表现", "生成行为规则建议"],
        }.get(intent, ["继续追问", "补充素材", "生成结论摘要"])
