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

A live single-backend run uses a JSON dataset with real media paths and a running retrieval
coordinator:

```bash
python scripts/multimodal_quality_benchmark.py \
  --dataset /shared/bench/game-rd-mm-v1.json \
  --endpoint http://127.0.0.1:8910 \
  --require-zero-contamination \
  --require-semantic-backend
```

For controlled backend comparison, use the matrix runner. Every row receives the same dataset,
case order, top-K, scope filter and scoring code. Each endpoint should represent a frozen
retrieval deployment, for example lexical baseline vs WeMM vs a future WeMM+reranker vs
WeMM+LCO audio specialist:

```bash
python scripts/multimodal_quality_matrix.py \
  --dataset /shared/bench/game-rd-mm-v1.json \
  --backend wemm=http://127.0.0.1:8910 \
  --backend wemm-rerank=http://127.0.0.1:8920 \
  --backend wemm-lco=http://127.0.0.1:8930 \
  --require-zero-contamination \
  --require-semantic-backend
```

The matrix always includes `baseline`, which is the product coordinator with semantic workers
disabled. It reports raw results per backend plus metric deltas versus that baseline. Endpoint
labels describe deployment variants only; the runner does not assume that a named reranker or
audio specialist actually exists behind an endpoint.

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

The matrix additionally reports deltas against the deterministic lexical baseline for the same
metrics. A lower latency/cost delta is better, while a higher recall/MRR/temporal delta is
better; contamination must remain zero rather than being traded for recall.

`worker_lane_seconds_proxy` is not claimed to be exact GPU kernel time. It includes worker
request latency and is only a cost proxy until worker-level CUDA timing is added.

The benchmark intentionally does **not** yet report provider media tokens or end-task success.
Those belong to a later generator/provider benchmark where the selected evidence is actually
sent to the model and the task answer is scored under a frozen model/budget.

## Evidence labels

- Built-in smoke: `synthetic-protocol-smoke-not-quality-evidence` and `none-protocol-smoke`.
- A single live endpoint plus external dataset: `measured-live-retrieval-only`.
- A live matrix plus external dataset: `controlled-live-retrieval-comparison-only`.

None of these labels means external SOTA. A SOTA claim requires a controlled external baseline,
frozen model/media/budgets, held-out cases, and reproducible evaluation artifacts.
