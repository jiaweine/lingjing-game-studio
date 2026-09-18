from worldforge.product.contextual_analyzer_v3 import ProductAnalyzer


def test_bug_outcome_never_promotes_without_real_project_execution():
    outcome = ProductAnalyzer._structured_outcome(
        {
            "intent": "battle_review",
            "context": {
                "actual_project_execution_available": False,
                "verification_claim_ceiling": "hypothesis-only-until-real-project-verification",
                "runtime_verification_scope": "synthetic-builtin-scenario",
            },
        }
    )

    assert outcome["issue_lifecycle"] is True
    assert outcome["requires_project_verification"] is True
    assert outcome["state"] == "insufficient_evidence"
    assert outcome["label"] == "证据不足"
    assert outcome["verified"] is False
    assert outcome["project_execution"] is False
    assert outcome["runtime_scope"] == "synthetic-builtin-scenario"


def test_real_project_execution_still_requires_independent_verifier_decision():
    outcome = ProductAnalyzer._structured_outcome(
        {
            "intent": "regression",
            "context": {
                "actual_project_execution_available": True,
                "verification_claim_ceiling": "evidence-bounded-observation",
                "runtime_verification_scope": "external-engine-observation",
            },
        }
    )

    assert outcome["issue_lifecycle"] is True
    assert outcome["requires_project_verification"] is True
    assert outcome["state"] == "needs_verifier_decision"
    assert outcome["verified"] is False
    assert outcome["project_execution"] is True


def test_non_bug_analysis_is_not_mislabeled_as_reproduction():
    outcome = ProductAnalyzer._structured_outcome(
        {
            "intent": "balance",
            "context": {"actual_project_execution_available": False},
        }
    )

    assert outcome["issue_lifecycle"] is False
    assert outcome["requires_project_verification"] is False
    assert outcome["state"] == "analysis_complete"
    assert outcome["verified"] is False
