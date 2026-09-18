from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Callable
from urllib.parse import urlsplit
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from worldforge.integrations.game_adapter import (
    FrozenKernelGameAdapterGateway,
    GameAdapterError,
    GameAdapterRequest,
    HttpGameAdapter,
    SqlGameAdapterReplayStore,
)
from worldforge.security import Principal
from worldforge.settings import settings
from .engine_probe_verifier import (
    EngineProbeVerificationError,
    evaluate_probe_snapshot,
)


_MAX_EVIDENCE_ITEM_BYTES = 6 * 1024 * 1024
_MAX_EVIDENCE_TOTAL_BYTES = 12 * 1024 * 1024
_EVIDENCE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_DEV_SIGNING_SECRET = secrets.token_bytes(32)
_ALLOWED_REQUESTS = {"logs", "snapshot", "screenshot"}
_RESULT_KIND_TO_REQUEST = {
    "log": "logs",
    "snapshot": "snapshot",
    "screenshot": "screenshot",
}


class GameAdapterConnectionRequest(BaseModel):
    endpoint: str = Field(default="http://127.0.0.1:9030", min_length=1, max_length=512)
    token: str | None = Field(default=None, max_length=4096)


class GameAdapterCaptureRequest(GameAdapterConnectionRequest):
    evidence_requests: list[str] = Field(
        default_factory=lambda: ["logs", "snapshot", "screenshot"],
        max_length=3,
    )
    require_screenshot: bool = False


def _flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _local_bridge_enabled() -> bool:
    return _flag("WORLDFORGE_ALLOW_LOCAL_GAME_ADAPTER", not settings.production)


def _signing_secret() -> bytes | str:
    configured = os.getenv("LINGJING_GAME_ADAPTER_SIGNING_SECRET", "").strip()
    if configured:
        if len(configured.encode("utf-8")) < 16:
            raise HTTPException(
                503,
                "LINGJING_GAME_ADAPTER_SIGNING_SECRET 至少需要 16 bytes",
            )
        return configured
    if settings.production:
        raise HTTPException(
            503,
            "生产环境启用本地 GameAdapter 时必须配置 LINGJING_GAME_ADAPTER_SIGNING_SECRET",
        )
    return _DEV_SIGNING_SECRET


def _normalize_loopback_endpoint(value: str) -> str:
    raw = str(value or "").strip().rstrip("/")
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("GameAdapter endpoint 端口无效") from exc
    if parsed.scheme != "http":
        raise ValueError("本地 GameAdapter 仅允许 http")
    if parsed.hostname != "127.0.0.1":
        raise ValueError("本地 GameAdapter 必须绑定 127.0.0.1")
    if parsed.username or parsed.password:
        raise ValueError("GameAdapter endpoint 不能包含 URL 凭证")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise ValueError("GameAdapter endpoint 只能填写 origin，例如 http://127.0.0.1:9030")
    if port is None or port < 1024 or port > 65535:
        raise ValueError("GameAdapter endpoint 必须包含 1024-65535 的本地端口")
    return f"http://127.0.0.1:{port}"


def _normalize_evidence_requests(values: list[str], *, require_screenshot: bool) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values or []:
        item = str(value or "").strip().lower()
        if item not in _ALLOWED_REQUESTS:
            raise ValueError(f"不支持的引擎证据类型: {item or '<empty>'}")
        if item not in normalized:
            normalized.append(item)
    if not normalized:
        normalized = ["logs", "snapshot"]
    if require_screenshot and "screenshot" not in normalized:
        normalized.append("screenshot")
    return tuple(normalized)


def _latest_scope(store, conversation_id: str, workspace_id: str) -> dict[str, Any]:
    latest = store.latest_job(conversation_id, workspace_id=workspace_id)
    if not latest:
        return {}
    payload = dict(latest.get("payload") or {})
    project_context = dict(payload.get("project_context") or {})
    return dict(project_context.get("scope") or {})


