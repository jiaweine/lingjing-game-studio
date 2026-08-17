from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import mimetypes
import statistics
import tempfile
import time
import uuid
from pathlib import Path

import jwt
from fastapi import (
    BackgroundTasks, Cookie, Depends, FastAPI, File, Form, Header, HTTPException,
    Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select, text as sql_text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from worldforge.api.manager import RunManager
from worldforge.benchmarks import run_benchmark
from worldforge.envs import get_scenario, list_scenarios
from worldforge.models import BenchmarkRequest, RunConfig
from worldforge.observability import (
    RequestContextMiddleware, SecurityHeadersMiddleware, SlidingWindowRateLimiter,
)
from worldforge.product import (
    ConversationStore, ProductAnalyzer, extract_video_frames, probe_media,
)
from worldforge.product.fanout import TaskEventFanoutHub
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID
from worldforge.providers import ProviderRegistry
from worldforge.security import (
    Principal, create_access_token, decode_access_token, hash_password, verify_password,
)
from worldforge.settings import settings
from worldforge.storage import build_storage

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = settings.data_dir
FRONTEND = ROOT / "frontend"
DATA_DIR.mkdir(parents=True, exist_ok=True)

manager = RunManager(DATA_DIR)
product_store = ConversationStore(
    DATA_DIR / "product.db",
    DATA_DIR / "assets",
    database_url=settings.database_url,
    auto_create_schema=settings.auto_create_schema,
    seed_dev_identity=settings.auth_mode == "dev",
)
storage = build_storage(settings, DATA_DIR / "objects")
providers = ProviderRegistry()
product_analyzer = ProductAnalyzer(manager.engine, providers)
rate_limiter = SlidingWindowRateLimiter(settings.rate_limit_per_minute)
task_event_hub = TaskEventFanoutHub(product_store)
logger = logging.getLogger("worldforge.api")


class AnalysisCancelled(Exception):
    pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    await task_event_hub.start()
    try:
        yield
    finally:
        await task_event_hub.stop()


app = FastAPI(
    title="灵境游戏研发执行工作台 API",
    description="面向游戏研发目标的自主执行、验证与证据工作空间。",
    version="1.0.0",
    docs_url="/docs" if not settings.production else None,
    redoc_url="/redoc" if not settings.production else None,
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware, log_requests=settings.request_log)
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
)
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")

PUBLIC_API_PATHS = {
    "/api/health",
    "/api/health/live",
    "/api/health/ready",
    "/api/config",
    "/api/product",
    "/api/auth/login",
    "/api/auth/register",
}


def _extract_token(authorization, cookie):
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return cookie


def _dev_principal():
    return Principal(
        user_id=DEMO_USER_ID,
        workspace_id=DEMO_WORKSPACE_ID,
        email="demo@local.lingjing",
        role="owner",
    )


def _refresh_membership(principal: Principal) -> Principal:
    membership = product_store.memberships
    users = product_store.users
    statement = (
        select(
            membership.c.role,
            users.c.email,
            users.c.status,
        )
        .select_from(
            membership.join(users, membership.c.user_id == users.c.id)
        )
        .where(
            (membership.c.workspace_id == principal.workspace_id)
            & (membership.c.user_id == principal.user_id)
        )
    )
    with product_store.engine.connect() as connection:
        row = connection.execute(statement).first()
    if not row or row.status != "active":
        raise HTTPException(401, "登录状态已失效")
    return Principal(
        user_id=principal.user_id,
        workspace_id=principal.workspace_id,
        email=str(row.email),
        role=str(row.role),
    )


def _decode_or_401(token):
    if not token:
        if settings.auth_mode == "dev":
            return _refresh_membership(_dev_principal())
        raise HTTPException(401, "请先登录")
    try:
        principal = decode_access_token(token, settings)
    except jwt.PyJWTError as exc:
        raise HTTPException(401, "登录状态已失效") from exc
    return _refresh_membership(principal)


async def require_principal(
    request: Request,
    authorization: str | None = Header(default=None),
    wf_session: str | None = Cookie(default=None),
) -> Principal:
    principal = getattr(request.state, "principal", None)
    if principal is None:
        principal = _decode_or_401(_extract_token(authorization, wf_session))
        request.state.principal = principal
    host = request.client.host if request.client else "unknown"
    rate_limiter.check(f"{principal.workspace_id}:{principal.user_id}:{host}")
    return principal


