from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

import httpx
from sqlalchemy import create_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from worldforge.integrations.game_adapter import (
    FrozenKernelGameAdapterGateway,
    GameAdapterError,
    GameAdapterRequest,
    HttpGameAdapter,
    SqlGameAdapterReplayStore,
    SyntheticContractAdapter,
)


async def _fetch_evidence_probe(args, observation) -> dict:
    if not args.endpoint:
        raise SystemExit("--fetch-evidence requires --endpoint")
    base = str(args.endpoint).rstrip("/")
    allowed_prefix = f"{base}/v1/adapter/evidence/"
    headers = {}
    token = args.token or os.getenv("LINGJING_GAME_ADAPTER_TOKEN")
    if token:
        headers["authorization"] = f"Bearer {token}"

    items = []
    async with httpx.AsyncClient(timeout=args.timeout) as client:
        for evidence in observation.evidence:
            locator = str(evidence.get("locator") or "")
            expected = str(evidence.get("sha256") or "").lower()
            row = {
                "kind": str(evidence.get("kind") or "artifact"),
                "locator": locator,
                "status_code": None,
                "bytes": 0,
                "sha256": None,
                "passed": False,
            }
            if not locator.startswith(allowed_prefix):
                row["error"] = "locator-outside-adapter-evidence-prefix"
                items.append(row)
                continue
            try:
                response = await client.get(locator, headers=headers)
            except httpx.HTTPError as exc:
                row["error"] = f"request-failed:{type(exc).__name__}"
                items.append(row)
                continue
            row["status_code"] = response.status_code
            body = response.content
            row["bytes"] = len(body)
            if len(body) > 6 * 1024 * 1024:
                row["error"] = "evidence-exceeds-6mb-conformance-limit"
                items.append(row)
                continue
            actual = hashlib.sha256(body).hexdigest()
            row["sha256"] = actual
            header_sha = str(response.headers.get("x-lingjing-sha256") or "").lower()
            row["content_type"] = response.headers.get("content-type")
            row["passed"] = bool(
                response.status_code == 200
                and expected
                and actual == expected
                and (not header_sha or header_sha == actual)
            )
            if not row["passed"]:
                row["error"] = "status-or-digest-mismatch"
            items.append(row)

    screenshot_present = any(
        item["kind"] == "screenshot" and item["passed"] for item in items
    )
    passed = bool(items) and all(item["passed"] for item in items)
    if args.require_screenshot:
        passed = passed and screenshot_present
    return {
        "requested": True,
        "same_adapter_origin_required": True,
        "items": items,
        "screenshot_present": screenshot_present,
        "passed": passed,
        "evidence_class": "retrievable-external-engine-observation-not-verifier-truth",
    }


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
        "evidence_fetch_probe": None,
        "durable_replay_probe": None,
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
    evidence_requests = ("logs", "snapshot", "screenshot")
    secret = args.signing_secret or os.getenv("LINGJING_GAME_ADAPTER_SIGNING_SECRET")
    if not secret:
        secret = "synthetic-conformance-secret-32-bytes" if not args.endpoint else None
    if not secret:
        raise SystemExit(
            "live dry-run conformance requires --signing-secret or "
            "LINGJING_GAME_ADAPTER_SIGNING_SECRET"
        )

    replay_temp: tempfile.TemporaryDirectory[str] | None = None
    replay_database_url: str | None = None
    replay_store = None
    if args.durable_replay_smoke:
        replay_temp = tempfile.TemporaryDirectory(prefix="lingjing-game-adapter-replay-")
        replay_path = Path(replay_temp.name) / "replay.sqlite3"
        replay_database_url = f"sqlite:///{replay_path.as_posix()}"
        replay_store = SqlGameAdapterReplayStore(
            create_engine(replay_database_url),
            auto_create_schema=True,
        )

    try:
        gateway = FrozenKernelGameAdapterGateway(secret, replay_store=replay_store)
        action_id = f"conformance-{int(time.time() * 1000)}"
        ticket = gateway.issue_ticket(
            adapter_id=capabilities.adapter_id,
            action_id=action_id,
            action=action,
            scope=scope,
            dry_run=True,
            evidence_requests=evidence_requests,
            ttl_seconds=30,
        )
        request = GameAdapterRequest(
            action_id=action_id,
            action=action,
            scope=scope,
            evidence_requests=evidence_requests,
            dry_run=True,
            ticket=ticket,
        )
        observation = await gateway.execute(adapter, request)
        result["execution_probe"] = observation.to_dict()

        evidence_fetch_passed = True
        if args.fetch_evidence:
            evidence_probe = await _fetch_evidence_probe(args, observation)
            result["evidence_fetch_probe"] = evidence_probe
            evidence_fetch_passed = bool(evidence_probe["passed"])

        durable_replay_passed = True
        if args.durable_replay_smoke:
            assert replay_database_url is not None
            second_store = SqlGameAdapterReplayStore(create_engine(replay_database_url))
            second_gateway = FrozenKernelGameAdapterGateway(secret, replay_store=second_store)
            before_calls = getattr(adapter, "calls", None)
            rejected = False
            try:
                await second_gateway.execute(adapter, request)
            except GameAdapterError as exc:
                rejected = "replay" in str(exc).lower()
            after_calls = getattr(adapter, "calls", None)
            dispatch_unchanged = (
                True
                if before_calls is None or after_calls is None
                else before_calls == after_calls
            )
            durable_replay_passed = bool(rejected and dispatch_unchanged)
            result["durable_replay_probe"] = {
                "backend": "sqlite-two-engine-shared-store-smoke",
                "second_gateway_rejected_replay": rejected,
                "second_dispatch_prevented": dispatch_unchanged,
                "passed": durable_replay_passed,
                "evidence_class": "durable-replay-mechanism-smoke-not-production-load-evidence",
            }

        result["conformance_passed"] = bool(
            observation.status == "dry-run"
            and observation.canonical_write_allowed is False
            and observation.verifier_status == "not-run"
            and evidence_fetch_passed
            and durable_replay_passed
        )
        return result
    finally:
        if replay_temp is not None:
            replay_temp.cleanup()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint")
    parser.add_argument("--token")
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--execute-dry-run", action="store_true")
    parser.add_argument("--fetch-evidence", action="store_true")
    parser.add_argument("--require-screenshot", action="store_true")
    parser.add_argument("--durable-replay-smoke", action="store_true")
    parser.add_argument("--signing-secret")
    parser.add_argument("--build-ref", default="conformance")
    parser.add_argument("--branch-ref", default="conformance")
    parser.add_argument("--environment-ref", default="ci")
    parser.add_argument("--target", default="adapter-health-probe")
    parser.add_argument("--require-conformance", action="store_true")
    args = parser.parse_args()

    if args.durable_replay_smoke and not args.execute_dry_run:
        parser.error("--durable-replay-smoke requires --execute-dry-run")
    if args.fetch_evidence and (not args.endpoint or not args.execute_dry_run):
        parser.error("--fetch-evidence requires --endpoint and --execute-dry-run")
    if args.require_screenshot and not args.fetch_evidence:
        parser.error("--require-screenshot requires --fetch-evidence")

    payload = asyncio.run(_run(args))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if args.require_conformance:
        if not args.execute_dry_run or not payload.get("conformance_passed"):
            raise SystemExit(2)


if __name__ == "__main__":
    main()
