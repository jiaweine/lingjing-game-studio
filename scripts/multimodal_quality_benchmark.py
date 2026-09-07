from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import httpx

from worldforge.benchmarks.multimodal_corpus import (
    canonical_corpus_digest,
    resolve_dataset_paths,
    validate_corpus,
)
from worldforge.benchmarks.multimodal_run import (
    attach_run_manifest,
    evaluate_repeated,
    measured_run_protocol_eligible,
    write_result_json,
)


def _asset(
    asset_id: str,
    name: str,
    mime: str,
    *,
    kind: str,
    build: str | None = None,
    eligible: bool = True,
    size: int = 0,
    duration: float | None = None,
    has_audio: bool | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "kind": kind,
        "_context": {"scope_eligible": eligible},
    }
    if build:
        meta["build"] = build
    if duration is not None:
        meta["duration"] = duration
    if has_audio is not None:
        meta["has_audio"] = has_audio
    return {
        "id": asset_id,
        "name": name,
        "mime": mime,
        "path": "",
        "size": size,
        "meta": meta,
    }


def protocol_smoke_dataset() -> dict[str, Any]:
    return {
        "name": "protocol-smoke-v1",
        "evidence_class": "synthetic-protocol-smoke-not-quality-evidence",
        "cases": [
            {
                "id": "build-isolation",
                "query": "检查 build 1.4.7 shield_race release screenshot",
                "assets": [
                    _asset(
                        "current-image",
                        "build-1.4.7-shield_race-release.png",
                        "image/png",
                        kind="image",
                        build="1.4.7",
                        size=4096,
                    ),
                    _asset(
                        "stale-image",
                        "build-2.0.0-shield_race-release.png",
                        "image/png",
                        kind="image",
                        build="2.0.0",
                        eligible=False,
                        size=4096,
                    ),
                    _asset(
                        "general-notes",
                        "project-notes.txt",
                        "text/plain",
                        kind="text",
                    ),
                ],
                "relevant": [{"asset_id": "current-image"}],
                "forbidden_asset_ids": ["stale-image"],
                "top_k": 3,
            },
            {
                "id": "audio-routing",
                "query": "release game audio duplicate sound",
                "assets": [
                    _asset(
                        "release-audio",
                        "release-game-audio-duplicate-sound.wav",
                        "audio/wav",
                        kind="audio",
                        build="1.4.7",
                        size=8192,
                    ),
                    _asset(
                        "other-audio",
                        "ambient-music.wav",
                        "audio/wav",
                        kind="audio",
                        build="1.4.7",
                        size=8192,
                    ),
                ],
                "relevant": [{"asset_id": "release-audio"}],
                "top_k": 2,
            },
            {
                "id": "temporal-protocol",
                "query": "60 秒附近 release boss run video",
                "assets": [
                    _asset(
                        "boss-video",
                        "release-boss-run-video.mp4",
                        "video/mp4",
                        kind="video",
                        build="1.4.7",
                        size=16384,
                        duration=100.0,
                        has_audio=True,
                    )
                ],
                "relevant": [
                    {"asset_id": "boss-video", "start": 55.0, "end": 65.0}
                ],
                "top_k": 1,
            },
        ],
    }


async def product_lexical_rank(
    query: str, assets: list[dict[str, Any]], top_k: int
) -> dict[str, Any]:
    from services.multimodal_retriever import app as coordinator

    class _DisabledWorker:
        enabled = False
        compute_budget_ms = None

        async def score(self, *, query, items, backend_hint):
            return coordinator.WorkerResult(backend_hint, [], 0.0, "disabled")

    visual, audio = coordinator.VISUAL_WORKER, coordinator.AUDIO_WORKER
    coordinator.VISUAL_WORKER = _DisabledWorker()
    coordinator.AUDIO_WORKER = _DisabledWorker()
    try:
        request = coordinator.RankRequest(query=query, top_k=top_k, assets=assets)
        return await coordinator.rank(request)
    finally:
        coordinator.VISUAL_WORKER = visual
        coordinator.AUDIO_WORKER = audio


def live_ranker(endpoint: str, timeout_seconds: float):
    endpoint = endpoint.strip().rstrip("/")

    async def _rank(
        query: str, assets: list[dict[str, Any]], top_k: int
    ) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.post(
                f"{endpoint}/v1/rank",
                json={"query": query, "top_k": top_k, "assets": assets},
            )
        response.raise_for_status()
        return dict(response.json() or {})

    return _rank


def _load_dataset(path: str | None) -> dict[str, Any]:
    if not path:
        return protocol_smoke_dataset()
    return dict(json.loads(Path(path).read_text(encoding="utf-8")))


