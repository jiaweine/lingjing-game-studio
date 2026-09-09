# External evidence execution runbook

This runbook starts where repository CI stops. The ContextOS / Project Memory / multimodal / GameAdapter implementation can be green in CI without proving real-project quality, production throughput, live-GPU performance or Unity/Unreal execution.

Use this document to collect those external measurements without upgrading a synthetic/mechanism result into a stronger claim than the evidence supports.

## 1. Freeze experiment identity first

Before every external run, record:

- exact Lingjing benchmark/code commit SHA;
- protocol version;
- frozen dataset digest, when a dataset is involved;
- provider model or immutable retrieval/engine deployment identity;
- database/engine environment identity where relevant;
- command line and non-secret environment configuration;
- UTC start time;
- raw result artifact path and its digest when the runner provides one.

Do not use mutable identifiers such as `latest` as the only deployment identity. Secrets, private game assets and private held-out labels must remain outside the public repository.

A rerun with a different code revision, dataset digest, provider/model revision or backend deployment is a different experiment. Do not silently pool it with the old result.

## 2. Long-horizon model evidence

### Required inputs

A quality-eligible `lingjing-long-horizon-model-v1` dataset must be human-authored held-out data, frozen, development-excluded and satisfy the protocol floors documented in `docs/LONG_HORIZON_MODEL_BENCHMARK.md`:

- at least 40 cases;
- at least 10 each of `qa`, `update`, `abstention`, `workflow`;
- at least 30 cases whose annotated long-range anchor is outside the legacy last-eight window.

The real dataset is not supplied by this repository.

### Controlled run

Use the same provider/model for both `baseline_last8` and `contextos`:

```bash
python scripts/long_horizon_model_benchmark.py \
  --dataset /private/bench/long-horizon-v1.json \
  --provider gemini \
  --require-quality-eligible-dataset \
  --output /private/bench/results/long-horizon-gemini.json
```

Replace the provider with the actual configured provider under test. Preserve the raw JSON result.

### What the automatic result proves

A successful frozen held-out automatic run is still only:

```text
evidence_class = measured-heldout-anchor-rubric-only
quality_claim  = anchor-rubric-only-not-general-semantic-quality
```

It may support controlled statements about the published anchor rubric: pass rate, required-anchor recall, forbidden-claim rate, abstention compliance, workflow-order compliance and ContextOS-minus-baseline deltas.

It does **not** by itself support a claim of general answer quality, broad game-R&D quality or SOTA.

### Human adjudication gate

For broader semantic-quality claims, add blind human adjudication that does not expose whether an answer came from baseline or ContextOS. Freeze the adjudication rubric and record disagreements/adjudication separately from the automatic anchor rubric. Do not train/tune against the held-out cases after inspecting results.

## 3. Real multimodal game-R&D retrieval evidence

### Build and freeze the corpus

Start from real private game-R&D assets, not documentation screenshots or CI fixtures.

If assets are already in Lingjing product storage, use the product export/staging tooling; otherwise place the raw files below the private corpus `assets/` directory. Then scaffold an annotation workspace:

```bash
python scripts/scaffold_multimodal_corpus.py \
  --corpus-root /shared/bench/game-rd-mm-v1
```

Use the readiness audit while humans annotate:

```bash
python scripts/multimodal_annotation_readiness.py \
  --workspace /shared/bench/game-rd-mm-v1/workspace.json \
  --output /shared/bench/game-rd-mm-v1/artifacts/annotation-readiness.json \
  --require-ready-for-freeze
```

The readiness audit never generates relevance/scope labels. When independent annotation, adjudication and development exclusion are complete, freeze:

```bash
python scripts/freeze_multimodal_corpus.py \
  --workspace /shared/bench/game-rd-mm-v1/workspace.json \
  --frozen-at 2026-09-09T00:00:00Z
```

Use the real freeze timestamp for the actual experiment. A successful freeze is only:

```text
corpus-protocol-eligible-not-model-quality
```

### Single live retriever

Require an immutable deployment identity, one full-corpus warmup, at least three measured passes, a semantic backend in every measured repeat and exactly zero scope/build contamination:

```bash
python scripts/multimodal_quality_benchmark.py \
  --dataset /shared/bench/game-rd-mm-v1/manifest.json \
  --endpoint http://127.0.0.1:8910 \
  --deployment-id 'retriever@sha256:...;wemm=<immutable-model-revision>' \
  --warmup-repeats 1 \
  --repeats 3 \
  --output /shared/bench/game-rd-mm-v1/artifacts/wemm-run.json \
  --require-zero-contamination \
  --require-semantic-backend \
  --require-quality-eligible-corpus \
  --require-measured-quality-run
```

Only a fully eligible run may carry:

```text
measured-live-retrieval-on-frozen-heldout-corpus
```

### Controlled matrix

Compare the deterministic lexical baseline with immutable live deployments under the same frozen corpus:

```bash
python scripts/multimodal_quality_matrix.py \
  --dataset /shared/bench/game-rd-mm-v1/manifest.json \
  --backend wemm=http://127.0.0.1:8910 \
  --deployment 'wemm=retriever@sha256:...;wemm=<revision>' \
  --backend wemm-rerank=http://127.0.0.1:8920 \
  --deployment 'wemm-rerank=retriever@sha256:...;wemm=<revision>;reranker=<revision>' \
  --backend wemm-lco=http://127.0.0.1:8930 \
  --deployment 'wemm-lco=retriever@sha256:...;wemm=<revision>;lco=<revision>' \
  --warmup-repeats 1 \
  --repeats 3 \
  --output /shared/bench/game-rd-mm-v1/artifacts/mm-matrix.json \
  --require-zero-contamination \
  --require-semantic-backend \
  --require-quality-eligible-corpus \
  --require-measured-quality-run
```

