from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import os
import re
from typing import Mapping

_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ASCII_RUN_RE = re.compile(r"[A-Za-z0-9_./:+#@-]+")
_NONSPACE_RE = re.compile(r"\S")


@dataclass(frozen=True)
class ProviderContextBudgetProfile:
    """Operator-declared provider context budget used by ContextOS packing.

    Model context windows change frequently and custom OpenAI-compatible gateways may expose
    arbitrary models. Lingjing therefore does not hard-code vendor/model window tables here.
    Deployments may declare a provider profile through environment configuration; otherwise
    ContextOS keeps its existing character-budget fallback and reports that fallback explicitly.
    """

    provider_key: str
    model: str | None
    context_window_tokens: int | None
    history_budget_tokens: int | None
    output_reserve_tokens: int
    kernel_budget_tokens: int | None
    per_message_budget_tokens: int | None
    token_estimator: str = "multilingual-heuristic-v1"
    source: str = "unconfigured"

    @property
    def enabled(self) -> bool:
        return bool(self.history_budget_tokens and self.history_budget_tokens > 0)

    def to_dict(self) -> dict:
        return asdict(self)


def _positive_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _prefix(provider_key: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(provider_key or "auto")).strip("_")
    return f"LINGJING_{(normalized or 'AUTO').upper()}"


def load_provider_context_budget(
    provider_key: str,
    *,
    model: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> ProviderContextBudgetProfile:
    """Load a provider budget without guessing model-specific public limits.

    Supported variables for key ``openai`` are, for example:
    ``LINGJING_OPENAI_CONTEXT_WINDOW_TOKENS``,
    ``LINGJING_OPENAI_HISTORY_BUDGET_TOKENS``,
    ``LINGJING_OPENAI_OUTPUT_RESERVE_TOKENS``,
    ``LINGJING_OPENAI_KERNEL_BUDGET_TOKENS`` and
    ``LINGJING_OPENAI_PER_MESSAGE_BUDGET_TOKENS``.

    ``provider_key=auto`` uses the corresponding ``LINGJING_AUTO_*`` common-denominator
    profile. This is intentionally explicit: automatic routing must not pretend all configured
    providers share the same context window.
    """
    env = environ or os.environ
    prefix = _prefix(provider_key)
    context_window = _positive_int(env.get(f"{prefix}_CONTEXT_WINDOW_TOKENS"))
    explicit_history = _positive_int(env.get(f"{prefix}_HISTORY_BUDGET_TOKENS"))
    reserve = _positive_int(env.get(f"{prefix}_OUTPUT_RESERVE_TOKENS")) or 1800

    source_parts: list[str] = []
    if context_window is not None:
        source_parts.append("context-window")
    if explicit_history is not None:
        source_parts.append("history-budget")
    if env.get(f"{prefix}_OUTPUT_RESERVE_TOKENS"):
        source_parts.append("output-reserve")

    history_budget = explicit_history
    if history_budget is None and context_window is not None:
        usable = max(0, context_window - reserve)
        # History is only one part of the final request. Keep a conservative share for the
        # system prompt, current user prompt, tool/media representation and output reserve.
        if usable >= 1024:
            history_budget = max(512, int(math.floor(usable * 0.35)))
            source_parts.append("derived-history-share")

    if context_window is not None and history_budget is not None:
        history_budget = min(history_budget, max(0, context_window - reserve))
    if history_budget is not None and history_budget < 256:
        history_budget = None

    kernel_budget = _positive_int(env.get(f"{prefix}_KERNEL_BUDGET_TOKENS"))
    per_message_budget = _positive_int(
        env.get(f"{prefix}_PER_MESSAGE_BUDGET_TOKENS")
    )
    if history_budget is not None:
        if kernel_budget is None:
            kernel_budget = max(160, min(history_budget, int(history_budget * 0.48)))
        else:
            kernel_budget = min(kernel_budget, history_budget)
        if per_message_budget is None:
            per_message_budget = max(96, min(history_budget, int(history_budget * 0.40)))
        else:
            per_message_budget = min(per_message_budget, history_budget)

    return ProviderContextBudgetProfile(
        provider_key=str(provider_key or "auto"),
        model=model,
        context_window_tokens=context_window,
        history_budget_tokens=history_budget,
        output_reserve_tokens=reserve,
        kernel_budget_tokens=kernel_budget,
        per_message_budget_tokens=per_message_budget,
        source="+".join(source_parts) if source_parts else "unconfigured",
    )


def estimate_text_tokens(text: str) -> int:
    """Conservative dependency-free multilingual token estimate.

    This is not presented as an exact tokenizer count. CJK codepoints are charged roughly one
    token each; ASCII/code runs are charged by length with tighter granularity for identifiers,
    paths and numeric/code-heavy strings; remaining punctuation is charged separately. The
    estimator is deterministic and intentionally exposed in telemetry so a provider-native
    tokenizer can replace it later without changing the packing contract.
    """
    value = str(text or "")
    if not value:
        return 0

    cjk = len(_CJK_RE.findall(value))
    without_cjk = _CJK_RE.sub(" ", value)
    ascii_tokens = 0
    for match in _ASCII_RUN_RE.finditer(without_cjk):
        token = match.group(0)
        divisor = 3 if any(ch.isdigit() for ch in token) or any(
            marker in token for marker in "_./:+#@-"
        ) else 4
        ascii_tokens += max(1, math.ceil(len(token) / divisor))
    remainder = _ASCII_RUN_RE.sub("", without_cjk)
    punctuation = sum(
        1 for char in remainder if _NONSPACE_RE.match(char) is not None
    )
    return max(1, cjk + ascii_tokens + punctuation)