def prepare_external_dataset(
    path: str,
    *,
    verify_files: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    dataset_path = Path(path).resolve()
    raw = _load_dataset(str(dataset_path))
    report = validate_corpus(
        raw,
        base_dir=dataset_path.parent,
        verify_files=verify_files,
    )
    if not report["structurally_valid"]:
        details = "; ".join(report["errors"][:5])
        raise SystemExit(f"invalid multimodal corpus: {details}")
    return resolve_dataset_paths(raw, base_dir=dataset_path.parent), report


def _live_run_blockers(
    *,
    endpoint: str,
    deployment_id: str,
    measured_repeats: int,
    warmup_repeats: int,
    semantic_seen_all_repeats: bool,
    forbidden_hits: int,
) -> list[str]:
    blockers = []
    if not endpoint:
        blockers.append("live endpoint is required")
    if not deployment_id:
        blockers.append("immutable deployment id is required")
    if not measured_run_protocol_eligible(
        measured_repeats=measured_repeats,
        warmup_repeats=warmup_repeats,
    ):
        blockers.append("requires >=3 measured repeats and >=1 full-corpus warmup")
    if not semantic_seen_all_repeats:
        blockers.append("semantic backend must be observed in every measured repeat")
    if forbidden_hits != 0:
        blockers.append("scope/build contamination must remain exactly zero")
    return blockers


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    corpus_validation = None
    if args.dataset:
        dataset, corpus_validation = prepare_external_dataset(
            args.dataset,
            verify_files=not args.skip_file_hash_verification,
        )
    else:
        dataset = _load_dataset(None)

    endpoint = (args.endpoint or os.getenv("LINGJING_MM_BENCH_ENDPOINT", "")).strip()
    deployment_id = str(args.deployment_id or "").strip()
    ranker = live_ranker(endpoint, args.timeout) if endpoint else product_lexical_rank
    result = await evaluate_repeated(
        dataset,
        ranker,
        default_top_k=args.top_k,
        temporal_iou_threshold=args.temporal_iou_threshold,
        measured_repeats=args.repeats,
        warmup_repeats=args.warmup_repeats,
    )
    result["mode"] = "live-sidecar" if endpoint else "product-lexical-fallback"
    result["external_dataset"] = bool(args.dataset)
    result["deployment_id"] = deployment_id or (
        "unspecified-live-deployment" if endpoint else "product-lexical-fallback"
    )
    if corpus_validation is not None:
        result["corpus_validation"] = corpus_validation

    corpus_quality_eligible = bool(
        corpus_validation and corpus_validation.get("strict_quality_eligible")
    )
    run_blockers = _live_run_blockers(
        endpoint=endpoint,
        deployment_id=deployment_id,
        measured_repeats=args.repeats,
        warmup_repeats=args.warmup_repeats,
        semantic_seen_all_repeats=bool(
            result.get("live_semantic_backend_seen_all_repeats")
        ),
        forbidden_hits=int(result.get("forbidden_hits") or 0),
    )
    measured_run_eligible = bool(corpus_quality_eligible and not run_blockers)
    result["measured_run_eligible"] = measured_run_eligible
    result["measured_run_blockers"] = run_blockers if args.dataset else []

    if not args.dataset:
        quality_claim = "none-protocol-smoke"
    elif not corpus_quality_eligible:
        quality_claim = "none-unvalidated-external-dataset"
    elif measured_run_eligible:
        quality_claim = "measured-live-retrieval-on-frozen-heldout-corpus"
    else:
        quality_claim = "none-incomplete-live-run-provenance"
    result["quality_claim"] = quality_claim

    dataset_digest = (
        str(corpus_validation.get("corpus_digest") or "")
        if corpus_validation
        else canonical_corpus_digest(dataset)
    )
    attach_run_manifest(
        result,
        root=ROOT,
        dataset_digest=dataset_digest,
        measured_repeats=args.repeats,
        warmup_repeats=args.warmup_repeats,
        schedule="single-backend-full-corpus-sequential-v1",
        deployment_ids={
            result["mode"]: str(result["deployment_id"]),
        },
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset")
    parser.add_argument("--endpoint")
    parser.add_argument(
        "--deployment-id",
        help="immutable live deployment revision (for example image digest + model revision)",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--temporal-iou-threshold", type=float, default=0.3)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--warmup-repeats", type=int, default=0)
    parser.add_argument("--output", help="optional JSON result artifact path")
    parser.add_argument("--require-zero-contamination", action="store_true")
    parser.add_argument("--require-semantic-backend", action="store_true")
    parser.add_argument("--require-quality-eligible-corpus", action="store_true")
    parser.add_argument("--require-measured-quality-run", action="store_true")
    parser.add_argument("--skip-file-hash-verification", action="store_true")
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    if args.warmup_repeats < 0:
        parser.error("--warmup-repeats must be >= 0")

    result = asyncio.run(_run(args))
    write_result_json(result, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.require_zero_contamination and result["forbidden_hits"] != 0:
        raise SystemExit("build contamination detected")
    if args.require_semantic_backend and not result.get(
        "live_semantic_backend_seen_all_repeats"
    ):
        raise SystemExit("semantic backend was not observed in every measured repeat")
    if args.require_quality_eligible_corpus:
        report = dict(result.get("corpus_validation") or {})
        if not report.get("strict_quality_eligible"):
            raise SystemExit("corpus is not eligible for measured quality claims")
    if args.require_measured_quality_run and not result.get("measured_run_eligible"):
        raise SystemExit(
            "run is not eligible for measured quality claims: "
            + "; ".join(result.get("measured_run_blockers") or [])
        )


if __name__ == "__main__":
    main()
