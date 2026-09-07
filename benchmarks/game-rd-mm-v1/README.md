# game-rd-mm-v1 held-out corpus protocol

This directory defines the evidence contract for the first real multimodal retrieval quality study. It intentionally does **not** contain a fabricated benchmark score or a synthetic corpus presented as production evidence.

This repository does not ship a real held-out game-R&D corpus. The images under `docs/assets/readme/` are product documentation screenshots, not eligible benchmark data.

## Prepare a real annotation workspace

Keep the real corpus outside the public repository by default:

```text
/path/to/game-rd-mm-v1/
  assets/
    session-001/...
    session-002/...
```

Scaffold the workspace from raw files:

```bash
python scripts/scaffold_multimodal_corpus.py \
  --corpus-root /path/to/game-rd-mm-v1
```

The scaffold step only inventories objective file properties:

- corpus-relative path;
- MIME and modality;
- byte size;
- SHA-256 content digest;
- duplicate-content groups;
- a stable asset id derived from path plus content digest.

It does **not** invent queries, relevance labels, temporal intervals, source groups, build/scope exclusions, annotator counts or adjudication. The generated `workspace.json` starts with zero cases and `heldout_policy.development_excluded=false`.

Use `case.template.json` as the authoring shape. Each case references `candidate_asset_ids` from the asset catalog. `forbidden_asset_ids` is only for candidate assets that are invalid by project/build scope; semantic hard negatives that are valid in scope stay candidates but are not forbidden.

After independent human annotation and adjudication, set `heldout_policy.development_excluded=true` only when that exclusion has actually been verified. Then freeze:

```bash
python scripts/freeze_multimodal_corpus.py \
  --workspace /path/to/game-rd-mm-v1/workspace.json \
  --frozen-at 2026-09-07T10:00:00Z
```

Freeze is fail-closed. It recompiles the benchmark manifest from the asset catalog, materializes scope flags from the human case labels, re-hashes every referenced file, runs the existing strict corpus validator, and refuses to write `manifest.json` unless the full `game-rd-mm-v1` quality protocol passes. A successful freeze only means **corpus-protocol eligible**; it is not model-quality evidence.

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

The authoring workspace keeps the asset catalog separate from case labels. Each case contains:

- `id`: stable case id;
- `query`: the human-authored user-style retrieval request;
- `source_group`: capture/session/project grouping used to audit dataset diversity;
- `target_modalities`: one or more of `text`, `image`, `video`, `audio`;
- `annotation.annotator_count` and `annotation.adjudicated`;
- `candidate_asset_ids`: ids from the scaffolded asset catalog;
- `relevant`: one or more gold asset ids, optionally with `[start, end]` for temporal evidence;
- `forbidden_asset_ids`: candidate assets that must never be returned because project/build scope excludes them;
- optional per-case `top_k`.

The freeze compiler turns candidate ids into the inline `assets` structure consumed by the benchmark. Semantic hard negatives that are valid in the current scope belong in `candidate_asset_ids` but **not** in `forbidden_asset_ids`.

## Freeze procedure

1. Collect the corpus without viewing retrieval results from the systems being compared.
2. Run the scaffold tool to hash and inventory the raw files.
3. Assign stable source groups and determine development/held-out separation before tuning on held-out labels.
4. Independently author queries and relevance/scope/temporal labels.
5. Adjudicate disagreements and record at least two annotators per case.
6. Verify `heldout_policy.development_excluded=true` only when true.
7. Run `scripts/freeze_multimodal_corpus.py` with an explicit ISO-8601 freeze timestamp.
8. Independently re-run:

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