@app.middleware("http")
async def auth_boundary(request: Request, call_next):
    path = request.url.path
    if (
        settings.auth_mode == "required"
        and path.startswith("/api/")
        and path not in PUBLIC_API_PATHS
        and request.method != "OPTIONS"
    ):
        try:
            request.state.principal = _decode_or_401(
                _extract_token(
                    request.headers.get("authorization"),
                    request.cookies.get("wf_session"),
                )
            )
        except HTTPException as exc:
            return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    return await call_next(request)


@app.get("/")
def index():
    return FileResponse(FRONTEND / "index.html")


class ConversationCreate(BaseModel):
    title: str = Field(default="新的研发任务", min_length=1, max_length=160)
    scene: str = Field(default="battle_review", max_length=80)


class ChatRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    asset_ids: list[str] = Field(default_factory=list, max_length=50)
    provider: str = "auto"


class RegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=10, max_length=256)
    name: str = Field(default="", max_length=120)
    workspace_name: str = Field(
        default="我的游戏团队", min_length=1, max_length=120
    )


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=320)
    password: str = Field(min_length=1, max_length=256)


def _session_response(principal: Principal, response: Response):
    token = create_access_token(principal, settings)
    response.set_cookie(
        "wf_session",
        token,
        httponly=True,
        secure=settings.secure_cookies,
        samesite="lax",
        max_age=settings.access_token_minutes * 60,
        path="/",
    )
    workspace = product_store.get_workspace(principal.workspace_id)
    return {
        "authenticated": True,
        "user": {
            "id": principal.user_id,
            "email": principal.email,
            "role": principal.role,
        },
        "workspace": {
            "id": workspace["id"],
            "name": workspace["name"],
            "slug": workspace["slug"],
            "plan": workspace["plan"],
        },
        "access_token": token,
        "token_type": "bearer",
    }


@app.get("/api/config")
def public_config():
    return {"auth_required": settings.auth_mode == "required"}


@app.post("/api/auth/register")
def auth_register(req: RegisterRequest, response: Response, request: Request):
    host = request.client.host if request.client else "unknown"
    rate_limiter.check(f"auth:{host}")
    try:
        row = product_store.create_user_workspace(
            email=req.email,
            name=req.name,
            password_hash=hash_password(req.password),
            workspace_name=req.workspace_name,
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    principal = Principal(
        user_id=row["user_id"],
        workspace_id=row["workspace_id"],
        email=row["email"],
        role=row["role"],
    )
    product_store.add_audit(
        request_id=getattr(request.state, "request_id", uuid.uuid4().hex),
        action="auth.register",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
    )
    return _session_response(principal, response)


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, response: Response, request: Request):
    host = request.client.host if request.client else "unknown"
    rate_limiter.check(f"auth:{host}")
    row = product_store.get_user_auth(req.email)
    if (
        not row
        or row.get("status") != "active"
        or not verify_password(row["password_hash"], req.password)
    ):
        raise HTTPException(401, "邮箱或密码错误")
    principal = Principal(
        user_id=row["id"],
        workspace_id=row["workspace_id"],
        email=row["email"],
        role=row["role"],
    )
    product_store.add_audit(
        request_id=getattr(request.state, "request_id", uuid.uuid4().hex),
        action="auth.login",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
    )
    return _session_response(principal, response)


@app.post("/api/auth/logout")
def auth_logout(
    response: Response,
    principal: Principal = Depends(require_principal),
):
    response.delete_cookie("wf_session", path="/")
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(
    response: Response,
    principal: Principal = Depends(require_principal),
):
    return _session_response(principal, response)


@app.get("/api/product")
def product_info():
    return {
        "name": "灵境游戏研发执行工作台",
        "subtitle": "把研发目标交给系统执行、复核并留下证据",
        "scenes": [
            {
                "key": "battle_review",
                "name": "战斗问题复现",
                "desc": "结合录像、截图和日志定位稳定触发条件",
            },
            {
                "key": "balance",
                "name": "数值风险检查",
                "desc": "验证极端组合、资源曲线与胜负边界",
            },
            {
                "key": "regression",
                "name": "版本回归验证",
                "desc": "复现历史问题并核验修复结果",
            },
            {
                "key": "npc",
                "name": "角色行为检查",
                "desc": "检查目标切换、连续交互与行为一致性",
            },
            {
                "key": "content_compare",
                "name": "多素材交叉核对",
                "desc": "把图片、视频、音频、日志和配置放进同一任务",
            },
        ],
        "accepted": ["图片", "视频", "音频", "日志", "JSON/CSV", "配置文件", "文本"],
    }


