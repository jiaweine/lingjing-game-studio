from worldforge.product import ConversationStore
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID


def test_product_metrics_include_verified_issue_value_signals(tmp_path):
    store = ConversationStore(
        db_path=tmp_path / "product.db",
        asset_dir=tmp_path / "assets",
    )
    conversation = store.create_conversation(
        title="Boss 偶发秒杀",
        workspace_id=DEMO_WORKSPACE_ID,
        created_by=DEMO_USER_ID,
    )
    answer = store.add_message(
        conversation["id"],
        "assistant",
        "当前证据需要人工复核。",
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
                "state": "insufficient_evidence",
                "verified": False,
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
        note="人工确认该交付本身正确",
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
