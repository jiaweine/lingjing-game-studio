from __future__ import annotations

from typing import Any

_SCOPE_PREFIX = "【验证范围】"
_ALLOWED_PUSH_KINDS = {"reproduction", "verification"}


def _compact(value: Any, limit: int = 2000) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def _safe_markdown_text(value: Any, limit: int = 2000) -> str:
    # Avoid turning model/user text into accidental GitHub mentions while preserving readable
    # customer-facing content. This function intentionally does not include raw HTML.
    return _compact(value, limit).replace("@", "@\u200b")


def _parse_scope(text: str) -> dict[str, str] | None:
    line = next(
        (
            row.strip()
            for row in str(text or "").splitlines()
            if row.strip().startswith(_SCOPE_PREFIX)
        ),
        None,
    )
    if not line:
        return None
    scope = {"build_ref": "", "branch_ref": "", "commit_ref": ""}
    for item in line[len(_SCOPE_PREFIX) :].split("|"):
        raw_key, separator, raw_value = item.partition("=")
        if not separator:
            continue
        key = raw_key.strip().lower()
        value = _compact(raw_value, 200)
        if key == "build":
            scope["build_ref"] = value
        elif key == "branch":
            scope["branch_ref"] = value
        elif key == "commit":
            scope["commit_ref"] = value
    return scope if any(scope.values()) else None


def _latest_issue_result(messages: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, str] | None]:
    current_scope: dict[str, str] | None = None
    latest: tuple[dict[str, Any], dict[str, str] | None] | None = None
    for message in messages:
        if message.get("role") == "user":
            current_scope = _parse_scope(str(message.get("content") or "")) or current_scope
            continue
        if message.get("role") != "assistant":
            continue
        payload = dict(message.get("payload") or {})
        outcome = payload.get("outcome")
        if not isinstance(outcome, dict):
            continue
        if not bool(
            outcome.get("issue_lifecycle")
            or outcome.get("requires_project_verification")
        ):
            continue
        latest = (message, dict(current_scope) if current_scope else None)
    if latest is None:
        raise ValueError("当前任务还没有结构化的 Bug/回归结果；请先重新执行一次问题任务")
    return latest


def _scope_line(scope: dict[str, str] | None) -> str:
    if not scope:
        return "未绑定 Build / Branch / Commit"
    parts: list[str] = []
    if scope.get("build_ref"):
        parts.append(f"Build `{_safe_markdown_text(scope['build_ref'], 160)}`")
    if scope.get("branch_ref"):
        parts.append(f"Branch `{_safe_markdown_text(scope['branch_ref'], 200)}`")
    if scope.get("commit_ref"):
        parts.append(f"Commit `{_safe_markdown_text(scope['commit_ref'], 160)}`")
    return " · ".join(parts) if parts else "未绑定 Build / Branch / Commit"


def build_github_issue_summary(
    *,
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    push_kind: str,
) -> str:
    push_kind = str(push_kind or "").strip().lower()
    if push_kind not in _ALLOWED_PUSH_KINDS:
        raise ValueError("不支持的 GitHub 推送类型")

    message, scope = _latest_issue_result(messages)
    payload = dict(message.get("payload") or {})
    outcome = dict(payload.get("outcome") or {})
    if push_kind == "verification" and not bool(outcome.get("verified")):
        raise ValueError("最终验证结论尚未通过独立 Verifier，不能推送为已验证修复")

    outcome_label = _safe_markdown_text(outcome.get("label") or outcome.get("state") or "需要确认", 160)
    outcome_state = _safe_markdown_text(outcome.get("state") or "unknown", 80)
    reason = _safe_markdown_text(outcome.get("reason") or "", 600)
    final_answer = _safe_markdown_text(message.get("content") or "", 2200)
    evidence = payload.get("evidence")
    evidence = evidence if isinstance(evidence, list) else []
    evidence_names: list[str] = []
    for item in evidence[:8]:
        if not isinstance(item, dict):
            continue
        label = _safe_markdown_text(
            item.get("title") or item.get("label") or item.get("kind") or "证据",
            160,
        )
        if label:
            evidence_names.append(label)

    title = _safe_markdown_text(conversation.get("title") or "Lingjing issue task", 240)
    conversation_id = _safe_markdown_text(conversation.get("id") or "", 96)
    heading = "修复验证更新" if push_kind == "verification" else "问题复现更新"
    verification_text = (
        "Verifier-authoritative"
        if bool(outcome.get("verified"))
        else "尚未形成 Verifier 权威结论"
    )

    lines = [
        f"<!-- lingjing:{conversation_id}:{push_kind} -->",
        f"## Lingjing · {heading}",
        "",
        f"- **任务**：{title}",
        f"- **版本范围**：{_scope_line(scope)}",
        f"- **当前结论**：{outcome_label} (`{outcome_state}`)",
        f"- **验证强度**：{verification_text}",
    ]
    if reason:
        lines.append(f"- **结论边界**：{reason}")

    lines.extend(["", "### 用户可见结果", final_answer or "暂无可发布结果摘要。"])
    lines.extend(["", "### 关联证据"])
    if evidence_names:
        lines.extend(f"- {name}" for name in evidence_names)
        if len(evidence) > len(evidence_names):
            lines.append(f"- 另有 {len(evidence) - len(evidence_names)} 条证据保留在 Lingjing 工作台")
    else:
        lines.append("- 当前结果没有可安全外发的证据名称；详细证据仍保留在 Lingjing 工作台。")

    lines.extend(
        [
            "",
            "> 此评论由 Lingjing 的显式推送动作生成，只包含用户可见结果、版本范围、结构化验证结论与证据名称；不包含 Agent 内部 trace、provider telemetry 或本地文件路径。",
        ]
    )
    return "\n".join(lines).strip()
