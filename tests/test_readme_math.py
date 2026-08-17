from pathlib import Path


README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_math_uses_supported_github_syntax() -> None:
    text = README.read_text(encoding="utf-8")
    assert r"\operatorname{" not in text
    assert text.count("```math\n") == 18
    assert r"\mathrm{clip}" in text
    assert r"\mathrm{Std}" in text
