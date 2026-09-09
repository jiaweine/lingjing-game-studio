from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import hashlib
import hmac
import json
import secrets
from threading import Lock
import time
from typing import Any, Protocol

import httpx
from sqlalchemy import Column, Float, Index, MetaData, String, Table, UniqueConstraint, delete, insert
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError


class GameAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class GameAdapterCapabilities:
    adapter_id: str
    engine: str
    engine_version: str | None = None
    protocol_version: str = "1.0"
    supports_dry_run: bool = True
    supports_snapshot: bool = True
    supports_logs: bool = True
    supports_screenshots: bool = False
    supports_video: bool = False
    supports_audio: bool = False
    mutating_actions: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GameAdapterTicket:
    ticket_id: str
    adapter_id: str
    action_id: str
    scope_digest: str
    request_digest: str
    issued_at: float
    expires_at: float
    nonce: str
    signature: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class GameAdapterRequest:
    action_id: str
    action: dict[str, Any]
    scope: dict[str, Any]
    evidence_requests: tuple[str, ...] = ("logs",)
    dry_run: bool = False
    ticket: GameAdapterTicket | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "action": dict(self.action),
            "scope": dict(self.scope),
            "evidence_requests": list(self.evidence_requests),
            "dry_run": bool(self.dry_run),
            "ticket": self.ticket.to_dict() if self.ticket else None,
        }


@dataclass(frozen=True)
class RawAdapterEvidence:
    kind: str
    locator: str
    sha256: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RawAdapterResult:
    adapter_id: str
    action_id: str
    ticket_id: str
    status: str
    before_snapshot_digest: str | None
    after_snapshot_digest: str | None
    evidence: tuple[RawAdapterEvidence, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)
    message: str = ""


@dataclass(frozen=True)
class GameAdapterObservation:
    adapter_id: str
    engine: str
    action_id: str
    ticket_id: str
    status: str
    before_snapshot_digest: str | None
    after_snapshot_digest: str | None
    evidence: tuple[dict[str, Any], ...]
    metrics: dict[str, Any]
    canonical_write_allowed: bool = False
    verifier_status: str = "not-run"
    evidence_class: str = "external-engine-observation-unverified"

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "evidence": [dict(row) for row in self.evidence],
        }


class GameAdapter(Protocol):
    async def capabilities(self) -> GameAdapterCapabilities: ...

    async def execute(self, request: GameAdapterRequest) -> RawAdapterResult: ...


class GameAdapterReplayStore(Protocol):
    """Atomic one-shot ticket consumption shared by one or more kernel gateways."""

    async def consume(
        self,
        *,
        ticket_id: str,
        nonce: str,
        expires_at: float,
        now: float,
    ) -> bool: ...


class InMemoryGameAdapterReplayStore:
    """Single-process replay protection for local/conformance use."""

    def __init__(self) -> None:
        self._tickets: dict[str, float] = {}
        self._nonces: dict[str, float] = {}
        self._lock = Lock()

    async def consume(
        self,
        *,
        ticket_id: str,
        nonce: str,
        expires_at: float,
        now: float,
    ) -> bool:
        with self._lock:
            expired_tickets = [key for key, expiry in self._tickets.items() if expiry < now]
            for key in expired_tickets:
                self._tickets.pop(key, None)
            expired_nonces = [key for key, expiry in self._nonces.items() if expiry < now]
            for key in expired_nonces:
                self._nonces.pop(key, None)
            if ticket_id in self._tickets or nonce in self._nonces:
                return False
            expiry = float(expires_at)
            self._tickets[str(ticket_id)] = expiry
            self._nonces[str(nonce)] = expiry
            return True


