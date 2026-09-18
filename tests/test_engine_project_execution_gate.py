from __future__ import annotations

from copy import deepcopy

import pytest

from worldforge.context.evidence_controller import EvidencePlan
from worldforge.context.verification_contract import (
    engine_project_execution_asset_ids,
)
from worldforge.product.contextual_analyzer import _task_verification_contract
from worldforge.product.contextual_analyzer_v3 import ProductAnalyzer


def _plan(*, causal: bool = True, comparison: bool = True) -> EvidencePlan:
    return EvidencePlan(
        needs_visual=True,
        needs_audio=False,
        needs_text=True,
        needs_temporal=False,
        causal=causal,
        comparison=comparison,
        exact_identifier=False,
        semantic_retrieval=False,
        temporal_frame_budget=0,
        max_semantic_promotions=0,
        deterministic_score=1.0,
        reason="engine-backed Bug/Fix verification",
    )


def _engine_asset(**meta_overrides):
    meta = {
        "kind": "text",
        "source_type": "game-adapter",
        "evidence_class": "external-engine-observation-unverified",
        "play_mode": "play",
        "adapter_evidence_kind": "snapshot",
        "adapter_id": "unity-editor-project-123",
        "ticket_id": "ticket-123",
        "sha256": "a" * 64,
        "build": "build-2.0.0",
        "branch": "release/2.0",
        "commit": "abc1234",
        "environment": "unity-editor",
    }
    meta.update(meta_overrides)
    return {
        "id": "asset-engine-1",
        "name": "unity-editor-snapshot.json",
        "mime": "application/json",
        "size": 512,
        "meta": meta,
    }


def test_governed_play_mode_engine_asset_unlocks_project_execution_context_only():
    asset = _engine_asset()

    assert engine_project_execution_asset_ids([asset]) == ("asset-engine-1",)

    contract, asset_ids = _task_verification_contract(
        _plan(),
        raw_assets=[asset],
        compiled_assets=[deepcopy(asset)],
    )
    stats = contract.stats()

    assert asset_ids == ("asset-engine-1",)
    assert stats["actual_project_execution_available"] is True
    assert stats["verification_claim_ceiling"] != (
        "hypothesis-only-until-real-project-verification"
    )
    assert set(stats["verification_identity_fields_present"]) >= {
        "build",
        "branch",
        "commit",
        "environment",
    }

    outcome = ProductAnalyzer._structured_outcome(
        {
            "intent": "regression",
            "context": {
                **stats,
                "runtime_verification_scope": "external-engine-observation",
            },
        }
    )
    assert outcome["state"] == "needs_verifier_decision"
    assert outcome["project_execution"] is True
    assert outcome["verified"] is False
    assert outcome["runtime_scope"] == "external-engine-observation"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_type", "user-upload"),
        ("evidence_class", "verified"),
        ("play_mode", "edit"),
        ("adapter_evidence_kind", "video"),
        ("adapter_id", ""),
        ("ticket_id", ""),
        ("sha256", "bad"),
    ],
)
def test_untrusted_or_non_play_engine_metadata_cannot_unlock_project_execution(
    field,
    value,
):
    asset = _engine_asset(**{field: value})

    assert engine_project_execution_asset_ids([asset]) == ()

    contract, asset_ids = _task_verification_contract(
        _plan(),
        raw_assets=[asset],
        compiled_assets=[deepcopy(asset)],
    )
    stats = contract.stats()
    assert asset_ids == ()
    assert stats["actual_project_execution_available"] is False
    assert stats["verification_claim_ceiling"] == (
        "hypothesis-only-until-real-project-verification"
    )

    outcome = ProductAnalyzer._structured_outcome(
        {
            "intent": "regression",
            "context": {
                **stats,
                "runtime_verification_scope": "none",
            },
        }
    )
    assert outcome["state"] == "insufficient_evidence"
    assert outcome["project_execution"] is False
    assert outcome["verified"] is False


def test_zero_sized_or_missing_id_engine_asset_cannot_unlock_project_execution():
    zero = _engine_asset()
    zero["size"] = 0
    missing_id = _engine_asset()
    missing_id["id"] = ""

    assert engine_project_execution_asset_ids([zero]) == ()
    assert engine_project_execution_asset_ids([missing_id]) == ()


def test_normal_upload_with_engine_like_filename_is_not_project_execution():
    upload = {
        "id": "asset-user",
        "name": "unity-editor-snapshot.json",
        "mime": "application/json",
        "size": 400,
        "meta": {
            "kind": "text",
            "play_mode": "play",
            "sha256": "a" * 64,
            "build": "build-2.0.0",
        },
    }

    contract, asset_ids = _task_verification_contract(
        _plan(),
        raw_assets=[upload],
        compiled_assets=[deepcopy(upload)],
    )

    assert asset_ids == ()
    assert contract.actual_project_execution_available is False
    assert contract.claim_ceiling == "hypothesis-only-until-real-project-verification"
