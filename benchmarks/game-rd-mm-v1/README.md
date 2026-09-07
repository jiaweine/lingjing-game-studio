# game-rd-mm-v1 held-out corpus protocol

This directory defines the evidence contract for the first real multimodal retrieval quality study. It intentionally does **not** contain a fabricated benchmark score or a synthetic corpus presented as production evidence.

## What qualifies as measured quality evidence

Measured evidence requires **both** a strict corpus and a strict run.

The corpus validator must report `strict_quality_eligible=true`. The frozen v1 corpus floor is:

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

The experiment run must additionally:

- talk to at least one live semantic backend;
- identify every live deployment with an immutable operator-supplied deployment id;
- execute at least one full-corpus warmup and three measured full-corpus repeats;
- observe a semantic backend in every measured repeat;
- keep scope/build contamination exactly zero;
- emit the frozen corpus digest, benchmark-code revision, repeat protocol and result digest.

These are evidence-quality floors, not claims that 100 cases or three repeats are statistically sufficient for a general SOTA conclusion.

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

Semantic hard negatives that are valid in the current scope belong in `assets` but **not** in `forbidden_asset_ids`. The latter is reserved for evidence that is invalid by scope, so the contamination metric retains a precise meaning.

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

Record the emitted `corpus_digest` with experiment outputs. Any annotation or manifest change creates a new digest and must be treated as a new corpus revision.

## Controlled comparison

With a frozen corpus and live coordinator endpoints:

```bash
python scripts/multimodal_quality_matrix.py \
  --dataset /path/to/game-rd-mm-v1/manifest.json \
  --backend wemm=http://127.0.0.1:8910 \
  --deployment 'wemm=retriever@sha256:...;wemm=<model-revision>' \
  --backend wemm-rerank=http://127.0.0.1:8920 \
  --deployment 'wemm-rerank=retriever@sha256:...;wemm=<revision>;reranker=<revision>' \
  --backend wemm-lco=http://127.0.0.1:8930 \
  --deployment 'wemm-lco=retriever@sha256:...;wemm=<revision>;lco=<revision>' \
  --warmup-repeats 1 \
  --repeats 3 \
  --output artifacts/game-rd-mm-v1-matrix.json \
  --require-zero-contamination \
  --require-semantic-backend \
  --require-quality-eligible-corpus \
  --require-measured-quality-run
```

The matrix keeps the same cases, Top-K policy and scorer across backends. Backend execution order is rotated deterministically between full-corpus passes so one deployment is not always measured first. Headline metrics aggregate all measured repeats rather than choosing a favorable run.

The result artifact records the corpus digest, exact benchmark-code revision, immutable deployment ids, warmup/repeat counts, cache-state semantics and a canonical result digest. Store that JSON next to any plots or tables derived from it.

No live endpoint and no frozen external corpus means protocol smoke only. Those runs are useful CI coverage but are not retrieval-quality evidence.