class SqlGameAdapterReplayStore:
    """Durable multi-process ticket replay protection backed by a SQLAlchemy engine.

    Production deployments should create the table through Alembic revision 20260909_0007.
    ``auto_create_schema`` exists for isolated integration tests or dedicated disposable stores.
    A database failure is fail-closed: execution is not dispatched when replay state cannot be
    atomically recorded.
    """

    def __init__(self, engine: Engine, *, auto_create_schema: bool = False) -> None:
        self.engine = engine
        self.metadata = MetaData()
        self.replays = Table(
            "game_adapter_ticket_replays",
            self.metadata,
            Column("ticket_id", String(64), primary_key=True),
            Column("nonce", String(64), nullable=False),
            Column("expires_at", Float, nullable=False),
            Column("consumed_at", Float, nullable=False),
            UniqueConstraint("nonce", name="uq_game_adapter_ticket_replays_nonce"),
        )
        Index("ix_game_adapter_ticket_replays_expires_at", self.replays.c.expires_at)
        if auto_create_schema:
            self.metadata.create_all(self.engine, tables=[self.replays])

    def _consume_sync(
        self,
        *,
        ticket_id: str,
        nonce: str,
        expires_at: float,
        now: float,
    ) -> bool:
        try:
            with self.engine.begin() as connection:
                # Once a ticket is expired the gateway rejects it before this store is called,
                # so removing old rows cannot make an expired ticket replayable.
                connection.execute(delete(self.replays).where(self.replays.c.expires_at < now))
                connection.execute(
                    insert(self.replays).values(
                        ticket_id=str(ticket_id),
                        nonce=str(nonce),
                        expires_at=float(expires_at),
                        consumed_at=float(now),
                    )
                )
            return True
        except IntegrityError:
            return False
        except SQLAlchemyError as exc:
            raise GameAdapterError("game adapter replay store unavailable") from exc

    async def consume(
        self,
        *,
        ticket_id: str,
        nonce: str,
        expires_at: float,
        now: float,
    ) -> bool:
        return await asyncio.to_thread(
            self._consume_sync,
            ticket_id=ticket_id,
            nonce=nonce,
            expires_at=expires_at,
            now=now,
        )