def _asset_descriptor(kind: str, data: bytes, meta: dict[str, Any]) -> tuple[str, str, str, dict[str, Any]]:
    if kind == "screenshot":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise GameAdapterError("adapter screenshot is not a PNG payload")
        width = int(meta.get("width") or 0)
        height = int(meta.get("height") or 0)
        return (
            "unity-camera-frame.png",
            "image/png",
            ".png",
            {"kind": "image", "valid": True, "width": width, "height": height},
        )
    text = data.decode("utf-8", errors="replace")
    lines = max(1, text.count("\n") + 1)
    preview = "\n".join(text.splitlines()[:8])[:4000]
    if kind == "snapshot":
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise GameAdapterError("adapter snapshot is not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise GameAdapterError("adapter snapshot must be a JSON object")
        return (
            "unity-editor-snapshot.json",
            "application/json",
            ".json",
            {"kind": "text", "valid": True, "lines": lines, "preview": preview},
        )
    return (
        "unity-console.log",
        "text/plain",
        ".log",
        {"kind": "text", "valid": True, "lines": lines, "preview": preview},
    )


def _validate_locator(endpoint: str, locator: str) -> str:
    base = urlsplit(endpoint)
    try:
        target = urlsplit(str(locator or ""))
        port = target.port
    except ValueError as exc:
        raise GameAdapterError("adapter evidence locator is invalid") from exc
    if (
        target.scheme != base.scheme
        or target.hostname != base.hostname
        or port != base.port
        or target.username
        or target.password
        or target.query
        or target.fragment
    ):
        raise GameAdapterError("adapter evidence locator escaped the configured loopback origin")
    prefix = "/v1/adapter/evidence/"
    if not target.path.startswith(prefix):
        raise GameAdapterError("adapter evidence locator is outside the evidence path")
    evidence_id = target.path[len(prefix):]
    if not _EVIDENCE_ID_RE.fullmatch(evidence_id):
        raise GameAdapterError("adapter evidence locator id is invalid")
    return str(locator)


async def _fetch_evidence_bytes(
    *,
    endpoint: str,
    token: str | None,
    evidence: tuple[dict[str, Any], ...],
    requested: tuple[str, ...],
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> list[dict[str, Any]]:
    requested_set = set(requested)
    headers = {"authorization": f"Bearer {token}"} if token else {}
    rows: list[dict[str, Any]] = []
    seen_result_kinds: set[str] = set()
    total = 0
    async with http_client_factory(timeout=15.0, follow_redirects=False) as client:
        for item in evidence:
            kind = str(item.get("kind") or "").strip().lower()
            request_kind = _RESULT_KIND_TO_REQUEST.get(kind)
            if not request_kind or request_kind not in requested_set:
                raise GameAdapterError("adapter returned unsolicited evidence kind")
            if kind in seen_result_kinds:
                raise GameAdapterError("adapter returned duplicate evidence kind")
            seen_result_kinds.add(kind)
            expected_sha = str(item.get("sha256") or "").strip().lower()
            if len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha):
                raise GameAdapterError("adapter evidence is missing a valid sha256")
            locator = _validate_locator(endpoint, str(item.get("locator") or ""))
            metadata = dict(item.get("meta") or {})
            async with client.stream("GET", locator, headers=headers) as response:
                if response.status_code != 200:
                    raise GameAdapterError(
                        f"adapter evidence fetch failed: HTTP {response.status_code}"
                    )
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > _MAX_EVIDENCE_ITEM_BYTES:
                            raise GameAdapterError("adapter evidence exceeds the per-item limit")
                    except ValueError:
                        pass
                payload = bytearray()
                async for chunk in response.aiter_bytes():
                    payload.extend(chunk)
                    if len(payload) > _MAX_EVIDENCE_ITEM_BYTES:
                        raise GameAdapterError("adapter evidence exceeds the per-item limit")
                    if total + len(payload) > _MAX_EVIDENCE_TOTAL_BYTES:
                        raise GameAdapterError("adapter evidence exceeds the capture total limit")
            data = bytes(payload)
            total += len(data)
            actual_sha = hashlib.sha256(data).hexdigest()
            header_sha = str(response.headers.get("x-lingjing-sha256") or "").strip().lower()
            if actual_sha != expected_sha or (header_sha and header_sha != actual_sha):
                raise GameAdapterError("adapter evidence sha256 mismatch")
            expected_mime = str(metadata.get("mime") or "").split(";", 1)[0].strip().lower()
            actual_mime = str(response.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            if expected_mime and actual_mime and expected_mime != actual_mime:
                raise GameAdapterError("adapter evidence content type mismatch")
            rows.append(
                {
                    "kind": kind,
                    "data": data,
                    "sha256": actual_sha,
                    "mime": actual_mime or expected_mime or "application/octet-stream",
                    "meta": metadata,
                }
            )
    return rows


def build_game_adapter_ingestion_router(
    *,
    store,
    storage,
    require_principal: Callable,
    adapter_factory: Callable[..., Any] = HttpGameAdapter,
    http_client_factory: Callable[..., Any] = httpx.AsyncClient,
) -> APIRouter:
    router = APIRouter()

    def require_editor(principal: Principal) -> None:
        if principal.role == "viewer":
            raise HTTPException(403, "只读成员不能连接或采集引擎证据")

    def require_local_bridge() -> None:
        if not _local_bridge_enabled():
            raise HTTPException(
                409,
                "当前部署未启用本机 GameAdapter。云端部署需要 outbound runner/relay，不能直接使用服务端 127.0.0.1。",
            )

    @router.post("/api/conversations/{conversation_id}/game-adapter/probe")
    async def probe_game_adapter(
        conversation_id: str,
        req: GameAdapterConnectionRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        require_editor(principal)
        require_local_bridge()
        try:
            conversation = store.get_conversation(
                conversation_id, workspace_id=principal.workspace_id
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        if conversation.get("archived_at") is not None:
            raise HTTPException(409, "已归档任务不能连接引擎")
        try:
            endpoint = _normalize_loopback_endpoint(req.endpoint)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        try:
            adapter = adapter_factory(endpoint, token=req.token, timeout_seconds=10.0)
            capabilities = await adapter.capabilities()
        except GameAdapterError as exc:
            raise HTTPException(409, str(exc)) from exc
        store.add_audit(
            request_id=getattr(request.state, "request_id", "game-adapter-probe"),
            action="game_adapter.probe",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type="conversation",
            resource_id=conversation_id,
            payload={
                "adapter_id": capabilities.adapter_id,
                "engine": capabilities.engine,
                "protocol_version": capabilities.protocol_version,
                "supports_screenshots": capabilities.supports_screenshots,
                "mutating_actions": capabilities.mutating_actions,
            },
        )
        return {
            "connected": True,
            "local_only": True,
            "capabilities": capabilities.to_dict(),
        }

    @router.post("/api/conversations/{conversation_id}/game-adapter/capture")
    async def capture_game_adapter_evidence(
        conversation_id: str,
        req: GameAdapterCaptureRequest,
        request: Request,
        principal: Principal = Depends(require_principal),
    ):
        require_editor(principal)
        require_local_bridge()
        try:
            conversation = store.get_conversation(
                conversation_id, workspace_id=principal.workspace_id
            )
        except KeyError as exc:
            raise HTTPException(404, "任务不存在") from exc
        if conversation.get("archived_at") is not None:
            raise HTTPException(409, "已归档任务不能采集引擎证据")
        if conversation.get("status") == "waiting_approval":
            raise HTTPException(409, "删除确认处理中，不能采集引擎证据")

        try:
            endpoint = _normalize_loopback_endpoint(req.endpoint)
            evidence_requests = _normalize_evidence_requests(
                req.evidence_requests,
                require_screenshot=req.require_screenshot,
            )
            adapter = adapter_factory(endpoint, token=req.token, timeout_seconds=20.0)
            capabilities = await adapter.capabilities()
            scope = _latest_scope(store, conversation_id, principal.workspace_id)
            replay_store = SqlGameAdapterReplayStore(
                store.engine,
                auto_create_schema=settings.auto_create_schema,
            )
            gateway = FrozenKernelGameAdapterGateway(
                _signing_secret(),
                replay_store=replay_store,
            )
            action = {
                "kind": "evidence.capture",
                "target": "current-conversation",
                "mutating": False,
            }
            action_id = f"capture-{uuid.uuid4().hex}"
            ticket = gateway.issue_ticket(
                adapter_id=capabilities.adapter_id,
                action_id=action_id,
                action=action,
                scope=scope,
                dry_run=True,
                evidence_requests=evidence_requests,
                ttl_seconds=30,
            )
            adapter_request = GameAdapterRequest(
                action_id=action_id,
                action=action,
                scope=scope,
                evidence_requests=evidence_requests,
                dry_run=True,
                ticket=ticket,
            )
            observation = await gateway.execute(adapter, adapter_request)
            if observation.status != "dry-run":
                raise GameAdapterError("adapter did not return a dry-run observation")
            fetched = await _fetch_evidence_bytes(
                endpoint=endpoint,
                token=req.token,
                evidence=observation.evidence,
                requested=evidence_requests,
                http_client_factory=http_client_factory,
            )
            if not fetched:
                raise GameAdapterError("adapter returned no retrievable evidence")
            if req.require_screenshot and not any(row["kind"] == "screenshot" for row in fetched):
                raise GameAdapterError("当前 Unity 场景没有可采集的 screenshot")
            probe_evaluations: list[dict[str, Any]] = []
            snapshot_row = next(
                (row for row in fetched if row["kind"] == "snapshot"),
                None,
            )
            if snapshot_row is not None:
                try:
                    probe_evaluations = evaluate_probe_snapshot(snapshot_row["data"])
                except EngineProbeVerificationError as exc:
                    raise GameAdapterError(str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except GameAdapterError as exc:
            raise HTTPException(409, str(exc)) from exc

        object_keys: list[str] = []
        pending_assets: list[dict[str, Any]] = []
        try:
            for row in fetched:
                name, declared_mime, suffix, media_meta = _asset_descriptor(
                    row["kind"], row["data"], row["meta"]
                )
                object_key = (
                    f"{principal.workspace_id}/assets/{uuid.uuid4().hex}/engine{suffix}"
                )
                storage.put_bytes(object_key, row["data"], declared_mime)
                object_keys.append(object_key)
                meta = {
                    **media_meta,
                    "source_type": "game-adapter",
                    "evidence_class": observation.evidence_class,
                    "adapter_id": observation.adapter_id,
                    "engine": observation.engine,
                    "ticket_id": observation.ticket_id,
                    "adapter_evidence_kind": row["kind"],
                    "sha256": row["sha256"],
                    "engine_object": row["meta"].get("engine_object"),
                    "scene": row["meta"].get("scene"),
                    "play_mode": row["meta"].get("play_mode"),
                    "engine_byte_size": row["meta"].get("byte_size"),
                }
                if row["kind"] == "snapshot" and probe_evaluations:
                    meta["probe_evaluations"] = probe_evaluations
                pending_assets.append(
                    {
                        "name": name,
                        "mime": declared_mime,
                        "path": object_key,
                        "size": len(row["data"]),
                        "meta": meta,
                        "storage_backend": storage.name,
                    }
                )
            assets = store.add_assets_batch(
                conversation_id,
                assets=pending_assets,
                workspace_id=principal.workspace_id,
                created_by=principal.user_id,
            )
        except Exception:
            for object_key in object_keys:
                try:
                    storage.delete(object_key)
                except Exception:
                    pass
            raise

        for asset in assets:
            asset["url"] = f"/api/assets/{asset['id']}/file"

        store.add_event(
            conversation_id,
            "engine.evidence.ingested",
            {
                "asset_ids": [asset["id"] for asset in assets],
                "adapter_id": observation.adapter_id,
                "engine": observation.engine,
                "ticket_id": observation.ticket_id,
                "evidence_class": observation.evidence_class,
                "scope": scope,
                "probe_evaluations": probe_evaluations,
            },
            workspace_id=principal.workspace_id,
        )
        if probe_evaluations:
            store.add_event(
                conversation_id,
                "engine.probe.evaluated",
                {
                    "ticket_id": observation.ticket_id,
                    "adapter_id": observation.adapter_id,
                    "engine": observation.engine,
                    "evaluations": probe_evaluations,
                    "authority": "contract-evaluation-only",
                },
                workspace_id=principal.workspace_id,
            )
        store.add_audit(
            request_id=getattr(request.state, "request_id", "game-adapter-capture"),
            action="game_adapter.evidence.ingest",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type="conversation",
            resource_id=conversation_id,
            payload={
                "asset_ids": [asset["id"] for asset in assets],
                "adapter_id": observation.adapter_id,
                "engine": observation.engine,
                "ticket_id": observation.ticket_id,
                "kinds": [row["kind"] for row in fetched],
                "scope": scope,
                "probe_outcomes": [
                    {
                        "probe_id": item["probe_id"],
                        "contract_id": item["contract_id"],
                        "outcome": item["outcome"],
                    }
                    for item in probe_evaluations
                ],
            },
        )
        return {
            "status": "ingested",
            "local_only": True,
            "evidence_class": observation.evidence_class,
            "verifier_status": observation.verifier_status,
            "probe_verifier_status": (
                "evaluated" if probe_evaluations else "not-applicable"
            ),
            "probe_evaluations": probe_evaluations,
            "canonical_write_allowed": observation.canonical_write_allowed,
            "before_snapshot_digest": observation.before_snapshot_digest,
            "after_snapshot_digest": observation.after_snapshot_digest,
            "assets": assets,
        }

    return router
