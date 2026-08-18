# Research notes behind the design

> Engineering research context only. These references are not shown in the customer workspace, are not product-comparison claims, and do not imply performance superiority over any external system.

WorldForge is an independent implementation. We reproduce or adapt mechanisms only where the repository has corresponding code and tests; directional references are labeled as such.

## 2026 self-evolving harness research

### Adaptive Auto-Harness

- Paper: https://arxiv.org/abs/2606.01770
- Code: https://github.com/A-EVO-Lab/AdaptiveHarness
- Status: 2026 public research + open source.

The work treats prompts, skills, tools, memories and supporting infrastructure as harness optimization surfaces and studies sustained improvement on open-ended task streams with a stateful evolver, harness tree and solve-time routing.

WorldForge adapts the broad idea of a persistent evolvable harness surface, but uses its own `HarnessGenome`, world-state phenotype, game-R&D evaluator, semantic archive and frozen promotion kernel.

### Self-Evolving Agent Harnesses via Gated Semantic Quality-Diversity

- Paper: https://arxiv.org/abs/2607.13683
- Status: 2026 public research.

The key design principle we adopt is **proposal-credit separation**: the component proposing a harness edit does not own deterministic measurement or sealed-test credit. The paper also motivates a categorical WHERE × WHY quality-diversity archive.

WorldForge maps that idea to game-R&D trajectory pathology cells and uses deterministic shadow evaluation + paired-bootstrap held-out credit.

### SHAPER

- Paper: https://arxiv.org/abs/2608.06755
- Status: 2026-08 public research.

SHAPER studies joint evolution of reusable skills and context-code harnesses. WorldForge's current Skill / Memory / Topology Genome direction is compatible with that research trend, but **this repository does not claim to reproduce SHAPER**.

## Published conference foundations

### ADAS / Automated Design of Agentic Systems

- OpenReview: https://openreview.net/forum?id=VbI7wVEy0r
- Code: https://github.com/ShengranHu/ADAS
- Status: ICLR 2025 Poster.

ADAS motivates searching over agentic system design rather than only tuning a fixed prompt. WorldForge adapts this principle by making Specialist topology, gates and recombination part of the searchable Harness program surface.

### Promptbreeder

- Paper: https://proceedings.mlr.press/v235/fernando24a.html
- Status: ICML 2024.

Promptbreeder evolves both task prompts and the mutation prompts that improve them. WorldForge adapts the self-referential idea at the Harness level: operator logits, mutation sigma, temperature and exploration are themselves part of the Genome.

## Open-source harness engineering baselines

### Pi

- Repository: https://github.com/badlogic/pi-mono

Pi is used as an engineering reference for a compact, extensible coding-agent harness and session/tool infrastructure. WorldForge does not copy Pi's product or architecture; it targets a stateful game-R&D Runtime with independent verification and Harness generation.

### DeerFlow 2.0

- Repository: https://github.com/bytedance/deer-flow

ByteDance describes DeerFlow 2.0 as an open-source long-horizon super-agent harness with sub-agents, memory, sandboxes, tools and extensible skills. It is a useful engineering baseline for long-running task infrastructure.

WorldForge's differentiator is not “more plugins”; it is explicit world-state ownership, counterfactual clone execution, finding-vs-safety verification and sealed Harness self-evolution.

### DeepSeek Agent ecosystem

- Official repository: https://github.com/deepseek-ai/awesome-deepseek-agent

DeepSeek's official public repository is a curated set of guides for integrating DeepSeek models into popular agent and coding-assistant tools. We use it only as a model-integration ecosystem reference.

**We do not claim that a third-party repository named `deepseek-harness` is an official DeepSeek unified Harness Runtime.** No such official unified Runtime is used as a performance baseline here.

## WorldForge-specific synthesis

The current game-R&D algorithm combines:

1. evidence-linked Harness Genome edits;
2. true antithetic evolutionary search;
3. behavior-plateau detection with mutation-scale escalation;
4. cross-case stable-elite refinement;
5. train-only minimum-effective-edit trust region;
6. semantic WHERE × WHY Pareto-QD archive;
7. sealed held-out paired-bootstrap credit;
8. frozen Verifier / evaluator / promotion authority.

The game-specific contribution is the combination of a world-state phenotype with a diagnostic-aware evaluator: anomaly findings are useful QA evidence, while rollback/replan/critical invariants remain safety failures.

Any future external performance comparison must follow the controlled protocol in [`BENCHMARKING.md`](BENCHMARKING.md). Feature checklists or different underlying models are not valid evidence of superiority.