Only a fully eligible matrix may carry:

```text
controlled-live-comparison-on-frozen-heldout-corpus
```

`worker_lane_seconds_proxy` is not CUDA kernel time. Coordinator p50/p95 is not an end-to-end product SLA. No measured retrieval label means SOTA.

## 4. PostgreSQL multi-worker ingestion evidence

CI intentionally uses SQLite only as a concurrency-mechanism smoke. For production-like evidence, create a **disposable** PostgreSQL database with the intended server major version, pool/network topology and worker placement. Never point this benchmark at production user data.

Example:

```bash
python scripts/memory_ingestion_load_benchmark.py \
  --database-url "$DISPOSABLE_POSTGRES_URL" \
  --events 10000 \
  --workers 16 \
  --confirm-disposable-database \
  --require-complete \
  > /private/bench/results/memory-ingestion-postgres.json
```

Repeat at more than one workload size rather than reporting one cherry-picked run. Record PostgreSQL server version, instance class/CPU/memory, connection-pool configuration and whether workers share a host or network.

A PostgreSQL run is labeled:

```text
postgresql-multiworker-load-measurement
```

The benchmark's `quality_claim` remains `none-load-protocol-only`; throughput from one environment is not a universal production SLA. The correctness gate remains: all expected receipts completed, no failed/ignored receipts, proposal count matches events and duplicate receipt IDs remain zero.

## 5. Real Unity / Unreal / custom-engine evidence

### Apply the control-plane schema

Production multi-process GameAdapter dispatch requires the durable replay schema through Alembic revision `20260909_0007` and a shared replay store. Do not use the process-local replay store when more than one kernel process can dispatch engine actions.

### Contract probe

Probe capabilities without execution first:

```bash
python scripts/game_adapter_conformance.py \
  --endpoint http://127.0.0.1:9030
```

Then run an explicitly prepared non-mutating dry-run:

```bash
python scripts/game_adapter_conformance.py \
  --endpoint http://127.0.0.1:9030 \
  --execute-dry-run \
  --signing-secret "$LINGJING_GAME_ADAPTER_SIGNING_SECRET" \
  --build-ref build-1.4.7 \
  --branch-ref release \
  --require-conformance
```

A successful live conformance result is only:

```text
external-adapter-contract-probe-not-project-verification
```

### Real project execution

For real project evidence, additionally record:

- engine and exact engine version;
- bridge/plugin commit or immutable package identity;
- project commit/build/branch/environment scope;
- exact authorized action envelope;
- before/after snapshot digests when supported;
- raw logs/screenshots/video/audio evidence and hashes;
- the independent Frozen Kernel verifier result.

`external-engine-observation-unverified`, `canonical_write_allowed=false` and `verifier_status=not-run` are expected immediately after adapter execution. Do not convert an adapter success response into project truth. Only the separate Frozen Kernel verifier may establish authoritative verification.

## 6. Provider token, latency and cost calibration

Gemini, Claude and explicitly trusted OpenAI-compatible/custom count endpoints expose per-request token-count telemetry including estimate/exact delta, exact-to-estimate ratio and native-count extra RTT when available. Use controlled request sets covering representative Chinese/English/code and media-bearing workloads.

Keep exact-token safety telemetry separate from generation latency:

- native token-count extra RTT is not model generation TTFT;
- wall latency from the long-horizon benchmark is not streaming TTFT unless first-token timing is separately instrumented;
- provider cost must come from the provider's real billable usage fields/pricing applicable to the measured deployment, not from character estimates;
- media/token distributions should be reported with sample counts and percentiles, not one example request.

No CI smoke supplies a production cost, TTFT or calibration-distribution claim.

## 7. Evidence promotion checklist

Before putting an external number into a release note, paper, dashboard or README benchmark table, require all applicable boxes:

- [ ] code revision frozen and recorded;
- [ ] protocol version recorded;
- [ ] dataset frozen, development-excluded and digest recorded where applicable;
- [ ] live provider/backend/engine deployment identity immutable and recorded;
- [ ] warmup/repeat protocol followed where required;
- [ ] no scope/build contamination;
- [ ] raw result artifact retained;
- [ ] result digest retained when produced by the runner;
- [ ] environment/hardware/database/engine metadata retained;
- [ ] human adjudication completed for any broader semantic-quality claim;
- [ ] evidence label matches the runner/protocol result exactly;
- [ ] statement does not promote a mechanism smoke into SOTA, SLA, real-project verification or general-quality evidence.

## 8. Stop conditions

Stop the run and do **not** publish a stronger claim when any of the following happens:

- held-out data was used for development or label generation;
- deployment identity cannot be recovered exactly;
- dataset digest or code revision changed mid-comparison;
- a required semantic backend was absent in any measured repeat;
- any forbidden/wrong-build asset contaminated a retrieval result;
- a GameAdapter replay store is unavailable or a ticket/request digest check fails;
- the PostgreSQL target is not disposable;
- a real-engine observation has not passed an independent Frozen Kernel verification step;
- an automatic anchor rubric is being presented as blind human semantic adjudication.

A failed gate is useful evidence about the system. Do not erase it by relabeling the run.
