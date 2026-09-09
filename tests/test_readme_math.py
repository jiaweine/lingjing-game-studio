from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
BENCHMARKING = ROOT / "docs" / "BENCHMARKING.md"
ARCHITECTURE = ROOT / "docs" / "ARCHITECTURE.md"
RESEARCH_NOTES = ROOT / "docs" / "RESEARCH_NOTES.md"


def test_readme_keeps_math_and_benchmark_detail_in_docs() -> None:
    """The v1 README is the product landing page, not the algorithm notebook.

    Detailed formulas and benchmark protocol belong in docs so GitHub's landing page stays
    readable while the underlying claims remain inspectable and versioned.
    """
    readme = README.read_text(encoding="utf-8")
    benchmark = BENCHMARKING.read_text(encoding="utf-8")

    assert r"\operatorname{" not in readme
    assert "```math\n" not in readme
    assert "README 只保留机制概览" in readme
    assert "docs/ARCHITECTURE.md" in readme
    assert "docs/BENCHMARKING.md" in readme
    assert "docs/RESEARCH_NOTES.md" in readme

    assert "sealed-heldout-game-harness-2026-08" in benchmark
    assert "sealed held-out gain" in benchmark
    assert "paired-bootstrap LCB" in benchmark
    assert "不能据此宣称通用 SOTA" in benchmark
    assert ARCHITECTURE.is_file()
    assert RESEARCH_NOTES.is_file()


def test_readme_does_not_present_bootstrap_constants_as_the_method() -> None:
    text = README.read_text(encoding="utf-8")
    assert "E(\\mathrm{scout})" not in text
    assert "Risk Specialist | 高 threat / 低 HP" not in text
    assert "Failure Attribution + Skill Evolver" not in text
    assert "固定 Analyst" not in text