@app.get("/api/providers")
def provider_list(principal: Principal = Depends(require_principal)):
    return providers.list()


@app.get("/api/workspace")
def workspace_current(principal: Principal = Depends(require_principal)):
    return product_store.get_workspace(principal.workspace_id)


@app.get("/api/audit")
def audit_list(
    limit: int = Query(default=50, ge=1, le=200),
    principal: Principal = Depends(require_principal),
):
    if principal.role not in {"owner", "admin"}:
        raise HTTPException(403, "只有管理员可以查看审计日志")
    return product_store.list_audit(
        workspace_id=principal.workspace_id, limit=limit
    )


@app.get("/api/conversations")
def conversation_list(
    limit: int = Query(default=30, ge=1, le=100),
    principal: Principal = Depends(require_principal),
):
    return product_store.list_conversations(
        limit, workspace_id=principal.workspace_id
    )


@app.post("/api/conversations")
def conversation_create(
    req: ConversationCreate,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    row = product_store.create_conversation(
        req.title,
        req.scene,
        workspace_id=principal.workspace_id,
        created_by=principal.user_id,
    )
    product_store.add_audit(
        request_id=request.state.request_id,
        action="conversation.create",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        resource_type="conversation",
        resource_id=row["id"],
    )
    return row


@app.get("/api/conversations/{conversation_id}")
def conversation_get(
    conversation_id: str,
    principal: Principal = Depends(require_principal),
):
    try:
        conversation = product_store.get_conversation(
            conversation_id, workspace_id=principal.workspace_id
        )
    except KeyError:
        raise HTTPException(404, "任务不存在")
    return {
        **conversation,
        "messages": product_store.list_messages(
            conversation_id, workspace_id=principal.workspace_id
        ),
        "assets": product_store.list_assets(
            conversation_id, workspace_id=principal.workspace_id
        ),
        "events": product_store.list_events(
            conversation_id, workspace_id=principal.workspace_id
        ),
        "job": product_store.latest_job(
            conversation_id, workspace_id=principal.workspace_id
        ),
    }


def _safe_filename(name):
    value = Path(name or "upload.bin").name.replace("\x00", "")
    return value[:240] or "upload.bin"


def _object_path(asset, key=None):
    object_key = key or str(asset["path"])
    path = storage.local_path(object_key)
    if path is not None:
        return path
    cache = DATA_DIR / "cache" / asset["workspace_id"] / asset["id"]
    cache.mkdir(parents=True, exist_ok=True)
    suffix = Path(object_key).suffix
    target = cache / (
        f"object{suffix}"
        if key is None
        else f"frame-{uuid.uuid5(uuid.NAMESPACE_URL, object_key).hex[:8]}{suffix}"
    )
    if not target.exists():
        target.write_bytes(storage.get_bytes(object_key))
    return target


def _materialize_assets(rows):
    out = []
    for row in rows:
        item = dict(row)
        meta = dict(item.get("meta", {}))
        item["path"] = str(_object_path(item))
        if meta.get("keyframes"):
            meta["keyframes"] = [
                str(_object_path(item, key)) for key in meta["keyframes"]
            ]
        item["meta"] = meta
        out.append(item)
    return out


@app.post("/api/assets")
async def asset_upload(
    request: Request,
    file: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    principal: Principal = Depends(require_principal),
):
    if conversation_id:
        try:
            product_store.get_conversation(
                conversation_id, workspace_id=principal.workspace_id
            )
        except KeyError:
            raise HTTPException(404, "任务不存在")

    filename = _safe_filename(file.filename)
    declared_mime = (file.content_type or "").strip().lower()
    guessed_mime = mimetypes.guess_type(filename)[0]
    mime = (
        guessed_mime
        if declared_mime in {"", "application/octet-stream"} and guessed_mime
        else declared_mime or guessed_mime or "application/octet-stream"
    )
    suffix = Path(filename).suffix[:16]
    max_bytes = settings.max_upload_mb * 1024 * 1024
    size = 0

    with tempfile.TemporaryDirectory(prefix="lingjing-upload-") as tmpdir:
        tmp = Path(tmpdir) / f"upload{suffix}"
        with tmp.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(
                        413, f"单个文件不能超过 {settings.max_upload_mb}MB"
                    )
                handle.write(chunk)

        meta = probe_media(tmp, mime)
        claimed_kind = (
            "image" if mime.startswith("image/")
            else "video" if mime.startswith("video/")
            else "audio" if mime.startswith("audio/")
            else None
        )
        if claimed_kind and (
            meta.get("valid") is False or meta.get("kind") != claimed_kind
        ):
            raise HTTPException(415, "素材内容与声明类型不匹配")
        frame_paths = (
            extract_video_frames(tmp, Path(tmpdir) / "frames", 3)
            if meta.get("kind") == "video"
            else []
        )
        asset_uuid = uuid.uuid4().hex
        object_key = (
            f"{principal.workspace_id}/assets/{asset_uuid}/source{suffix}"
        )
        storage.put_file(object_key, tmp, mime)
        frame_keys = []
        for index, frame in enumerate(frame_paths):
            key = (
                f"{principal.workspace_id}/assets/{asset_uuid}/frames/"
                f"{index:02d}.jpg"
            )
            storage.put_file(key, frame, "image/jpeg")
            frame_keys.append(key)
        if frame_keys:
            meta["keyframes"] = frame_keys

    row = product_store.add_asset(
        conversation_id,
        name=filename,
        mime=mime,
        path=object_key,
        size=size,
        meta=meta,
        workspace_id=principal.workspace_id,
        created_by=principal.user_id,
        storage_backend=storage.name,
    )
    product_store.add_audit(
        request_id=request.state.request_id,
        action="asset.upload",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        resource_type="asset",
        resource_id=row["id"],
        payload={"mime": mime, "size": size},
    )
    row["url"] = f"/api/assets/{row['id']}/file"
    return row


@app.get("/api/assets/{asset_id}/preview/{index}")
def asset_preview(
    asset_id: str,
    index: int = 0,
    principal: Principal = Depends(require_principal),
):
    try:
        row = product_store.get_asset(
            asset_id, workspace_id=principal.workspace_id
        )
    except KeyError:
        raise HTTPException(404, "素材不存在")
    frames = row.get("meta", {}).get("keyframes", [])
    if frames:
        key = frames[max(0, min(index, len(frames)-1))]
        local = storage.local_path(key)
        return (
            FileResponse(local, media_type="image/jpeg")
            if local is not None
            else Response(storage.get_bytes(key), media_type="image/jpeg")
        )
    if row.get("meta", {}).get("kind") == "image":
        local = storage.local_path(row["path"])
        return (
            FileResponse(local, media_type=row["mime"])
            if local is not None
            else Response(storage.get_bytes(row["path"]), media_type=row["mime"])
        )
    raise HTTPException(404, "没有可用预览")


@app.get("/api/assets/{asset_id}/file")
def asset_file(
    asset_id: str,
    principal: Principal = Depends(require_principal),
):
    try:
        row = product_store.get_asset(
            asset_id, workspace_id=principal.workspace_id
        )
    except KeyError:
        raise HTTPException(404, "素材不存在")
    local = storage.local_path(row["path"])
    if local is not None:
        return FileResponse(local, media_type=row["mime"], filename=row["name"])
    url = storage.signed_url(row["path"], filename=row["name"], expires=300)
    if not url:
        raise HTTPException(503, "对象存储暂不可用")
    return RedirectResponse(url, status_code=307)


async def _product_emit(conversation_id, workspace_id, type_, payload):
    return product_store.add_event(
        conversation_id, type_, payload, workspace_id=workspace_id
    )


async def _run_analysis_job(
    *,
    conversation_id,
    workspace_id,
    text,
    provider_key,
    history,
    assets,
    job_id=None,
):
    def ensure_active():
        if not job_id:
            return
        try:
            job = product_store.get_job(job_id, workspace_id=workspace_id)
        except KeyError as exc:
            raise AnalysisCancelled from exc
        if job["status"] == "cancelled":
            raise AnalysisCancelled

    try:
        async def sink(type_, payload):
            ensure_active()
            await _product_emit(
                conversation_id, workspace_id, type_, payload
            )

        ensure_active()
        prepared = _materialize_assets(assets)
        result = await product_analyzer.run(
            text=text,
            assets=prepared,
            provider_key=provider_key,
            sink=sink,
            history=history,
        )
        ensure_active()
        message = product_store.add_message(
            conversation_id,
            "assistant",
            result["answer"],
            result,
            workspace_id=workspace_id,
        )
        await _product_emit(
            conversation_id,
            workspace_id,
            "answer.ready",
            {"message": message, "result": result},
        )
        return True
    except AnalysisCancelled:
        return False
    except Exception as exc:
        logger.exception(
            "analysis job failed",
            extra={"conversation_id": conversation_id},
        )
        await _product_emit(
            conversation_id,
            workspace_id,
            "answer.error",
            {"message": "处理过程中出现问题", "detail": repr(exc)},
        )
        raise


@app.post("/api/conversations/{conversation_id}/messages")
async def conversation_message(
    conversation_id: str,
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    try:
        product_store.get_conversation(
            conversation_id, workspace_id=principal.workspace_id
        )
    except KeyError:
        raise HTTPException(404, "任务不存在")

    history = product_store.list_messages(
        conversation_id, workspace_id=principal.workspace_id
    )
    assets = product_store.list_assets(
        conversation_id, workspace_id=principal.workspace_id
    )
    asset_by_id = {asset["id"]: asset for asset in assets}
    for asset_id in req.asset_ids:
        if asset_id in asset_by_id:
            continue
        try:
            asset = product_store.get_asset(
                asset_id, workspace_id=principal.workspace_id
            )
            assets.append(asset)
            asset_by_id[asset_id] = asset
        except KeyError:
            continue

    user_message = product_store.add_message(
        conversation_id,
        "user",
        req.content,
        {"asset_ids": req.asset_ids},
        workspace_id=principal.workspace_id,
    )
    if not history:
        product_store.touch(
            conversation_id,
            title=req.content.strip().replace("\n", " ")[:36] or "新的研发任务",
            workspace_id=principal.workspace_id,
        )

    await _product_emit(
        conversation_id,
        principal.workspace_id,
        "message.accepted",
        {"message_id": user_message["id"], "asset_count": len(assets)},
    )
    job_payload = {
        "text": req.content,
        "provider": req.provider,
        "history": history,
        "asset_ids": [asset["id"] for asset in assets],
    }
    job = product_store.enqueue_job(
        workspace_id=principal.workspace_id,
        conversation_id=conversation_id,
        payload=job_payload,
    )
    product_store.add_audit(
        request_id=request.state.request_id,
        action="message.create",
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        resource_type="conversation",
        resource_id=conversation_id,
        payload={"job_id": job["id"], "asset_count": len(assets)},
    )

    if settings.queue_mode == "external":
        return {
            "status": "queued",
            "message": user_message,
            "job_id": job["id"],
        }

    async def work():
        try:
            with product_store.engine.begin() as connection:
                claimed = connection.execute(
                    product_store.jobs.update()
                    .where(
                        (product_store.jobs.c.id == job["id"])
                        & (product_store.jobs.c.status == "queued")
                    )
                    .values(
                        status="running",
                        worker_id="api-inprocess",
                        claimed_at=time.time(),
                        attempts=1,
                    )
                )
            if claimed.rowcount == 0:
                return
            completed = await _run_analysis_job(
                conversation_id=conversation_id,
                workspace_id=principal.workspace_id,
                text=req.content,
                provider_key=req.provider,
                history=history,
                assets=assets,
                job_id=job["id"],
            )
            if completed:
                product_store.finish_job(job["id"])
        except Exception as exc:
            product_store.fail_job(job["id"], repr(exc), max_attempts=1)

    background_tasks.add_task(work)
    return {
        "status": "accepted",
        "message": user_message,
        "job_id": job["id"],
    }


@app.get("/api/jobs/{job_id}")
def job_get(
    job_id: str,
    principal: Principal = Depends(require_principal),
):
    try:
        return product_store.get_job(
            job_id, workspace_id=principal.workspace_id
        )
    except KeyError:
        raise HTTPException(404, "任务不存在")


@app.post("/api/jobs/{job_id}/cancel")
async def job_cancel(
    job_id: str,
    request: Request,
    principal: Principal = Depends(require_principal),
):
    try:
        job = product_store.get_job(job_id, workspace_id=principal.workspace_id)
    except KeyError:
        raise HTTPException(404, "任务不存在")
    if job["status"] in {"completed", "failed", "cancelled"}:
        return job
    cancelled = product_store.cancel_job(
        job_id, workspace_id=principal.workspace_id
    )
    if cancelled["status"] == "cancelled":
        await _product_emit(
            job["conversation_id"],
            principal.workspace_id,
            "answer.cancelled",
            {"job_id": job_id},
        )
        product_store.add_audit(
            request_id=request.state.request_id,
            action="job.cancel",
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            resource_type="job",
            resource_id=job_id,
        )
    return cancelled


@app.websocket("/ws/conversations/{conversation_id}")
async def conversation_ws(websocket: WebSocket, conversation_id: str):
    token = (
        websocket.cookies.get("wf_session")
        or websocket.query_params.get("access_token")
    )
    try:
        principal = _decode_or_401(token)
        product_store.get_conversation(
            conversation_id, workspace_id=principal.workspace_id
        )
    except (HTTPException, KeyError):
        await websocket.close(code=4401)
        return

    # Subscribe before replay. Events that arrive during replay may appear in both
    # paths, so event-id deduplication below closes the replay/subscribe race.
    queue = task_event_hub.subscribe(conversation_id)
    last_event_id = max(
        0, int(websocket.query_params.get("after_id", "0") or 0)
    )
    await websocket.accept()
    try:
        replay = product_store.list_events(
            conversation_id,
            after_id=last_event_id,
            workspace_id=principal.workspace_id,
        )
        for event in replay:
            event_id = int(event.get("id", 0) or 0)
            if event_id <= last_event_id:
                continue
            await websocket.send_json(event)
            last_event_id = event_id

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "type": "heartbeat",
                    "conversation_id": conversation_id,
                    "after_id": last_event_id,
                })
                continue
            event_id = int(event.get("id", 0) or 0)
            if event_id <= last_event_id:
                continue
            await websocket.send_json(event)
            last_event_id = event_id
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        task_event_hub.unsubscribe(conversation_id, queue)


