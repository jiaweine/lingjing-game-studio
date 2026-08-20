"""Utilities for turning verified runtime findings into regression cases.

A regression case is generated only from evidence that already passed
verification. Synthetic observations can inform a finding but cannot directly
become a production regression case.
"""

from dataclasses import dataclass
from typing import Iterable

from .regression_case import RegressionCase


@dataclass(frozen=True)
class VerifiedFinding:
    title: str
    build_id: str
    steps: tuple[str, ...]
    assertions: tuple[str, ...]
    verified: bool = True


def regression_case_from_finding(finding: VerifiedFinding) -> RegressionCase:
    """Create a replayable regression case from verified evidence."""
    if not finding.verified:
        raise ValueError("Only verified findings can create regression cases")

    return RegressionCase(
        title=finding.title,
        build_id=finding.build_id,
        reproduction_steps=list(finding.steps),
        assertions=list(finding.assertions),
    )


def generate_regression_cases(findings: Iterable[VerifiedFinding]) -> list[RegressionCase]:
    return [regression_case_from_finding(item) for item in findings if item.verified]
