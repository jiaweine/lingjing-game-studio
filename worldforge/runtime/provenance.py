from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import inspect
import json
import os
from typing import Any, Mapping


PROVENANCE_SCHEMA_VERSION = 2
SOURCE_REVISION_ENV_VARS = (
    "WORLD_FORGE_SOURCE_REVISION",
    "GITHUB_SHA",
    "SOURCE_SHA",
    "VERCEL_GIT_COMMIT_SHA",
)
SAFE_SESSION_VERSION_KEYS = (
    "game_build_version",
    "game_build_id",
    "dataset_version",
    "eval_version",
    "model_version",
)


def _jsonable(value: Any) -> Any:
    """Convert runtime state into a deterministic JSON-compatible structure."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if is_dataclass(value):
        return _jsonable(asdict(value))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump())
    tolist = getattr(value, "tolist", None)
    if callable(tolist):
        return _jsonable(tolist())
    if isinstance(value, Mapping):
        return {
            str(key): _jsonable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted((_jsonable(item) for item in value), key=repr)
    if hasattr(value, "__dict__"):
        return _jsonable(vars(value))
    return repr(value)


def stable_digest(value: Any) -> str:
    payload = json.dumps(
        _jsonable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def source_revision(
    explicit: str | None = None,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    if explicit and explicit.strip():
        return explicit.strip()
    values = environ if environ is not None else os.environ
    for key in SOURCE_REVISION_ENV_VARS:
        value = values.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _component_source(component: Any) -> str:
    target = component if inspect.isclass(component) else type(component)
    try:
        return inspect.getsource(target)
    except (OSError, TypeError):
        return f"{target.__module__}.{target.__qualname__}"


def component_identity(component: Any) -> dict[str, str]:
    target = component if inspect.isclass(component) else type(component)
    return {
        "class": f"{target.__module__}.{target.__qualname__}",
        "fingerprint": stable_digest(_component_source(component)),
    }


def policy_identity(policy: Any) -> dict[str, Any]:
    card_dict = getattr(policy, "card_dict", None)
    card = card_dict() if callable(card_dict) else _jsonable(getattr(policy, "card", {}))
    state = {
        "card": card,
        "W1": getattr(policy, "W1", None),
        "b1": getattr(policy, "b1", None),
        "W2": getattr(policy, "W2", None),
        "b2": getattr(policy, "b2", None),
        "mean": getattr(policy, "mean", None),
        "scale": getattr(policy, "scale", None),
    }
    return {
        "name": card.get("name") if isinstance(card, dict) else None,
        "generation": card.get("generation") if isinstance(card, dict) else None,
        "fingerprint": stable_digest(state),
    }


def harness_identity(genome: Any) -> dict[str, Any]:
    data = _jsonable(genome)
    return {
        "genome_id": data.get("genome_id") if isinstance(data, dict) else None,
        "generation": data.get("generation") if isinstance(data, dict) else None,
        "fingerprint": stable_digest(data),
    }


def skill_bank_identity(skill_bank: Any) -> dict[str, Any]:
    snapshot = getattr(skill_bank, "snapshot", None)
    data = snapshot() if callable(snapshot) else _jsonable(skill_bank)
    count = len(data) if hasattr(data, "__len__") else None
    return {
        "count": count,
        "fingerprint": stable_digest(data),
    }


def memory_identity(memory: Any) -> dict[str, Any]:
    records = list(getattr(memory, "records", []))
    return {
        "record_count": len(records),
        "fingerprint": stable_digest(records),
    }


def build_runtime_provenance(
    *,
    kernel: Any,
    runtime_wrapper: Any | None = None,
    policy: Any,
    harness_genome: Any,
    skill_bank: Any,
    memory: Any,
    verifier: Any,
    scenario: Any,
    config: Any,
    session_meta: Mapping[str, Any] | None = None,
    explicit_source_revision: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Build a stable provenance envelope for one execution configuration.

    `kernel` identifies the frozen canonical-execution implementation. A separate
    `runtime_wrapper` identity records orchestration layers such as the self-evolving
    Harness wrapper, so audit data never conflates the two architectural boundaries.

    Only explicitly version-like session metadata is retained. User identifiers,
    tokens, prompts, and arbitrary session fields must not leak into provenance.
    """
    meta = dict(session_meta or {})
    version_meta = {
        key: meta[key]
        for key in SAFE_SESSION_VERSION_KEYS
        if meta.get(key) is not None
    }
    envelope = {
        "schema_version": PROVENANCE_SCHEMA_VERSION,
        "source_revision": source_revision(
            explicit_source_revision,
            environ=environ,
        ),
        "kernel": component_identity(kernel),
        "runtime_wrapper": (
            component_identity(runtime_wrapper)
            if runtime_wrapper is not None
            else None
        ),
        "policy": policy_identity(policy),
        "harness": harness_identity(harness_genome),
        "skill_bank": skill_bank_identity(skill_bank),
        "memory": memory_identity(memory),
        "verifier": component_identity(verifier),
        "scenario_fingerprint": stable_digest(scenario),
        "config_fingerprint": stable_digest(config),
        "versions": version_meta,
    }
    envelope["combined_fingerprint"] = stable_digest(envelope)
    return envelope