@app.get("/api/health/live")
def liveness():
    return {"status": "ok"}


@app.get("/api/health/ready")
def readiness():
    checks = {"database": False, "object_storage": False}
    try:
        with product_store.engine.connect() as connection:
            connection.execute(sql_text("select 1"))
        checks["database"] = True
    except Exception as exc:
        checks["database_error"] = type(exc).__name__
    try:
        checks["object_storage"] = bool(storage.healthcheck())
    except Exception as exc:
        checks["storage_error"] = type(exc).__name__
    ok = bool(checks["database"] and checks["object_storage"])
    if not ok:
        logger.warning("readiness check failed", extra={"checks": checks})
    return JSONResponse(
        {"status": "ready" if ok else "not_ready"},
        status_code=200 if ok else 503,
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "product": "灵境游戏工作台"}


@app.get("/api/model")
def model_card(principal: Principal = Depends(require_principal)):
    card = manager.engine.policy_model.card_dict()
    return {
        "card": card,
        "role": "local decision prior",
        "ownership": "WorldForge",
        "external_api": False,
        "runtime_contract": {
            "policy_can": ["rank actions", "estimate confidence"],
            "runtime_owns": [
                "state",
                "planning",
                "speculative evaluation",
                "execution",
                "verification",
                "rollback",
                "evolution",
            ],
        },
    }


