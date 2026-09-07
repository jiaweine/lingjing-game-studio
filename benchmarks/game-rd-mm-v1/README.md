# game-rd-mm-v1 held-out corpus protocol

This directory defines the evidence contract for the first real multimodal retrieval quality study.
It intentionally does **not** contain a fabricated benchmark score or a synthetic corpus presented
as production evidence.

## What qualifies as measured quality evidence

A run may use the `measured-live-retrieval-on-frozen-heldout-corpus` label only when the corpus
validator reports `strict_quality_eligible=true` and the benchmark talks to a live sidecar backend.

The frozen v1 floor is:

- at least 100 human-annotated held-out cases;
- at least 20 distinct `source_group` values;
- at least 20 target cases for each of text, image, video, and audio;
- at least 20 temporal-localization cases;
- at least 25 scope-negative cases with explicitly forbidden wrong-build assets;
- at least two annotators per case plus adjudication;
- every asset carries a lowercase SHA-256 content digest;
- every manifest path is corpus-relative;
- the benchmark verifies every referenced file against its digest before a quality claim is allowed;
- the manifest declares `heldout_policy.development_excluded=true`.

These are evidence-quality floors, not claims that 100 cases are statistically sufficient for a
general SOTA conclusion.

## Annotation unit

Each case contains:

- `id`: stable case id;
- `query`: the user-style retrieval request;
- `source_group`: capture/session/project grouping used to audit dataset diversity;
- `target_modalities`: one or more of `text`, `image`, `video`, `audio`;
- `annotation.annotator_count` and `annotation.adjudicated`;
- `assets`: candidate evidence, including `path`, `sha256`, MIME, modality metadata and scope flag;
- `relevant`: one or more gold asset ids, optionally with `[start, end]` for temporal evidence;
- `forbidden_asset_ids`: assets that must never be returned because project/build scope excludes them;
- optional per-case `top_k`.

Semantic hard negatives that are valid in the current scope belong in `assets` but **not** in
`forbidden_asset_ids`. The latter is reserved for evidence that is invalid by scope, so the
contamination metric retains a precise meaning.

## Freeze procedure

1. Collect and annotate the corpus without viewing retrieval results from the systems being compared.
2. Adjudicate disagreements.
3. Assign stable `source_group` values and confirm development/test separation.
4. Store media under corpus-relative paths and record SHA-256 for every referenced asset.
5. Set the top-level fields:
   - `name = game-rd-mm-v1`
   - `protocol_version = 1.0`
   - `annotation_guideline_version = game-rd-mm-v1-annotation-1`
   - `evidence_class = human-annotated-heldout`
   - `split = heldout`
   - `frozen = true`
   - `frozen_at = <ISO-8601 timestamp>`
   - `heldout_policy.development_excluded = true`
6. Run:

```bash
python scripts/validate_multimodal_corpus.py \
  --dataset /path/to/game-rd-mm-v1/manifest.json \
  --verify-files \
  --require-quality-eligible
```

Record the emitted `corpus_digest` with experiment outputs. Any annotation or manifest change creates
a new digest and must be treated as a new corpus revision.

## Controlled comparison

With a frozen corpus and live coordinator endpoints:

```bash
python scripts/multimodal_quality_matrix.py \
  --dataset /path/to/game-rd-mm-v1/manifest.json \
  --backend wemm=http://127.0.0.1:8910 \
  --backend wemm-rerank=http://127.0.0.1:8920 \
  --backend wemm-lco=http://127.0.0.1:8930 \
  --require-zero-contamination \
  --require-semantic-backend \
  --require-quality-eligible-corpus
```

The matrix freezes the same cases, Top-K policy and scorer across backends and reports deltas for
Recall@K, MRR, temporal IoU/hit rate, scope contamination, returned-media bytes, p50/p95 latency and
worker-lane time proxy.

No live endpoint and no frozen external corpus means protocol smoke only. Those runs are useful CI
coverage but are not retrieval-quality evidence.
