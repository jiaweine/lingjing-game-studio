# game-rd-mm-v1 annotation readiness audit

`workspace.json` is an authoring surface, not quality evidence. Use the readiness audit while humans are building the held-out corpus:

```bash
python scripts/multimodal_annotation_readiness.py \
  --workspace /path/to/game-rd-mm-v1/workspace.json \
  --output artifacts/annotation-readiness.json
```

The audit is read-only with respect to annotations. It does **not** generate or rewrite queries, relevance labels, temporal intervals, source groups, scope exclusions, annotator counts or adjudication.

Its output always carries:

```text
evidence_claim = none-annotation-readiness-audit
```

A green readiness report is still not a frozen corpus and is not retrieval/model quality evidence.

## Independent readiness dimensions

The report deliberately separates concepts that should not be collapsed into one boolean:

1. `coverage.annotation_complete` — all current cases contain the required human fields, at least two annotators and adjudication. This is independent of whether the held-out split has been sealed.
2. `coverage.development_excluded` — the operator has explicitly verified `heldout_policy.development_excluded=true`.
3. `coverage.protocol_coverage_ready` — aggregate counts reach the frozen v1 floors: 100 cases, 20 source groups, at least 20 target cases per modality, 20 temporal cases and 25 scope-negative cases.
4. `coverage.case_semantics_valid` — a draft-compiled manifest has no structural/case errors under the authoritative strict corpus validator. This catches issues such as a temporal gold interval beyond media duration or a target modality with no matching relevant asset before freeze.
5. `workspace_validation.authoring_freeze_ready` — the existing workspace validator has no structural errors or authoring/split blockers.
6. `coverage.ready_for_strict_freeze_attempt` — all of the relevant authoring, split, quantity and strict case-semantic checks above are ready. This only means it is worth attempting the existing strict freeze command; freeze still re-hashes files and applies every quality blocker.

The audit's `strict_case_semantic_preflight` compiles the current workspace as an **annotation draft**, invokes the same `validate_corpus` implementation used by freeze, and consumes only its `errors`. Expected draft blockers such as `frozen=false`, missing file verification and aggregate quality floors remain represented by the separate readiness dimensions instead of being misclassified as case errors.

## Deficits

`coverage.deficits` reports the exact remaining counts for:

- total cases;
- unique source groups;
- text/image/video/audio target cases independently;
- temporal-localization cases;
- scope-negative cases.

This is a planning aid, not an instruction to synthetically manufacture cases to hit quotas. The corpus still needs naturally collected game-R&D evidence and independent human labels.

## Annotation work queue

`cases.incomplete_cases` identifies each unfinished case and lists missing authoring fields. `cases.missing_field_counts` aggregates the backlog across:

- query;
- source group;
- target modalities;
- candidate asset references;
- relevance;
- forbidden/scope-label consistency;
- annotator count;
- adjudication;
- basic temporal interval validity.

The stricter case-semantic preflight then checks the compiled candidate/relevant/scope relationships against the authoritative corpus validator, including media-duration bounds and target-modality compatibility.

The report also shows candidate references, relevance labels, forbidden references and in-scope semantic hard-negative candidates. These are descriptive counts only.

## Coverage and provenance diagnostics

The asset section reports:

- source asset counts by modality;
- unique content hashes;
- exact duplicate-content groups;
- declared build/branch/commit/environment distributions;
- conflicting aliases on a single asset, such as `build_ref=1.4.7` together with `version=1.4.8`.

Declared scope metadata remains provenance. The readiness audit never turns those values into `scope_eligible`; case-level `forbidden_asset_ids` stays a human annotation decision and the freeze compiler remains the authority that materializes scope eligibility.

## Leakage audit

The audit warns when identical content hashes appear under multiple asset paths or are referenced by cases assigned to multiple `source_group` values. This can indicate duplicate capture leakage, but it can also be an intentional shared hard negative.

Therefore `leakage_audit.automatic_block=false`: the tool surfaces the evidence and requires human review rather than silently changing source groups, deleting assets or editing candidate labels.

If duplicate content crosses source groups, resolve the collection/split design before sealing the held-out set. Do not inspect retrieval-system results to decide the held-out labels.

## Optional gates

The CLI can be used as a human workflow gate:

```bash
# Fails with exit 2 until all current cases have complete human annotation fields.
python scripts/multimodal_annotation_readiness.py \
  --workspace /path/to/game-rd-mm-v1/workspace.json \
  --require-annotation-complete

# Fails with exit 3 until frozen-v1 aggregate quantity floors are covered.
python scripts/multimodal_annotation_readiness.py \
  --workspace /path/to/game-rd-mm-v1/workspace.json \
  --require-protocol-coverage

# Fails with exit 4 until annotation, split, quantity and strict case semantics are ready.
python scripts/multimodal_annotation_readiness.py \
  --workspace /path/to/game-rd-mm-v1/workspace.json \
  --require-ready-for-freeze
```

These gates are intentionally not wired to a real private corpus in public CI. Public CI only runs synthetic protocol smoke to verify the audit implementation and never treats that smoke as benchmark evidence.
