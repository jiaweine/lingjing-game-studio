from worldforge.product import ConversationStore
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID


def _store(tmp_path):
    return ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )


def test_product_metrics_include_verified_issue_value_signals(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        title="Boss 偶发秒杀",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    answer = store.add_message(
        conversation["id"],
        "assistant",
        "真实项目 Verifier 已确认修复。",
        payload={
            "context": {
                "provider_selected_key": "provider-test",
                "provider_selected_model": "model-test",
                "provider_native_token_telemetry": {
                    "native_token_estimated_text_tokens": 120,
                    "native_token_count_input_tokens": 128,
                },
            },
            "outcome": {
                "issue_lifecycle": True,
                "requires_project_verification": True,
                "state": "verified",
                "label": "已验证",
                "verified": True,
            },
        },
        workspace_id=DEMO_WORKSPACE_ID,
    )

    store.upsert_feedback(
        workspace_id=DEMO_WORKSPACE_ID,
        user_id=DEMO_USER_ID,
        message_id=answer["id"],
        verdict="correct",
        evidence_useful=True,
        human_verified=True,
        note="人工确认 Verifier 结论与证据一致",
    )

    metrics = store.product_metrics(workspace_id=DEMO_WORKSPACE_ID)

    assert metrics["verified_issue_count"] == 1
    assert metrics["weekly_verified_issues"] == 1
    assert metrics["median_time_to_verified_issue_seconds"] is not None
    assert metrics["provider_usage_result_count"] == 1
    assert metrics["provider_usage_exact_token_coverage_rate"] == 1.0
    assert metrics["provider_estimated_input_tokens"] == 120
    assert metrics["provider_exact_input_tokens"] == 128
    assert metrics["provider_keys_observed"] == ["provider-test"]
    assert metrics["provider_models_observed"] == ["model-test"]
    assert metrics["cost_available"] is False
    assert metrics["total_provider_cost_usd"] is None
    assert metrics["cost_per_verified_issue_usd"] is None


def test_human_approval_does_not_turn_insufficient_evidence_into_verified_issue(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        title="录像提示异常但没有真实项目复现",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    answer = store.add_message(
        conversation["id"],
        "assistant",
        "当前分析正确地指出证据还不够。",
        payload={
            "outcome": {
                "issue_lifecycle": True,
                "requires_project_verification": True,
                "state": "insufficient_evidence",
                "label": "证据不足",
                "verified": False,
                "reason": "尚无真实项目独立验证执行。",
            }
        },
        workspace_id=DEMO_WORKSPACE_ID,
    )

    store.upsert_feedback(
        workspace_id=DEMO_WORKSPACE_ID,
        user_id=DEMO_USER_ID,
        message_id=answer["id"],
        verdict="correct",
        evidence_useful=True,
        human_verified=True,
        note="这条回答本身是正确的",
    )

    gate = store.feedback_gate(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    current = store.get_conversation(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    metrics = store.product_metrics(workspace_id=DEMO_WORKSPACE_ID)

    assert gate["approved"] is False
    assert gate["task_status"] == "review"
    assert gate["issue_outcome"]["state"] == "insufficient_evidence"
    assert current["status"] == "review"
    assert metrics["verified_issue_count"] == 0
    assert metrics["weekly_verified_issues"] == 0


def test_non_issue_analysis_keeps_human_quality_gate_but_not_verified_issue_metric(tmp_path):
    store = _store(tmp_path)
    conversation = store.create_conversation(
        title="数值平衡分析",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    answer = store.add_message(
        conversation["id"],
        "assistant",
        "数值分析完成，可以由设计师人工确认是否采纳。",
        payload={
            "outcome": {
                "issue_lifecycle": False,
                "requires_project_verification": False,
                "intent": "balance",
                "state": "analysis_complete",
                "label": "分析完成",
                "verified": False,
            }
        },
        workspace_id=DEMO_WORKSPACE_ID,
    )

    store.upsert_feedback(
        workspace_id=DEMO_WORKSPACE_ID,
        user_id=DEMO_USER_ID,
        message_id=answer["id"],
        verdict="correct",
        evidence_useful=True,
        human_verified=True,
        note="设计师确认该分析可采纳",
    )

    gate = store.feedback_gate(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    current = store.get_conversation(conversation["id"], workspace_id=DEMO_WORKSPACE_ID)
    metrics = store.product_metrics(workspace_id=DEMO_WORKSPACE_ID)

    assert gate["approved"] is True
    assert gate["task_status"] == "verified"
    assert current["status"] == "verified"
    assert "issue_outcome" not in gate
    assert metrics["verified_issue_count"] == 0
    assert metrics["weekly_verified_issues"] == 0
    assert metrics["median_time_to_verified_issue_seconds"] is None
