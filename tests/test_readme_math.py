from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_math_uses_supported_github_syntax() -> None:
    text = README.read_text(encoding="utf-8")
    assert r"\operatorname{" not in text
    assert text.count("```math\n") == 18
    assert r"\mathrm{clip}" in text
    assert r"\mathrm{Std}" in text
    assert r"g_j(s;G)" in text
    assert r"p_G(o)" in text
    assert r"\alpha^\star" in text
    assert r"\mathrm{LCB}_q" in text
    assert r"\mathrm{Promote}(G')" in text
    assert "Self-Evolving Game R&D Agent Harness Runtime" in text
    assert "sealed-heldout-game-harness-2026-08" in text


def test_readme_does_not_present_bootstrap_constants_as_the_method() -> None:
    text = README.read_text(encoding="utf-8")
    assert "E(\\mathrm{scout})" not in text
    assert "Risk Specialist | 高 threat / 低 HP" not in text
    assert "Failure Attribution + Skill Evolver" not in text
    assert "固定 Analyst" not in text