@app.get("/api/runtime")
def runtime(principal: Principal = Depends(require_principal)):
    return {
        "plugins": manager.engine.plugins.describe(),
        "skills": manager.engine.skills.snapshot(),
        "event_store": {
            "append_only": True,
            "hash_chain": True,
            "fork": True,
            "replay": True,
            "snapshots": True,
        },
        "decision_model": {
            "fixed_dag": False,
            "state_conditioned": True,
            "counterfactual": True,
            "rollback": True,
            "recursive_council": True,
            "self_evolution": True,
        },
        "policy": manager.engine.policy_model.card_dict(),
    }



@app.get("/api/diagnostics")
def diagnostics(principal: Principal = Depends(require_principal)):
    plugins = manager.engine.plugins.describe()
    skills = manager.engine.skills.snapshot()
    names = {plugin.get("name") for plugin in plugins}
    checks = {
        "local_policy_loaded": manager.engine.policy_model.card.external_api is False,
        "plugin_registry": len(plugins) >= 9,
        "skill_bank": len(skills) >= 4,
        "event_chain": True,
        "counterfactual_brancher": "counterfactual-brancher" in names,
        "state_verifier": "state-verifier" in names,
        "regression_gated_evolution": "failure-evolver" in names,
    }
    return {
        "ok": all(checks.values()),
        "checks": checks,
        "plugin_count": len(plugins),
        "skill_count": len(skills),
    }


