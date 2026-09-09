# Long-horizon model evaluation protocol

`lingjing-long-horizon-model-v1` measures whether the final model answer actually benefits from the bounded ContextOS pack, instead of stopping at compiler-level retrieval metrics.

The comparison is controlled:

- `baseline_last8`: the historical product boundary, last eight user/assistant turns plus the current query;
- `contextos`: the same query and same provider/model, but with `ContextCompiler` structured task state plus its bounded selected history;
- same held-out cases;
- same provider configuration;
- same automatic anchor rubric.

The runner is:

```bash
python scripts/long_horizon_model_benchmark.py
```

With no arguments it runs only a four-case synthetic scorer/packing smoke and emits:

```text
evidence_class = synthetic-protocol-smoke-not-model-quality-evidence
quality_claim  = none-protocol-smoke
```

## Frozen held-out dataset

A quality-eligible dataset must declare:

```json
{
  "name": "lingjing-long-horizon-model-v1",
  "protocol_version": "1.0",
  "evidence_class": "human-authored-heldout",
  "frozen": true,
  "heldout_policy": {"development_excluded": true},
  "cases": []
}
```

The v1 floor is at least 40 cases, balanced with at least 10 each of:

- `qa`: retrieve a genuinely old confirmed fact;
- `update`: use the current/superseding value instead of an obsolete one;
- `abstention`: refuse to invent an unsupported answer;
- `workflow`: preserve an ordered multi-step project procedure.

At least 30 cases must put their annotated long-range anchor outside the legacy last-eight-turn window. This is a protocol floor, not a claim that 40 cases are sufficient for a broad SOTA statement.

Each case contains authoritative history, a current query, `long_range_anchor_index`, and a human-authored rubric. The rubric supports:

- `required_any`: groups of acceptable anchor aliases;
- `forbidden_any`: obsolete or unsafe claims that must not appear;
- `abstention_required` plus explicit acceptable abstention markers;
- `workflow_order`: ordered alias groups that must appear in sequence.

The scorer is deliberately transparent. It does not use another model as an uncalibrated judge.

## Live comparison

Configure one of Lingjing's existing providers, then run:

```bash
python scripts/long_horizon_model_benchmark.py \
  --dataset /private/bench/long-horizon-v1.json \
  --provider gemini \
  --require-quality-eligible-dataset \
  --output /private/bench/results/long-horizon-gemini.json
```

The runner records, for baseline and ContextOS:

- case pass rate;
- required-anchor recall;
- forbidden-claim rate;
- abstention compliance;
- workflow-order compliance;
- category-level pass rates;
- mean and p95 wall latency;
- provider request telemetry, including native token-count telemetry when available.

It also emits ContextOS-minus-baseline deltas for the main rubric dimensions.

Even on a frozen human-held-out corpus, the automatic result is labeled:

```text
evidence_class = measured-heldout-anchor-rubric-only
quality_claim = anchor-rubric-only-not-general-semantic-quality
```

That label is intentional. Exact anchor rubrics are useful for controlled regressions, but they do not replace blind human adjudication of answer quality, usefulness, nuance, or unsupported reasoning.

## What is not manufactured by this protocol

The repository does not ship the real held-out long-horizon corpus, a live-provider result, human blind judgments, or provider cost data. Those must come from a real experiment. A protocol smoke must never be copied into a benchmark table as if it were model-quality evidence.
