from __future__ import annotations

from dataclasses import asdict, dataclass
import os
import re
from typing import Any, Mapping

from .context_budget import estimate_text_tokens

_AUTO_THRESHOLD_RATIO = 0.65
_MESSAGE_OVERHEAD_TOKENS = 4


@dataclass(frozen=True)
class NativeTokenDecision:
    provider_key: str
    mode: str
    should_count: bool
    reason: str
    estimated_text_tokens: int
    media_items: int
    safe_input_tokens: int | None
    limit_source: str | None

    def to_telemetry(self) -> dict[str, Any]:
        payload = asdict(self)
        return {f"native_token_{key}": value for key, value in payload.items()}


def _prefix(provider_key: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(provider_key or "provider")).strip("_")
    return f"LINGJING_{(normalized or 'PROVIDER').upper()}"


def native_token_mode(
    provider_key: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str:
    env = environ or os.environ
    raw = env.get(
        f"{_prefix(provider_key)}_NATIVE_TOKEN_COUNT",
        env.get("LINGJING_NATIVE_TOKEN_COUNT", "auto"),
    )
    value = str(raw or "auto").strip().lower()
    if value in {"1", "true", "yes", "on", "always"}:
        return "on"
    if value in {"0", "false", "no", "off", "never"}:
        return "off"
    return "auto"


def estimate_messages_tokens(messages: list[dict[str, Any]] | None) -> int:
    total = 0
    for message in messages or []:
        content = message.get("content", "")
        if isinstance(content, list):
            text = " ".join(
                str(item.get("text", ""))
                for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        else:
            text = str(content or "")
        total += estimate_text_tokens(text) + _MESSAGE_OVERHEAD_TOKENS
    return total


def decide_native_token_count(
    provider_key: str,
    *,
    messages: list[dict[str, Any]] | None,
    media_items: int,
    safe_input_tokens: int | None,
    limit_source: str | None,
    environ: Mapping[str, str] | None = None,
) -> NativeTokenDecision:
    """Choose whether a provider-native tokenizer call is worth its extra RTT.

    ``auto`` is intentionally sparse: no native call is made until a trustworthy input limit is
    known, and then only media-bearing requests or text requests already near the safe limit are
    counted. Operators can force ``on`` for calibration/diagnostics or ``off`` for latency-sensitive
    paths. This keeps exact counting a safety/escalation tool rather than a mandatory extra request.
    """
    mode = native_token_mode(provider_key, environ=environ)
    estimated = estimate_messages_tokens(messages)
    media_items = max(0, int(media_items))
    safe = int(safe_input_tokens) if safe_input_tokens and safe_input_tokens > 0 else None

    if mode == "off":
        return NativeTokenDecision(
            provider_key=str(provider_key),
            mode=mode,
            should_count=False,
            reason="operator-disabled",
            estimated_text_tokens=estimated,
            media_items=media_items,
            safe_input_tokens=safe,
            limit_source=limit_source,
        )
    if mode == "on":
        return NativeTokenDecision(
            provider_key=str(provider_key),
            mode=mode,
            should_count=True,
            reason="operator-forced",
            estimated_text_tokens=estimated,
            media_items=media_items,
            safe_input_tokens=safe,
            limit_source=limit_source,
        )
    if safe is None:
        return NativeTokenDecision(
            provider_key=str(provider_key),
            mode=mode,
            should_count=False,
            reason="no-trustworthy-input-limit",
            estimated_text_tokens=estimated,
            media_items=media_items,
            safe_input_tokens=None,
            limit_source=limit_source,
        )
    if media_items > 0:
        reason = "media-token-cost-needs-native-count"
        should_count = True
    else:
        threshold = max(256, int(safe * _AUTO_THRESHOLD_RATIO))
        should_count = estimated >= threshold
        reason = "estimated-text-near-limit" if should_count else "estimated-text-well-below-limit"
    return NativeTokenDecision(
        provider_key=str(provider_key),
        mode=mode,
        should_count=should_count,
        reason=reason,
        estimated_text_tokens=estimated,
        media_items=media_items,
        safe_input_tokens=safe,
        limit_source=limit_source,
    )


def native_count_exceeds_limit(input_tokens: int, decision: NativeTokenDecision) -> bool:
    return bool(
        decision.safe_input_tokens is not None
        and int(input_tokens) > int(decision.safe_input_tokens)
    )