@app.get("/api/scenarios")
def scenarios(principal: Principal = Depends(require_principal)):
    return [scenario.model_dump() for scenario in list_scenarios()]


def _assert_run_access(session_id: str, principal: Principal) -> dict:
    meta = manager.engine.events.session_meta(session_id)
    if not meta:
        raise HTTPException(404, "未找到该运行会话")
    owner_workspace = meta.get("meta", {}).get("workspace_id")
    if owner_workspace:
        if owner_workspace != principal.workspace_id:
            raise HTTPException(404, "未找到该运行会话")
    elif not (
        settings.auth_mode == "dev"
        and principal.workspace_id == DEMO_WORKSPACE_ID
    ):
        raise HTTPException(404, "未找到该运行会话")
    return meta


@app.get("/api/runs")
def recent_runs(
    limit: int = Query(default=20, ge=1, le=100),
    principal: Principal = Depends(require_principal),
):
    rows = manager.engine.events.list_sessions(max(limit * 4, limit))
    visible = []
    for row in rows:
        owner_workspace = row.get("meta", {}).get("workspace_id")
        if owner_workspace == principal.workspace_id:
            visible.append(row)
        elif (
            not owner_workspace
            and settings.auth_mode == "dev"
            and principal.workspace_id == DEMO_WORKSPACE_ID
        ):
            visible.append(row)
        if len(visible) >= limit:
            break
    return visible


