from worldforge.product.analyzer import ProductAnalyzer


def test_synthetic_evidence_has_limited_confidence_weight():
    observed = [
        {
            "id": "E1",
            "type": "video",
            "provenance": "observed",
        }
    ]
    synthetic = observed + [
        {
            "id": "E2",
            "type": "simulation",
            "provenance": "synthetic",
        }
    ]
    reproduced = observed + [
        {
            "id": "E2",
            "type": "replay",
            "provenance": "reproduced",
        }
    ]

    observed_score = ProductAnalyzer._evidence_confidence(observed)
    synthetic_score = ProductAnalyzer._evidence_confidence(synthetic, {"status": "done"})
    reproduced_score = ProductAnalyzer._evidence_confidence(reproduced)

    assert synthetic_score > observed_score
    assert synthetic_score - observed_score <= 0.08
    assert reproduced_score > synthetic_score


def test_runtime_presence_alone_does_not_inflate_confidence():
    evidence = [
        {
            "id": "E1",
            "type": "text",
            "provenance": "observed",
        }
    ]

    without_runtime = ProductAnalyzer._evidence_confidence(evidence, None)
    with_runtime = ProductAnalyzer._evidence_confidence(
        evidence,
        {"status": "done", "evidence_provenance": "synthetic"},
    )

    assert with_runtime == without_runtime


def test_evidence_pack_preserves_provenance_aware_wording():
    analyzer = object.__new__(ProductAnalyzer)
    evidence = [
        {
            "id": "E1",
            "type": "video",
            "title": "boss-phase-2.mp4",
            "provenance": "observed",
        },
        {
            "id": "E2",
            "type": "simulation",
            "title": "WorldForge 场景 boss_burst",
            "provenance": "synthetic",
        },
    ]

    deliverables = analyzer._deliverables("regression", evidence, {"status": "done"})
    reproduction_card = next(
        row for row in deliverables if row["type"] == "reproduction_card"
    )
    evidence_pack = next(row for row in deliverables if row["type"] == "evidence_pack")

    assert "真实" in reproduction_card["summary"]
    assert "内部模拟" in evidence_pack["summary"]
    assert evidence_pack["evidence_ids"] == ["E1", "E2"]
