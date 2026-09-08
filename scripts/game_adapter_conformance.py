from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.integrations.game_adapter import (
    FrozenKernelGameAdapterGateway,
    GameAdapterRequest,
    HttpGameAdapter,
    SyntheticContractAdapter,
)


async def _run(args) -> dict:
    if args.endpoint:
        adapter = HttpGameAdapter(
            args.endpoint,
            token=args.token or os.getenv("LINGJING_GAME_ADAPTER_TOKEN"),
            timeout_seconds=args.timeout,
        )
        evidence_class = "external-adapter-contract-probe-not-project-verification"
    else:
        adapter = SyntheticContractAdapter()
        evidence_class = "synthetic-adapter-contract-smoke-not-engine-evidence"

    capabilities = await adapter.capabilities()
    result: dict = {
        "protocol": "lingjing-game-adapter-v1",
        "capabilities": capabilities.to_dict(),
        "execution_probe": None,
        "evidence_class": evidence_class,
        "quality_claim": "none-adapter-conformance-only",
    }
    if not args.execute_dry_run:
        return result
    if not capabilities.supports_dry_run:
        raise SystemExit("adapter does not support required dry-run conformance execution")

    scope = {
        "build_ref": args.build_ref,
        "branch_ref": args.branch_ref,
        "environment_ref": args.environment_ref,
    }
    scope = {key: value for key, value in scope.items() if value}
    action = {
        "kind": "conformance.inspect",
        "target": args.target,
        "mutating": False,
    }
    secret = args.signing_secret or os.getenv("LINGJING_GAME_ADAPTER_SIGNING_SECRET")
    if not secret:
        secret = "synthetic-conformance-secret-32-bytes" if not args.endpoint else None
    if not secret:
        raise SystemExit(
            "live dry-run conformance requires --signing-secret or "
            "LINGJING_GAME_ADAPTER_SIGNING_SECRET"
        )
    gateway = FrozenKernelGameAdapterGateway(secret)
    action_id = f"conformance-{int(time.time() * 1000)}"
    ticket = gateway.issue_ticket(
        adapter_id=capabilities.adapter_id,
        action_id=action_id,
        scope=scope,
        ttl_seconds=30,
    )
    request = GameAdapterRequest(
        action_id=action_id,
        action=action,
        scope=scope,
        evidence_requests=("logs", "screenshot"),
        dry_run=True,
        ticket=ticket,
    )
    observation = await gateway.execute(adapter, request)
    result["execution_probe"] = observation.to_dict()
    result["conformance_passed"] = bool(
        observation.status == "dry-run"
        and observation.canonical_write_allowed is False
        and observation.verifier_status == "not-run"
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint")
    parser.add_argument("--token")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--execute-dry-run", action="store_true")
    parser.add_argument("--signing-secret")
    parser.add_argument("--build-ref", default="conformance")
    parser.add_argument("--branch-ref", default="conformance")
    parser.add_argument("--environment-ref", default="ci")
    parser.add_argument("--target", default="adapter-health-probe")
    parser.add_argument("--require-conformance", action="store_true")
    args = parser.parse_args()

    payload = asyncio.run(_run(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.require_conformance:
        if not args.execute_dry_run or not payload.get("conformance_passed"):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