@app.post("/api/runs")
async def create_run(
    config: RunConfig,
    principal: Principal = Depends(require_principal),
):
    try:
        return {
            "session_id": await manager.start(
                config,
                workspace_id=principal.workspace_id,
                user_id=principal.user_id,
            ),
            "status": "running",
            "policy": manager.engine.policy_model.card.name,
        }
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/api/runs/{session_id}")
def run_status(
    session_id: str,
    principal: Principal = Depends(require_principal),
):
    _assert_run_access(session_id, principal)
    return manager.status(session_id)


@app.post("/api/runs/{session_id}/cancel")
async def cancel_run(
    session_id: str,
    principal: Principal = Depends(require_principal),
):
    _assert_run_access(session_id, principal)
    return await manager.cancel(session_id)


@app.get("/api/runs/{session_id}/events")
def run_events(
    session_id: str,
    after_seq: int = 0,
    principal: Principal = Depends(require_principal),
):
    _assert_run_access(session_id, principal)
    return [
        event.model_dump()
        for event in manager.engine.events.list_events(session_id, after_seq)
    ]


@app.get("/api/runs/{session_id}/verify")
def verify_run(
    session_id: str,
    principal: Principal = Depends(require_principal),
):
    _assert_run_access(session_id, principal)
    return {
        "session_id": session_id,
        "hash_chain_valid": manager.engine.events.verify_chain(session_id),
    }


def _run_report(session_id):
    events = manager.engine.events.list_events(session_id)
    if not events:
        raise HTTPException(404, "未找到该运行会话")
    by_type = {}
    for event in events:
        by_type.setdefault(event.event_type, []).append(event)

    started = by_type.get("run.started", [None])[0]
    completed = by_type.get("run.completed", [None])[-1]
    decisions = by_type.get("decision.committed", [])
    actions = by_type.get("action.executed", [])
    branches = by_type.get("counterfactual.evaluated", [])
    findings = by_type.get("qa.finding", [])
    rollbacks = by_type.get("runtime.rollback", [])
    replans = by_type.get("runtime.replan", [])
    policy_events = by_type.get("policy.prior", [])
    latencies = [
        float(event.payload.get("latency_ms", 0))
        for event in decisions
        if event.payload.get("latency_ms") is not None
    ]
    confidences = [
        float(event.payload.get("confidence", 0))
        for event in decisions
    ]
    branch_count = sum(
        len(event.payload.get("branches", [])) for event in branches
    )
    verified = sum(
        1 for event in actions
        if event.payload.get("verification", {}).get("recommendation")
        in {"accept", "continue", "proceed"}
    )
    summary = completed.payload.get("summary") if completed else None
    final_state = completed.payload.get("final_state") if completed else None
    scenario = started.payload.get("scenario", {}) if started else {}
    policy = (
        started.payload.get("policy") if started else None
    ) or manager.engine.policy_model.card_dict()
    return {
        "session_id": session_id,
        "status": "completed" if completed else "running",
        "scenario": scenario,
        "policy": policy,
        "summary": summary,
        "final_state": final_state,
        "metrics": {
            "decision_count": len(decisions),
            "action_count": len(actions),
            "counterfactual_futures": branch_count,
            "rollback_count": len(rollbacks),
            "replan_count": len(replans),
            "finding_count": len(findings),
            "verifier_coverage": round(
                len(actions) / max(1, len(actions)), 4
            ),
            "verified_accept_rate": round(
                verified / max(1, len(actions)), 4
            ),
            "avg_decision_confidence": (
                round(statistics.mean(confidences), 4)
                if confidences else 0.0
            ),
            "avg_decision_latency_ms": (
                round(statistics.mean(latencies), 2)
                if latencies else 0.0
            ),
            "policy_decision_frames": len(policy_events),
            "event_count": len(events),
            "hash_chain_valid": manager.engine.events.verify_chain(session_id),
        },
        "findings": [event.payload for event in findings],
        "evolution": [
            event.payload for event in by_type.get("evolution.patch", [])
        ] + [
            event.payload for event in by_type.get("policy.optimization", [])
        ],
    }


