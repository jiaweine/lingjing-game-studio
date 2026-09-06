from __future__ import annotations

from worldforge.context.memory_identity import MemoryIdentityResolver


def _head(
    memory_key: str,
    content: str,
    *,
    kind: str = "fact",
    state: str = "active",
    revision: int = 1,
    build_ref: str | None = None,
    branch_ref: str | None = None,
):
    return {
        "id": f"mem-{memory_key}-{revision}-{build_ref or 'general'}",
        "memory_key": memory_key,
        "revision": revision,
        "kind": kind,
        "content": content,
        "state": state,
        "build_ref": build_ref,
        "branch_ref": branch_ref,
        "commit_ref": None,
        "environment_ref": None,
    }


def _proposal(
    content: str,
    *,
    kind: str = "fact",
    build_ref: str | None = None,
    branch_ref: str | None = None,
):
    return {
        "kind": kind,
        "content": content,
        "build_ref": build_ref,
        "branch_ref": branch_ref,
        "commit_ref": None,
        "environment_ref": None,
    }


def test_same_identity_value_update_is_recommended():
    resolver = MemoryIdentityResolver()
    resolution = resolver.resolve(
        _proposal(
            "已确认 build 1.4.7 护盾冷却是 5 秒。",
            build_ref="1.4.7",
            branch_ref="release",
        ),
        [
            _head(
                "combat.shield.cooldown",
                "build 1.4.7 护盾冷却已确认是 6 秒。",
                build_ref="1.4.7",
                branch_ref="release",
            )
        ],
    )

    assert resolution.abstained is False
    assert resolution.recommended_key == "combat.shield.cooldown"
    assert resolution.best_score >= resolution.threshold
    assert resolution.candidates[0].scope_relation == "exact"


def test_cross_build_same_identity_can_reuse_key_without_merging_scope_heads():
    resolver = MemoryIdentityResolver()
    resolution = resolver.resolve(
        _proposal(
            "已确认 build 2.0.0 护盾冷却是 5 秒。",
            build_ref="2.0.0",
            branch_ref="release",
        ),
        [
            _head(
                "combat.shield.cooldown",
                "build 1.4.7 护盾冷却已确认是 6 秒。",
                build_ref="1.4.7",
                branch_ref="release",
            )
        ],
    )

    assert resolution.recommended_key == "combat.shield.cooldown"
    assert resolution.candidates[0].scope_relation == "cross-scope"
    # Reusing the semantic key does not imply overwriting the old build head; Store scope
    # identity remains separate and will create the 2.0.0 head independently.


def test_different_predicate_abstains_instead_of_false_merge():
    resolver = MemoryIdentityResolver()
    resolution = resolver.resolve(
        _proposal("已确认护盾持续时间是 5 秒。"),
        [_head("combat.shield.cooldown", "护盾冷却已确认是 6 秒。")],
    )

    assert resolution.abstained is True
    assert resolution.recommended_key is None


def test_different_named_entity_abstains_instead_of_false_merge():
    resolver = MemoryIdentityResolver()
    resolution = resolver.resolve(
        _proposal("Confirmed ice_shield cooldown is 5 seconds."),
        [_head("combat.fire_shield.cooldown", "Confirmed fire_shield cooldown is 6 seconds.")],
    )

    assert resolution.abstained is True
    assert resolution.recommended_key is None


def test_ambiguous_near_duplicate_candidates_force_abstention():
    resolver = MemoryIdentityResolver()
    resolution = resolver.resolve(
        _proposal("Confirmed shield cooldown is 5 seconds."),
        [
            _head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),
            _head("ui.shield.cooldown", "Confirmed shield cooldown is 6 seconds."),
        ],
    )

    assert len(resolution.candidates) == 2
    assert resolution.best_score >= resolution.threshold
    assert resolution.score_margin < resolution.margin_threshold
    assert resolution.abstained is True
    assert resolution.recommended_key is None


def test_retracted_and_kind_mismatch_heads_are_not_identity_candidates():
    resolver = MemoryIdentityResolver()
    resolution = resolver.resolve(
        _proposal("Confirmed shield cooldown is 5 seconds.", kind="fact"),
        [
            _head(
                "combat.shield.cooldown",
                "Confirmed shield cooldown is 6 seconds.",
                state="retracted",
            ),
            _head(
                "combat.shield.cooldown.rule",
                "Shield cooldown must remain 6 seconds.",
                kind="constraint",
            ),
        ],
    )

    assert resolution.candidates == ()
    assert resolution.abstained is True


def test_result_is_explainable_and_never_mutates_inputs():
    resolver = MemoryIdentityResolver()
    proposal = _proposal("Confirmed shield cooldown is 5 seconds.")
    heads = [_head("combat.shield.cooldown", "Confirmed shield cooldown is 6 seconds.")]
    before_proposal = dict(proposal)
    before_head = dict(heads[0])

    result = resolver.resolve(proposal, heads).to_dict()

    assert proposal == before_proposal
    assert heads[0] == before_head
    assert result["mode"] == "deterministic-memory-identity-v1"
    assert result["recommended_key"] == "combat.shield.cooldown"
    assert result["candidates"][0]["reasons"]
    assert "score" in result["candidates"][0]
