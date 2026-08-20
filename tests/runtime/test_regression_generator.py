import pytest

from worldforge.runtime.regression_generator import (
    VerifiedFinding,
    regression_case_from_finding,
)


def test_verified_finding_generates_regression_case():
    case = regression_case_from_finding(
        VerifiedFinding(
            title="damage overflow",
            build_id="build-1",
            steps=("enter phase two",),
            assertions=("damage within limit",),
        )
    )

    assert case.title == "damage overflow"
    assert case.build_id == "build-1"
    assert case.reproduction_steps == ["enter phase two"]


def test_unverified_finding_cannot_create_regression_case():
    with pytest.raises(ValueError):
        regression_case_from_finding(
            VerifiedFinding(
                title="unknown issue",
                build_id="build-1",
                steps=(),
                assertions=(),
                verified=False,
            )
        )
