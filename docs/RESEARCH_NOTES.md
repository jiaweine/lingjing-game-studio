# Research notes behind the design

> Engineering research context only. These references are not shown in the customer workspace, are not used as product comparison claims, and do not imply performance superiority over any external system.

These references informed architecture choices; WorldForge is an independent implementation.

- DeepSeek Harness developer preview: pluginized models/tools/skills/sessions/sandboxes/storage/loops/scheduling/UI and append-only traceability. https://deepseek.com/harness/en/
- Orak: MCP-based plug-and-play game environments and agentic modules across 12 game genres. https://arxiv.org/abs/2506.03610
- BALROG: agentic LLM/VLM evaluation over challenging dynamic game environments. https://arxiv.org/abs/2411.13543
- lmgame-Bench: unified Gym-style game evaluation with perception, memory and reasoning scaffolds. https://arxiv.org/abs/2505.15146
- Harness-Bench: controlled evaluation of harness configurations under shared environments and budgets. https://arxiv.org/abs/2605.27922

## What WorldForge changes

WorldForge does not reproduce the "everything is a plugin" design as its primary differentiator. It makes the **environment state and its counterfactual forks** first-class. The runtime has explicit semantics for world-state snapshots, speculative branches, risk-adjusted branch selection, post-action verification, rollback, self-play curricula and regression-gated strategy evolution.

This is a domain-specific design choice for game AI and game QA rather than a claim of universal superiority over general-purpose coding harnesses.

Any future external performance comparison should follow the controlled protocol documented in [`BENCHMARKING.md`](BENCHMARKING.md) and should never be inferred from a feature checklist alone.
