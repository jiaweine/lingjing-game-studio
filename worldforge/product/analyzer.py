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
    def _evidence_confidence(evidence, runtime_result=None):
        """Estimate confidence without treating synthetic simulation as reproduction.

        User-provided artifacts are observed evidence. A WorldForge scenario run is
        synthetic evidence unless a real game execution adapter explicitly marks the
        evidence as reproduced. Synthetic evidence can support hypothesis formation,
        but it must not receive the same confidence boost as real reproduction.
        """
        observed = [
            item for item in evidence
            if item.get("provenance", "observed") == "observed"
        ]
        reproduced = [
            item for item in evidence
            if item.get("provenance") == "reproduced"
        ]
        synthetic = [
            item for item in evidence
            if item.get("provenance") == "synthetic"
        ]

        observed_kinds = {item.get("type") for item in observed}
        diversity = min(.24, max(0, len(observed_kinds) - 1) * .06)
        volume = min(.18, len(observed) * .035)
        reproduced_boost = min(.34, len(reproduced) * .17)
        synthetic_support = min(.08, len(synthetic) * .04)
        return round(
            min(.92, .18 + diversity + volume + reproduced_boost + synthetic_support),
            2,
        )

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
                "provenance": "observed",
                "provenance_label": "用户提供 / 观察证据",
            })

        await sink("progress", {
            "step": "线索对齐",
            "detail": self._progress_detail(intent),
            "percent": 34,
        })

        runtime_result = None
        if intent in {"battle_review", "balance", "regression"}:
            scenario = {
                "battle_review": "boss_burst",
                "balance": "glass_cannon",
                "regression": "loot_exploit",
            }[intent]
            await sink("progress", {
                "step": "模拟复核",
                "detail": "正在内部验证场景中检查相似假设；该结果不代表已在用户真实游戏中复现",
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
                    enable_evolution=bool(human_feedback_gate),
                ),
                demo_delay=0,
                session_meta={
                    "human_feedback_gate": bool(human_feedback_gate),
                    "evidence_provenance": "synthetic",
                },
            )
            runtime_result = summary.model_dump()
            runtime_result["evidence_provenance"] = "synthetic"
            runtime_result["evidence_scope"] = "worldforge-internal-scenario"
            runtime_result["source_scenario"] = scenario
            runtime_result["real_game_reproduction"] = False
            evidence.append({
                "id": f"E{len(evidence) + 1}",
                "type": "simulation",
                "label": "内部模拟复核",
                "title": (
                    f"内部模拟复核 · {summary.steps} 步 · "
                    f"{'命中相似异常路径' if summary.outcome not in {'victory', 'success'} else '模拟结果稳定'}"
                ),
                "provenance": "synthetic",
                "provenance_label": "内部模拟 / 非真实游戏复现",
                "claim_scope": "hypothesis_support_only",
            })

        await sink("progress", {
            "step": "交叉核对",
            "detail": "正在对照用户观察证据与内部模拟结果，寻找相互支持或冲突的线索",
            "percent": 78,
        })

        model_assets = self._model_assets(assets)
        provider = self.providers.choose(provider_key, model_assets)
        generated = None

        # If no full-capability route is available, keep useful visual evidence rather
        # than discarding the entire inference pass because one audio asset is present.
        if provider is None and any(
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
                                "你是游戏研发任务的证据分析器。只根据给定素材、任务历史和复核结果形成结论；"
                                "严格区分 observed（用户观察）、synthetic（内部模拟）、reproduced（真实环境复现）"
                                "和 inferred（模型推断）。synthetic 只能支持假设，不能写成用户真实游戏已复现。"
                                "不要把猜测写成事实；优先给出可执行的下一步验证。"
                            ),
                        },
                        *prior,
                        {
                            "role": "user",
                            "content": self._prompt(
                                text, intent, evidence, runtime_result, history, assets
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

        analysis_mode = "model_assisted" if generated else "demo"
        if not generated:
            await sink("notice", {
                "title": "当前为演示分析",
                "detail": (
                    "本次未调用可用推理模型；输出用于展示分析流程和形成待验证假设，"
                    "不能视为真实游戏结论。"
                ),
            })

        confidence = self._evidence_confidence(evidence, runtime_result)
        modality_counts = Counter(item.get("type", "file") for item in evidence)
        provenance_counts = Counter(
            item.get("provenance", "observed") for item in evidence
        )
        real_game_reproduced = bool(provenance_counts.get("reproduced"))
        claim_status = "hypothesis_only"
        verification_status = "not_verified"
        answer = generated or (
            "### 演示分析（非真实游戏结论）\n\n"
            "本次未调用推理模型；以下内容是基于固定分析模板与内部模拟形成的待验证假设。\n\n"
            + self._demo_answer(intent)
        )
        await sink("progress", {
            "step": "形成结论",
            "detail": "已完成证据归并、来源分层、冲突检查和下一步验证建议",
            "percent": 100,
        })
        return {
            "answer": answer,
            "intent": intent,
            "analysis_mode": analysis_mode,
            "claim_status": claim_status,
            "verification_status": verification_status,
            "evidence": evidence,
            "deliverables": self._deliverables(intent, evidence, runtime_result),
            "runtime": runtime_result,
            "context": {
                "history_messages": len(history),
                "task_assets": len(assets),
                "evidence_confidence": confidence,
                "modality_counts": dict(modality_counts),
                "provenance_counts": dict(provenance_counts),
                "real_game_reproduced": real_game_reproduced,
                "analysis_mode": analysis_mode,
                "claim_status": claim_status,
                "verification_status": verification_status,
                "limitations": [
                    "内部 WorldForge 场景是 synthetic evidence，不代表用户游戏 Build",
                    "结论需要真实游戏执行适配器或人工复现证据后才能升级为 verified",
                ],
            },
            "suggestions": self._suggestions(intent),
        }

    def _deliverables(self, intent, evidence, runtime_result):
        evidence_ids = [item.get("id") for item in evidence if item.get("id")]
        evidence_pack = {
            "type": "evidence_pack",
            "title": "证据包",
            "summary": "保留本次判断使用的用户观察证据与内部模拟证据，并标注来源，方便团队复查。",
            "items": [item.get("title", "") for item in evidence if item.get("title")],
            "evidence_ids": evidence_ids,
        }
        if intent == "battle_review":
            return [
                {
                    "type": "reproduction_card",
                    "title": "问题验证卡",
                    "summary": "把当前异常窗口固化成待真实环境复现的验证入口。",
                    "items": ["使用当前素材作为观察基线", "锁定异常阶段与资源窗口", "接入真实游戏执行环境后按同条件复现"],
                    "evidence_ids": evidence_ids,
                },
                {
                    "type": "regression_checklist",
                    "title": "回归检查清单",
                    "summary": "覆盖触发条件、邻近条件与修复后复核。",
                    "items": ["真实环境确认原触发条件", "相邻时间窗无新增异常", "资源与伤害变化符合预期"],
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
                    "title": "回归验证卡",
                    "summary": "把历史异常、当前观察条件和内部模拟结果放在同一交付物，真实复现状态单独记录。",
                    "items": ["复用当前异常观察条件", "核对版本差异", "在真实游戏构建中重复同条件验证"],
                    "evidence_ids": evidence_ids,
                },
                {
                    "type": "release_checklist",
                    "title": "发布前检查项",
                    "summary": "只有关键路径在真实目标环境重新通过后才适合关闭问题。",
                    "items": ["真实环境原问题不可复现", "关键邻接路径通过", "证据与结果已人工复核"],
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
            "regression": "正在核对版本差异并整理可验证的异常路径",
            "npc": "正在检查角色行为、上下文一致性与异常跳变",
        }.get(intent, "正在拆解问题并寻找最相关的证据")

    def _prompt(self, text, intent, evidence, runtime, history, assets):
        return (
            f"当前研发目标：{text}\n"
            f"任务类型：{intent}\n"
            f"素材证据：\n{self._asset_context(assets)}\n"
            f"证据索引（含 provenance）：{evidence}\n"
            f"内部模拟结果（若存在则 provenance=synthetic，不等于真实游戏复现）：{runtime}\n"
            f"此前已有 {len(history)} 条任务消息。\n\n"
            "输出要求：\n"
            "1. 先给结论，并明确置信度高/中/低。\n"
            "2. 用证据编号说明支持结论的依据，并标注观察、内部模拟或真实复现来源；若素材互相冲突要指出。\n"
            "3. synthetic 内部模拟只能写成假设支持，绝不能表述为用户真实游戏已经复现。\n"
            "4. 给出最可能触发条件，不足以证明的内容标记为待验证。\n"
            "5. 最后给 2-4 个能最大幅度减少不确定性的下一步验证动作。"
        )

    def _demo_answer(self, intent):
        if intent == "battle_review":
            return (
                "### 待验证假设\n当前模板分析更倾向于**高爆发阶段的资源衔接问题**，而非单一伤害数值失控；内部模拟仅支持该假设，尚未在用户真实游戏中复现。\n\n"
                "### 证据\n1. 用户素材显示高风险阶段承伤和资源消耗同时抬升。\n"
                "2. 内部模拟命中了相似关键窗口，但它属于 synthetic evidence。\n\n"
                "### 建议\n优先在真实游戏构建中检查减伤覆盖、敌方爆发参数和技能冷却，并记录可重复输入序列。"
            )
        if intent == "balance":
            return (
                "### 待验证假设\n当前数值可能存在高收益高波动组合；内部模拟用于发现风险边界，不等价于线上或目标 Build 的真实表现。\n\n"
                "### 建议\n优先收窄极端波动，并用目标版本和分层玩家策略重新验证。"
            )
        if intent == "regression":
            return (
                "### 待验证假设\n当前证据提示可能存在回归路径，内部模拟命中了相似异常，但**尚未证明目标游戏版本已稳定复现**。\n\n"
                "### 建议\n锁定版本与配置差异，在真实 Build 中补可重复输入和自动回归用例。"
            )
        if intent == "npc":
            return (
                "### 待验证假设\n角色行为可能存在上下文切换不够平滑的问题。\n\n"
                "### 建议\n优先验证冲突指令和连续多轮交互。"
            )
        return "### 待验证假设\n已完成当前素材整理与问题拆解；尚未形成真实环境结论。可以继续追加素材和追问，不需要重新描述背景。"

    def _suggestions(self, intent):
        return {
            "battle_review": ["把异常时间段单独展开", "继续核对伤害配置", "生成真实环境回归清单"],
            "balance": ["看不同玩家策略的结果", "找出最危险的数值组合", "生成调参建议"],
            "regression": ["整理待真实复现步骤", "对比两个版本配置", "生成发布前检查项"],
            "npc": ["检查角色目标切换", "对比两段对话表现", "生成行为规则建议"],
        }.get(intent, ["继续追问", "补充素材", "生成结论摘要"])
