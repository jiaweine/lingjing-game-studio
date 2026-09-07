# Multimodal Quality Benchmark

This benchmark is deliberately separate from the deterministic multimodal scope/provenance gate.

The existing `scripts/multimodal_context_benchmark.py` answers safety questions such as whether raw assets remain addressable, build/branch/commit scope prevents stale evidence from becoming model-facing, text/image/video/audio remain representable, and cross-build comparison abstains rather than guesses. It does **not** claim semantic retrieval quality.

## Evidence ladder

There are two independent gates before a run may carry a measured quality label:

1. **Corpus eligibility** — `game-rd-mm-v1` must pass the frozen held-out validator with file hashes verified in the benchmark process.
2. **Run eligibility** — the live deployment must be explicitly identified, warmed up, measured repeatedly, observed using a semantic backend in every measured repeat, and maintain exactly zero scope/build contamination.

A corpus can therefore be valid while a particular experiment run is still ineligible for a quality claim. This prevents a one-off endpoint call from inheriting the credibility of a carefully frozen dataset.

## Real-corpus authoring

The repository intentionally does not contain a real held-out game-R&D corpus. Product gallery screenshots under `docs/assets/readme/` are documentation assets and must not be reused as held-out quality evidence.

For a real corpus root with raw files under `assets/`, run:

```bash
python scripts/scaffold_multimodal_corpus.py \
  --corpus-root /shared/bench/game-rd-mm-v1
```

The scaffold produces `workspace.json` with a deterministic asset catalog. It computes corpus-relative paths, MIME/modality, byte size and SHA-256, and audits duplicate content. It does **not** generate benchmark labels or infer build/scope truth.

Human annotators then add cases using asset ids from the catalog. Retrieval output must not be used to manufacture held-out queries or relevance labels. After independent annotation, adjudication and a verified development exclusion, freeze the workspace:

```bash
python scripts/freeze_multimodal_corpus.py \
  --workspace /shared/bench/game-rd-mm-v1/workspace.json \
  --frozen-at 2026-09-07T10:00:00Z
```

Freeze recompiles the existing benchmark manifest, materializes scope eligibility from human `forbidden_asset_ids`, re-hashes all referenced files and runs the strict corpus validator. It fails without writing a quality-eligible manifest if any structural, annotation, diversity, modality, temporal, scope-negative or file-integrity requirement is missing.

The generated workspace and real media should normally remain outside the public repository. `benchmarks/game-rd-mm-v1/.gitignore` ignores local `assets/`, `workspace.json`, `manifest.json` and result `artifacts/` by default.

## Protocol smoke

`python scripts/multimodal_quality_benchmark.py` runs a synthetic protocol smoke against the product coordinator's real lexical fallback. It verifies evaluator wiring, scope filtering, metrics, repeat aggregation and the result schema. Its results are not quality evidence.

CI may use multiple repeats to exercise aggregation, but synthetic smoke always keeps `quality_claim = none-protocol-smoke`.

## Single live backend

For a measured live run, use a strict held-out corpus and an immutable deployment identity. The identity should be sufficient to recover the exact retrieval deployment, for example an OCI image digest plus model revision. A mutable label such as `latest` is not a useful deployment id.

```bash
python scripts/multimodal_quality_benchmark.py \
  --dataset /shared/bench/game-rd-mm-v1/manifest.json \
  --endpoint http://127.0.0.1:8910 \
  --deployment-id 'retriever@sha256:...;wemm=tencent/WeMM-Embedding-2B@<revision>' \
  --warmup-repeats 1 \
  --repeats 3 \
  --output artifacts/wemm-run.json \
  --require-zero-contamination \
  --require-semantic-backend \
  --require-quality-eligible-corpus \
  --require-measured-quality-run
```

The minimum repeat floor is deliberately small: one full-corpus warmup and three measured full-corpus passes. It is a reproducibility floor, not a claim that three repeats are enough for all latency studies.

## Controlled backend matrix

The matrix always includes `baseline`, which is the product coordinator with semantic workers disabled. Every live backend needs both an endpoint and an immutable deployment id:

```bash
python scripts/multimodal_quality_matrix.py \
  --dataset /shared/bench/game-rd-mm-v1/manifest.json \
  --backend wemm=http://127.0.0.1:8910 \
  --deployment 'wemm=retriever@sha256:...;wemm=<model-revision>' \
  --backend wemm-rerank=http://127.0.0.1:8920 \
  --deployment 'wemm-rerank=retriever@sha256:...;wemm=<revision>;reranker=<revision>' \
  --backend wemm-lco=http://127.0.0.1:8930 \
  --deployment 'wemm-lco=retriever@sha256:...;wemm=<revision>;lco=<revision>' \
  --warmup-repeats 1 \
  --repeats 3 \
  --output artifacts/mm-matrix.json \
  --require-zero-contamination \
  --require-semantic-backend \
  --require-quality-eligible-corpus \
  --require-measured-quality-run
```

Measured matrix passes use a deterministic balanced backend rotation. The case order within every full-corpus pass stays fixed, while the backend that executes first rotates by pass. This reduces a simple fixed-order cache/thermal bias without randomizing the frozen case sequence.

Endpoint labels describe deployment variants only. The runner never infers that a label such as `wemm-rerank` proves a reranker actually exists. The explicit deployment id and backend telemetry are the audit trail.

## Repeated-run aggregation

The runner never selects a best repeat.

- Recall@K, MRR, temporal IoU and temporal hit rate are averaged over every measured full-corpus repeat.
- p50/p95 latency are computed by pooling every measured case-request latency across repeats.
- retrieved media bytes and `worker_lane_seconds_proxy` are normalized to a mean full-corpus pass.
- forbidden hits accumulate across every measured exposure; any non-zero value blocks measured quality eligibility.
- the first measured repeat's per-case rows remain in `case_results` for compact diagnostics, and `case_results_semantics` states that headline metrics aggregate all repeats.
- `repeat_metric_samples` keeps the top-level metric sample from every measured pass so variance is visible rather than hidden.

`worker_lane_seconds_proxy` remains only a coordinator/worker-lane cost proxy. It is **not** exact CUDA kernel time and must not be presented as GPU execution time.

## Run manifest and artifact identity

Every benchmark result contains `run_manifest` with:

- benchmark-code revision and how it was resolved (`LINGJING_BENCH_SOURCE_REVISION`, `GITHUB_SHA`, or `git rev-parse HEAD`);
- frozen dataset digest;
- measured and warmup repeat counts;
- cache-state label;
- scheduling protocol;
- deployment ids;
- latency/cost semantics;
- a canonical `result_digest` over the result payload.

`--output` writes the same JSON payload atomically to disk. The digest is semantic rather than a hash of pretty-printed bytes, so changing whitespace does not create a new result identity.

The benchmark-code revision identifies the evaluator tree, not the remote model deployment. That is why live backends require a separate explicit deployment id.

## Reported metrics

The v1 evaluator reports macro Recall@K, mean reciprocal rank, temporal localization IoU/hit rate, forbidden/build contamination, pooled p50/p95 coordinator latency, retrieved media bytes, `worker_lane_seconds_proxy`, backend mix and whether a non-lexical semantic backend was actually observed.

The matrix additionally reports deltas against the deterministic lexical baseline. A lower latency/cost delta is better, while a higher recall/MRR/temporal delta is better. Contamination is not a Pareto dimension: it must remain exactly zero.

The benchmark intentionally does **not** yet report provider media tokens, exact GPU kernel cost or end-task answer success. Those require later generator/provider and worker-instrumentation studies.

## Evidence labels

- Asset inventory / annotation workspace: `none-asset-inventory-only` / `none-annotation-workspace`.
- Successful corpus freeze: `corpus-protocol-eligible-not-model-quality`.
- Built-in smoke: `synthetic-protocol-smoke-not-quality-evidence` plus `none-protocol-smoke`.
- External dataset that fails the frozen corpus gate: `none-unvalidated-external-dataset`.
- Strict corpus but incomplete live-run provenance/repeat/semantic/contamination gate: `none-incomplete-live-run-provenance`.
- Eligible single live run: `measured-live-retrieval-on-frozen-heldout-corpus`.
- Eligible controlled matrix: `controlled-live-comparison-on-frozen-heldout-corpus`.

None of these labels means external SOTA. A SOTA claim still requires strong controlled external baselines, frozen model/media/budgets, held-out cases, sufficient statistical power and reproducible artifacts.
