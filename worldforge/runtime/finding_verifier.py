"""Verification boundary between runtime findings and trusted findings.

This module intentionally keeps verification separate from event generation.
Runtime events and generated findings are hypotheses until an explicit
verification step accepts them.
"""

from dataclasses import dataclass, field
from typing import Any

from .finding_builder import RuntimeFinding


@dataclass(frozen=True)
class VerifiedFinding:
    finding: RuntimeFinding
    verified: bool
    verifier: str
    details: dict[str, Any] = field(default_factory=dict)


class FindingVerifier:
    """Minimal verification contract for runtime findings."""

    name = "base-finding-verifier"

    def verify(self, finding: RuntimeFinding) -> VerifiedFinding:
        raise NotImplementedError


class DeterministicFindingVerifier(FindingVerifier):
    """Reference verifier used by tests and deterministic pipelines.

    Production game verifiers can subclass this boundary and attach real
    assertions from RuntimeProvider observations.
    """

    name = "deterministic-finding-verifier"

    def verify(self, finding: RuntimeFinding) -> VerifiedFinding:
        return VerifiedFinding(
            finding=finding,
            verified=True,
            verifier=self.name,
            details={"reason": "deterministic acceptance"},
        )
