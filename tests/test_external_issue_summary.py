import pytest

from worldforge.product.external_issue_summary import build_github_issue_summary


def _conversation():
    return {"id": "conv-123", "title": "Boss phase 2 秒杀"}


def _messages(*, verified=False):
    return [
        {
            "role": "user",
            "content": "沿用原条件验证修复。\n\n【验证范围】Build=1.4.7 | Branch=release/1.4 | Commit=abc123",
        },
        {
            "role": "assistant",
            "content": "当前结果：修复后未观察到原问题。@team 请注意回归边界。",
            "payload": {
                "outcome": {
                    "issue_lifecycle": True,
                    "requires_project_verification": True,
                    "state": "verified" if verified else "needs_verifier_decision",
                    "label": "已验证" if verified else "需要验证结论",
                    "verified": verified,
                    "reason": "真实项目执行已完成。" if verified else "仍需独立 Verifier 判定。",
                },
                "evidence": [
                    {
                        "title": "Boss phase transition log",
                        "locator": "/private/runtime/evidence/boss.log",
                    },
                    {
                        "label": "修复后截图",
                        "provenance": {"token": "must-never-leak"},
                    },
                ],
                "context": {
                    "provider_native_token_telemetry": {"secret": 999},
                    "agent_trace": "internal chain",
                },
            },
        },
    ]


def test_reproduction_summary_contains_only_product_safe_fields():
    body = build_github_issue_summary(
        conversation=_conversation(),
        messages=_messages(verified=False),
        push_kind="reproduction",
    )

    assert "Boss phase 2 秒杀" in body
    assert "Build `1.4.7`" in body
    assert "Branch `release/1.4`" in body
    assert "Commit `abc123`" in body
    assert "需要验证结论" in body
    assert "Boss phase transition log" in body
    assert "修复后截图" in body
    assert "/private/runtime" not in body
    assert "must-never-leak" not in body
    assert "provider_native_token_telemetry" not in body
    assert "internal chain" not in body
    assert "@\u200bteam" in body


def test_ci_result_scope_overrides_stale_user_message_scope():
    messages = _messages(verified=True)
    messages[-1]["payload"]["context"]["verification_scope"] = {
        "build_ref": "1.4.8",
        "branch_ref": "fix/boss-phase-2",
        "commit_ref": "d" * 40,
        "environment_ref": "qa",
    }
    messages[-1]["payload"]["context"]["ci_trigger"] = {
        "source": "github_workflow_run",
        "head_sha": "d" * 40,
    }

    body = build_github_issue_summary(
        conversation=_conversation(),
        messages=messages,
        push_kind="verification",
    )

    assert "Build `1.4.8`" in body
    assert "Branch `fix/boss-phase-2`" in body
    assert f"Commit `{'d' * 40}`" in body
    assert "Commit `abc123`" not in body


def test_verification_summary_requires_verifier_authoritative_outcome():
    with pytest.raises(ValueError, match="独立 Verifier"):
        build_github_issue_summary(
            conversation=_conversation(),
            messages=_messages(verified=False),
            push_kind="verification",
        )

    body = build_github_issue_summary(
        conversation=_conversation(),
        messages=_messages(verified=True),
        push_kind="verification",
    )
    assert "修复验证更新" in body
    assert "Verifier-authoritative" in body
    assert "已验证" in body


def test_summary_rejects_non_issue_analysis():
    with pytest.raises(ValueError, match="结构化的 Bug/回归结果"):
        build_github_issue_summary(
            conversation=_conversation(),
            messages=[
                {
                    "role": "assistant",
                    "content": "数值分析完成",
                    "payload": {
                        "outcome": {
                            "issue_lifecycle": False,
                            "state": "analysis_complete",
                        }
                    },
                }
            ],
            push_kind="reproduction",
        )