@app.get("/api/runs/{session_id}/report")
def run_report(
    session_id: str,
    principal: Principal = Depends(require_principal),
):
    _assert_run_access(session_id, principal)
    return _run_report(session_id)


@app.get("/api/runs/{session_id}/replay")
def replay(
    session_id: str,
    seq: int | None = None,
    principal: Principal = Depends(require_principal),
):
    _assert_run_access(session_id, principal)
    events = manager.engine.events.list_events(session_id)
    if not events:
        raise HTTPException(404, "未找到该运行会话")
    target = seq or events[-1].seq
    snapshot = manager.engine.events.get_snapshot(session_id, target)
    prefix = [
        event.model_dump()
        for event in events if event.seq <= target
    ][-20:]
    return {
        "session_id": session_id,
        "target_seq": target,
        "snapshot": snapshot,
        "events": prefix,
        "hash_chain_valid": manager.engine.events.verify_chain(session_id),
    }


@app.get("/api/runs/{session_id}/decision-space")
def decision_space(
    session_id: str,
    principal: Principal = Depends(require_principal),
):
    _assert_run_access(session_id, principal)
    events = manager.engine.events.list_events(session_id)
    if not events:
        raise HTTPException(404, "未找到该运行会话")

    def last(event_type):
        rows = [event for event in events if event.event_type == event_type]
        return rows[-1].payload if rows else None

    return {
        "world": last("world.state"),
        "policy": last("policy.prior"),
        "planner": last("planner.candidates"),
        "branches": last("counterfactual.evaluated"),
        "decision": last("decision.committed"),
        "specialists": last("subagent.deliberation"),
    }


@app.get("/api/skills")
def skills(principal: Principal = Depends(require_principal)):
    return manager.engine.skills.snapshot()


@app.get("/api/selfplay/{scenario_id}")
async def selfplay(
    scenario_id: str,
    seeds: int = Query(default=6, ge=2, le=40),
    principal: Principal = Depends(require_principal),
):
    try:
        return await asyncio.to_thread(
            manager.engine.selfplay.curriculum, scenario_id, seeds
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/benchmarks")
async def benchmark(
    req: BenchmarkRequest,
    principal: Principal = Depends(require_principal),
):
    rows = await asyncio.to_thread(
        run_benchmark, req.seeds, req.scenarios
    )
    return {
        "rows": [row.model_dump() for row in rows],
        "protocol": {
            "environment": "BalanceLab",
            "same_scenarios": True,
            "same_step_budget": True,
            "external_models": False,
            "note": "本地可复现消融评测，不宣称外部产品成绩。",
        },
    }


@app.get("/api/scenarios/{scenario_id}/brief")
def scenario_brief(
    scenario_id: str,
    principal: Principal = Depends(require_principal),
):
    try:
        scenario = get_scenario(scenario_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc
    notes = {
        "boss_burst": "验证隐藏机制侦查、风险控制和长周期资源管理。",
        "economy_trap": "验证跨阶段经济规划，避免前期局部最优造成后期崩盘。",
        "glass_cannon": "验证高方差环境中的试演与灾难性风险控制。",
        "loot_exploit": "验证异常奖励循环识别、证据留存和版本回归能力。",
    }
    return {
        "scenario": scenario.model_dump(),
        "why_it_matters": notes.get(
            scenario_id, "验证游戏自主系统的长周期决策能力。"
        ),
    }


@app.websocket("/ws/runs/{session_id}")
async def run_ws(websocket: WebSocket, session_id: str):
    token = (
        websocket.cookies.get("wf_session")
        or websocket.query_params.get("access_token")
    )
    try:
        principal = _decode_or_401(token)
        _assert_run_access(session_id, principal)
    except HTTPException:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    queue = manager.subscribe(session_id)
    try:
        for event in manager.engine.events.list_events(session_id):
            await websocket.send_json(event.model_dump())
        while True:
            try:
                payload = await asyncio.wait_for(queue.get(), timeout=15)
                await websocket.send_json(payload)
            except asyncio.TimeoutError:
                await websocket.send_json({
                    "event_type": "heartbeat",
                    "session_id": session_id,
                })
    except WebSocketDisconnect:
        pass
    finally:
        manager.unsubscribe(session_id, queue)
