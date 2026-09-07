# Multimodal Quality Benchmark

This benchmark is deliberately separate from the deterministic multimodal scope/provenance
gate.

The existing `scripts/multimodal_context_benchmark.py` answers safety questions such as:

- are raw assets still addressable;
- does build/branch/commit scope prevent stale evidence from becoming model-facing;
- do text/image/video/audio all remain representable;
- does cross-build comparison abstain rather than guess.

It does **not** claim semantic retrieval quality.

## Protocol

`python scripts/multimodal_quality_benchmark.py` runs a synthetic protocol smoke against the
product coordinator's real lexical fallback. This only verifies that the evaluator, scope
filter, metrics and output schema work. Its results are not quality evidence.

A live run uses a JSON dataset with real media paths and a running retrieval coordinator:

```bash
python scripts/multimodal_quality_benchmark.py \
  --dataset /shared/bench/game-rd-mm-v1.json \
  --endpoint http://127.0.0.1:8910 \
  --require-zero-contamination \
  --require-semantic-backend
```

The dataset schema is intentionally small:

```json
{
  "name": "game-rd-mm-v1",
  "evidence_class": "controlled-live-sidecar",
  "cases": [
    {
      "id": "boss-audio-37s",
      "query": "37 秒附近技能音效是否重复触发",
      "top_k": 5,
      "assets": [
        {
          "id": "run-147",
          "name": "boss-run-1.4.7.mp4",
          "mime": "video/mp4",
          "path": "/shared/assets/boss-run-1.4.7.mp4",
          "meta": {
            "kind": "video",
            "duration": 120.0,
            "has_audio": true,
            "_context": {"scope_eligible": true}
          }
        }
      ],
      "relevant": [
        {"asset_id": "run-147", "start": 34.0, "end": 41.0}
      ],
      "forbidden_asset_ids": ["run-200"]
    }
  ]
}
```

`scope_eligible=false` assets are removed before the rank request, matching the product-side
`ScopedMultimodalRetrievalClient` authority boundary.

## Reported metrics

The v1 evaluator reports:

- macro `Recall@K`;
- mean reciprocal rank;
- temporal localization IoU and hit rate for cases with labeled intervals;
- forbidden/build contamination rate and count;
- p50/p95 coordinator latency;
- bytes of retrieved image/video/audio assets at K;
- a `worker_lane_seconds_proxy` derived from coordinator visual/audio worker latencies;
- backend mix and whether a non-lexical semantic backend was actually observed.

`worker_lane_seconds_proxy` is not claimed to be exact GPU kernel time. It includes worker
request latency and is only a cost proxy until worker-level CUDA timing is added.

The benchmark intentionally does **not** yet report provider media tokens or end-task success.
Those belong to a later generator/provider benchmark where the selected evidence is actually
sent to the model and the task answer is scored under a frozen model/budget.

## Comparison ladder

The intended controlled comparison is:

1. product lexical/deterministic fallback;
2. WeMM visual/video/text retrieval;
3. WeMM plus a reranker, if/when a reranker is added;
4. WeMM plus LCO acoustic specialist for audio-intent cases.

All rows must use the same cases, top-K, scope filter, media files, timeout and scoring code.
Do not compare a synthetic smoke score with a GPU live score, and do not call either one
external SOTA evidence without a controlled external baseline.