def _canonical(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _scope_digest(scope: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(dict(scope or {}))).hexdigest()


def _request_digest(
    action: dict[str, Any],
    *,
    dry_run: bool,
    evidence_requests: tuple[str, ...] | list[str],
) -> str:
    return hashlib.sha256(
        _canonical(
            {
                "action": dict(action or {}),
                "dry_run": bool(dry_run),
                "evidence_requests": [str(item) for item in evidence_requests],
            }
        )
    ).hexdigest()


def _ticket_message(
    *,
    ticket_id: str,
    adapter_id: str,
    action_id: str,
    scope_digest: str,
    request_digest: str,
    issued_at: float,
    expires_at: float,
    nonce: str,
) -> bytes:
    return _canonical(
        {
            "ticket_id": ticket_id,
            "adapter_id": adapter_id,
            "action_id": action_id,
            "scope_digest": scope_digest,
            "request_digest": request_digest,
            "issued_at": round(float(issued_at), 6),
            "expires_at": round(float(expires_at), 6),
            "nonce": nonce,
        }
    )


class FrozenKernelGameAdapterGateway:
    """Capability boundary between external game engines and the Frozen Kernel.

    An adapter is an actuator + evidence source, never a canonical-state authority. Every
    execution requires a short-lived HMAC ticket bound to adapter, action id, exact request
    semantics and scope. Remote evidence provenance is sanitized at this boundary: the adapter
    cannot claim verifier/system origin, and the result remains
    ``external-engine-observation-unverified`` until the real kernel verifier independently
    consumes it.

    The default replay store is deliberately process-local for conformance/development. Pass a
    durable shared ``GameAdapterReplayStore`` (normally ``SqlGameAdapterReplayStore``) whenever
    more than one kernel process can dispatch external-engine work.
    """

    def __init__(
        self,
        signing_secret: bytes | str | None = None,
        *,
        replay_store: GameAdapterReplayStore | None = None,
    ) -> None:
        if signing_secret is None:
            signing_secret = secrets.token_bytes(32)
        if isinstance(signing_secret, str):
            signing_secret = signing_secret.encode("utf-8")
        if len(signing_secret) < 16:
            raise ValueError("game adapter signing secret must be at least 16 bytes")
        self._secret = bytes(signing_secret)
        self._replay_store = replay_store or InMemoryGameAdapterReplayStore()

    def issue_ticket(
        self,
        *,
        adapter_id: str,
        action_id: str,
        action: dict[str, Any],
        scope: dict[str, Any],
        dry_run: bool,
        evidence_requests: tuple[str, ...] | list[str] = ("logs",),
        ttl_seconds: float = 30.0,
        now: float | None = None,
    ) -> GameAdapterTicket:
        issued = float(time.time() if now is None else now)
        ttl = max(1.0, min(300.0, float(ttl_seconds)))
        expires = issued + ttl
        ticket_id = f"ga-{secrets.token_hex(10)}"
        nonce = secrets.token_hex(16)
        scope_digest = _scope_digest(scope)
        request_digest = _request_digest(
            action,
            dry_run=dry_run,
            evidence_requests=evidence_requests,
        )
        message = _ticket_message(
            ticket_id=ticket_id,
            adapter_id=str(adapter_id),
            action_id=str(action_id),
            scope_digest=scope_digest,
            request_digest=request_digest,
            issued_at=issued,
            expires_at=expires,
            nonce=nonce,
        )
        signature = hmac.new(self._secret, message, hashlib.sha256).hexdigest()
        return GameAdapterTicket(
            ticket_id=ticket_id,
            adapter_id=str(adapter_id),
            action_id=str(action_id),
            scope_digest=scope_digest,
            request_digest=request_digest,
            issued_at=issued,
            expires_at=expires,
            nonce=nonce,
            signature=signature,
        )

    def _verify_ticket(
        self,
        ticket: GameAdapterTicket,
        *,
        adapter_id: str,
        action_id: str,
        action: dict[str, Any],
        scope: dict[str, Any],
        dry_run: bool,
        evidence_requests: tuple[str, ...] | list[str],
        now: float,
    ) -> None:
        if ticket.adapter_id != adapter_id or ticket.action_id != action_id:
            raise GameAdapterError("adapter ticket identity mismatch")
        if ticket.scope_digest != _scope_digest(scope):
            raise GameAdapterError("adapter ticket scope mismatch")
        if ticket.request_digest != _request_digest(
            action,
            dry_run=dry_run,
            evidence_requests=evidence_requests,
        ):
            raise GameAdapterError("adapter ticket request mismatch")
        if float(ticket.expires_at) < now or float(ticket.issued_at) > now + 5.0:
            raise GameAdapterError("adapter ticket expired or not yet valid")
        expected = hmac.new(
            self._secret,
            _ticket_message(
                ticket_id=ticket.ticket_id,
                adapter_id=ticket.adapter_id,
                action_id=ticket.action_id,
                scope_digest=ticket.scope_digest,
                request_digest=ticket.request_digest,
                issued_at=ticket.issued_at,
                expires_at=ticket.expires_at,
                nonce=ticket.nonce,
            ),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, ticket.signature):
            raise GameAdapterError("adapter ticket signature invalid")

    async def execute(
        self,
        adapter: GameAdapter,
        request: GameAdapterRequest,
        *,
        now: float | None = None,
    ) -> GameAdapterObservation:
        capabilities = await adapter.capabilities()
        ticket = request.ticket
        if ticket is None:
            raise GameAdapterError("external game execution requires a Frozen Kernel ticket")
        current = float(time.time() if now is None else now)
        self._verify_ticket(
            ticket,
            adapter_id=capabilities.adapter_id,
            action_id=request.action_id,
            action=request.action,
            scope=request.scope,
            dry_run=request.dry_run,
            evidence_requests=request.evidence_requests,
            now=current,
        )
        if request.dry_run and not capabilities.supports_dry_run:
            raise GameAdapterError("adapter does not support dry-run execution")
        if not request.dry_run and not capabilities.mutating_actions:
            raise GameAdapterError("adapter has not declared mutating action capability")

        consumed = await self._replay_store.consume(
            ticket_id=ticket.ticket_id,
            nonce=ticket.nonce,
            expires_at=ticket.expires_at,
            now=current,
        )
        if not consumed:
            raise GameAdapterError("adapter ticket replay rejected")
        # Consume before dispatch. A timeout is ambiguous and must require a new explicit
        # kernel decision/ticket rather than silently retrying a potentially mutating call.

        raw = await adapter.execute(request)
        if raw.adapter_id != capabilities.adapter_id:
            raise GameAdapterError("adapter result identity mismatch")
        if raw.action_id != request.action_id or raw.ticket_id != ticket.ticket_id:
            raise GameAdapterError("adapter result does not match execution ticket")
        if raw.status not in {"succeeded", "failed", "rejected", "dry-run"}:
            raise GameAdapterError(f"unsupported adapter result status: {raw.status}")
        if capabilities.supports_snapshot and raw.status in {"succeeded", "dry-run"}:
            if not raw.before_snapshot_digest or not raw.after_snapshot_digest:
                raise GameAdapterError("snapshot-capable adapter omitted before/after digest")

        evidence: list[dict[str, Any]] = []
        for item in raw.evidence[:64]:
            kind = str(item.kind or "artifact")[:64]
            locator = str(item.locator or "").strip()[:4000]
            if not locator:
                continue
            sha = str(item.sha256 or "").strip().lower() or None
            if sha is not None and (
                len(sha) != 64 or any(ch not in "0123456789abcdef" for ch in sha)
            ):
                raise GameAdapterError("adapter evidence sha256 is invalid")
            # Do not copy arbitrary remote provenance fields such as source_type=verifier.
            safe_meta = {
                key: value
                for key, value in dict(item.metadata or {}).items()
                if key in {
                    "mime",
                    "size",
                    "duration",
                    "start",
                    "end",
                    "frame",
                    "stream",
                    "engine_object",
                }
            }
            evidence.append(
                {
                    "kind": kind,
                    "locator": locator,
                    "sha256": sha,
                    "meta": safe_meta,
                    "provenance": {
                        "source_type": "game-adapter",
                        "adapter_id": capabilities.adapter_id,
                        "engine": capabilities.engine,
                        "ticket_id": ticket.ticket_id,
                        "verified": False,
                    },
                }
            )

        return GameAdapterObservation(
            adapter_id=capabilities.adapter_id,
            engine=capabilities.engine,
            action_id=request.action_id,
            ticket_id=ticket.ticket_id,
            status=raw.status,
            before_snapshot_digest=raw.before_snapshot_digest,
            after_snapshot_digest=raw.after_snapshot_digest,
            evidence=tuple(evidence),
            metrics=dict(raw.metrics or {}),
        )


class HttpGameAdapter:
    """Reference HTTP transport for Unity/Unreal/custom engine bridge processes."""

    def __init__(
        self,
        endpoint: str,
        *,
        token: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.endpoint = str(endpoint).rstrip("/")
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("game adapter endpoint must be http(s)")
        self.token = token
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self._capabilities: GameAdapterCapabilities | None = None

    def _headers(self) -> dict[str, str]:
        headers = {"content-type": "application/json"}
        if self.token:
            headers["authorization"] = f"Bearer {self.token}"
        return headers

    async def capabilities(self) -> GameAdapterCapabilities:
        if self._capabilities is not None:
            return self._capabilities
        try:
            async with httpx.AsyncClient(timeout=min(10.0, self.timeout_seconds)) as client:
                response = await client.get(
                    f"{self.endpoint}/v1/adapter/capabilities",
                    headers=self._headers(),
                )
        except httpx.HTTPError as exc:
            raise GameAdapterError("game adapter capabilities request failed") from exc
        if response.status_code >= 400:
            raise GameAdapterError(
                f"game adapter capabilities failed: HTTP {response.status_code}"
            )
        try:
            payload = dict(response.json())
            capabilities = GameAdapterCapabilities(
                adapter_id=str(payload["adapter_id"]),
                engine=str(payload["engine"]),
                engine_version=(str(payload["engine_version"]) if payload.get("engine_version") else None),
                protocol_version=str(payload.get("protocol_version") or "1.0"),
                supports_dry_run=bool(payload.get("supports_dry_run", True)),
                supports_snapshot=bool(payload.get("supports_snapshot", True)),
                supports_logs=bool(payload.get("supports_logs", True)),
                supports_screenshots=bool(payload.get("supports_screenshots", False)),
                supports_video=bool(payload.get("supports_video", False)),
                supports_audio=bool(payload.get("supports_audio", False)),
                mutating_actions=bool(payload.get("mutating_actions", False)),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GameAdapterError("invalid game adapter capabilities payload") from exc
        if capabilities.protocol_version != "1.0":
            raise GameAdapterError(
                f"unsupported game adapter protocol {capabilities.protocol_version!r}"
            )
        self._capabilities = capabilities
        return capabilities

    async def execute(self, request: GameAdapterRequest) -> RawAdapterResult:
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.post(
                    f"{self.endpoint}/v1/adapter/execute",
                    headers=self._headers(),
                    json=request.to_dict(),
                )
        except httpx.HTTPError as exc:
            raise GameAdapterError("game adapter execution request failed") from exc
        if response.status_code >= 400:
            raise GameAdapterError(
                f"game adapter execution failed: HTTP {response.status_code}"
            )
        try:
            payload = dict(response.json())
            evidence = tuple(
                RawAdapterEvidence(
                    kind=str(row.get("kind") or "artifact"),
                    locator=str(row.get("locator") or ""),
                    sha256=(str(row.get("sha256")) if row.get("sha256") else None),
                    metadata=dict(row.get("metadata") or {}),
                )
                for row in list(payload.get("evidence") or [])[:64]
                if isinstance(row, dict)
            )
            return RawAdapterResult(
                adapter_id=str(payload["adapter_id"]),
                action_id=str(payload["action_id"]),
                ticket_id=str(payload["ticket_id"]),
                status=str(payload["status"]),
                before_snapshot_digest=(
                    str(payload["before_snapshot_digest"])
                    if payload.get("before_snapshot_digest")
                    else None
                ),
                after_snapshot_digest=(
                    str(payload["after_snapshot_digest"])
                    if payload.get("after_snapshot_digest")
                    else None
                ),
                evidence=evidence,
                metrics=dict(payload.get("metrics") or {}),
                message=str(payload.get("message") or ""),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GameAdapterError("invalid game adapter execution payload") from exc


class SyntheticContractAdapter:
    """CI-only adapter for protocol mechanics; never real engine evidence."""

    def __init__(self, adapter_id: str = "synthetic-contract") -> None:
        self._capabilities = GameAdapterCapabilities(
            adapter_id=adapter_id,
            engine="synthetic",
            engine_version="contract-smoke",
            supports_dry_run=True,
            supports_snapshot=True,
            supports_logs=True,
            supports_screenshots=True,
            mutating_actions=False,
        )
        self.calls = 0

    async def capabilities(self) -> GameAdapterCapabilities:
        return self._capabilities

    async def execute(self, request: GameAdapterRequest) -> RawAdapterResult:
        self.calls += 1
        digest = hashlib.sha256(_canonical(request.action)).hexdigest()
        return RawAdapterResult(
            adapter_id=self._capabilities.adapter_id,
            action_id=request.action_id,
            ticket_id=request.ticket.ticket_id if request.ticket else "missing",
            status="dry-run" if request.dry_run else "rejected",
            before_snapshot_digest=digest,
            after_snapshot_digest=digest,
            evidence=(
                RawAdapterEvidence(
                    kind="log",
                    locator=f"synthetic://{request.action_id}/log",
                    sha256=hashlib.sha256(b"synthetic-contract-log").hexdigest(),
                    metadata={
                        "mime": "text/plain",
                        "source_type": "verifier",  # must be discarded by gateway
                    },
                ),
            ),
            metrics={"synthetic": True},
        )
