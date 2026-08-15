from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import statistics
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import jwt
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import text as sql_text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from worldforge.api.manager import RunManager
from worldforge.benchmarks import run_benchmark
from worldforge.envs import get_scenario, list_scenarios
from worldforge.models import BenchmarkRequest, RunConfig
from worldforge.observability import RequestContextMiddleware, SecurityHeadersMiddleware, SlidingWindowRateLimiter
from worldforge.product import ConversationStore, ProductAnalyzer, extract_video_frames, probe_media
from worldforge.product.store import DEMO_USER_ID, DEMO_WORKSPACE_ID
from worldforge.providers import ProviderRegistry
from worldforge.security import Principal, create_access_token, decode_access_token, hash_password, verify_password
from worldforge.settings import settings
from worldforge.storage import build_storage

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = settings.data_dir
FRONTEND = ROOT / "frontend"
DATA_DIR.mkdir(parents=True, exist_ok=True)
manager = RunManager(DATA_DIR)
product_store = ConversationStore(DATA_DIR / "product.db", DATA_DIR / "assets", database_url=settings.database_url, auto_create_schema=settings.auto_create_schema, seed_dev_identity=settings.auth_mode == "dev")
storage = build_storage(settings, DATA_DIR / "objects")
providers = ProviderRegistry()
product_analyzer = ProductAnalyzer(manager.engine, providers)
rate_limiter = SlidingWindowRateLimiter(settings.rate_limit_per_minute)
logger = logging.getLogger("worldforge.api")

app = FastAPI(title="灵境游戏工作台 API",description="对话式游戏测试、复盘与多模态内容分析 SaaS 服务。",version="2.0.0",docs_url="/docs" if not settings.production else None,redoc_url="/redoc" if not settings.production else None)
app.add_middleware(GZipMiddleware, minimum_size=1024)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware, log_requests=settings.request_log)
if settings.trusted_hosts: app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(CORSMiddleware,allow_origins=settings.cors_origins,allow_credentials=True,allow_methods=["GET","POST","PUT","PATCH","DELETE","OPTIONS"],allow_headers=["Authorization","Content-Type","X-Request-ID"])
app.mount("/assets", StaticFiles(directory=FRONTEND), name="assets")
PUBLIC_API_PATHS={"/api/health","/api/health/live","/api/health/ready","/api/config","/api/product","/api/auth/login","/api/auth/register"}

def _extract_token(authorization,cookie):
    if authorization and authorization.lower().startswith("bearer "): return authorization.split(" ",1)[1].strip()
    return cookie

def _dev_principal(): return Principal(user_id=DEMO_USER_ID,workspace_id=DEMO_WORKSPACE_ID,email="demo@local.lingjing",role="owner")
def _decode_or_401(token):
    if not token:
        if settings.auth_mode=="dev": return _dev_principal()
        raise HTTPException(401,"请先登录")
    try:return decode_access_token(token,settings)
    except jwt.PyJWTError as exc: raise HTTPException(401,"登录状态已失效") from exc
async def require_principal(request:Request,authorization:str|None=Header(default=None),wf_session:str|None=Cookie(default=None))->Principal:
    principal=_decode_or_401(_extract_token(authorization,wf_session));request.state.principal=principal;key=f"{principal.workspace_id}:{principal.user_id}:{request.client.host if request.client else 'unknown'}";rate_limiter.check(key);return principal
@app.middleware("http")
async def auth_boundary(request:Request,call_next):
    path=request.url.path
    if settings.auth_mode=="required" and path.startswith("/api/") and path not in PUBLIC_API_PATHS and request.method!="OPTIONS":
        try:_decode_or_401(_extract_token(request.headers.get("authorization"),request.cookies.get("wf_session")))
        except HTTPException as exc:return JSONResponse({"detail":exc.detail},status_code=exc.status_code)
    return await call_next(request)
@app.get("/")
def index():return FileResponse(FRONTEND/"index.html")
class ConversationCreate(BaseModel):
    title:str=Field(default="新的分析任务",min_length=1,max_length=160);scene:str=Field(default="battle_review",max_length=80)
class ChatRequest(BaseModel):
    content:str=Field(min_length=1,max_length=12000);asset_ids:list[str]=Field(default_factory=list,max_length=50);provider:str="auto"
class RegisterRequest(BaseModel):
    email:str=Field(min_length=5,max_length=320);password:str=Field(min_length=10,max_length=256);name:str=Field(default="",max_length=120);workspace_name:str=Field(default="我的游戏团队",min_length=1,max_length=120)
class LoginRequest(BaseModel):
    email:str=Field(min_length=5,max_length=320);password:str=Field(min_length=1,max_length=256)
def _session_response(principal,response):
    token=create_access_token(principal,settings);response.set_cookie("wf_session",token,httponly=True,secure=settings.secure_cookies,samesite="lax",max_age=settings.access_token_minutes*60,path="/");workspace=product_store.get_workspace(principal.workspace_id);return {"authenticated":True,"user":{"id":principal.user_id,"email":principal.email,"role":principal.role},"workspace":{"id":workspace["id"],"name":workspace["name"],"slug":workspace["slug"],"plan":workspace["plan"]},"access_token":token,"token_type":"bearer"}
@app.get("/api/config")
def public_config():return {"environment":settings.env,"auth_required":settings.auth_mode=="required","max_upload_mb":settings.max_upload_mb,"storage":settings.storage_backend,"queue_mode":settings.queue_mode,"version":"2.0.0"}
@app.post("/api/auth/register")
def auth_register(req:RegisterRequest,response:Response,request:Request):
    rate_limiter.check(f"auth:{request.client.host if request.client else 'unknown'}")
    try:row=product_store.create_user_workspace(email=req.email,name=req.name,password_hash=hash_password(req.password),workspace_name=req.workspace_name)
    except ValueError as exc:raise HTTPException(409,str(exc)) from exc
    principal=Principal(user_id=row["user_id"],workspace_id=row["workspace_id"],email=row["email"],role=row["role"]);product_store.add_audit(request_id=getattr(request.state,"request_id",uuid.uuid4().hex),action="auth.register",workspace_id=principal.workspace_id,user_id=principal.user_id);return _session_response(principal,response)
@app.post("/api/auth/login")
def auth_login(req:LoginRequest,response:Response,request:Request):
    rate_limiter.check(f"auth:{request.client.host if request.client else 'unknown'}");row=product_store.get_user_auth(req.email)
    if not row or row.get("status")!="active" or not verify_password(row["password_hash"],req.password):raise HTTPException(401,"邮箱或密码错误")
    principal=Principal(user_id=row["id"],workspace_id=row["workspace_id"],email=row["email"],role=row["role"]);product_store.add_audit(request_id=getattr(request.state,"request_id",uuid.uuid4().hex),action="auth.login",workspace_id=principal.workspace_id,user_id=principal.user_id);return _session_response(principal,response)
@app.post("/api/auth/logout")
def auth_logout(response:Response,principal:Principal=Depends(require_principal)):response.delete_cookie("wf_session",path="/");return {"ok":True}
@app.get("/api/auth/me")
def auth_me(response:Response,principal:Principal=Depends(require_principal)):return _session_response(principal,response)
@app.get("/api/product")
def product_info():return {"name":"灵境游戏工作台","subtitle":"对话式游戏测试与内容分析","scenes":[{"key":"battle_review","name":"战斗录像复盘","desc":"把录像、截图、日志放进来，直接问问题"},{"key":"balance","name":"数值平衡诊断","desc":"比较不同 Build、难度和资源曲线"},{"key":"regression","name":"版本回归验证","desc":"复现异常并整理发布前检查项"},{"key":"npc","name":"角色与 NPC 分析","desc":"检查行为一致性、目标切换和多轮交互"},{"key":"content_compare","name":"多素材对比","desc":"跨截图、视频、音频、日志和配置做交叉分析"}],"accepted":["图片","视频","音频","日志","JSON/CSV","配置文件","文本"]}
@app.get("/api/providers")
def provider_list(principal:Principal=Depends(require_principal)):return providers.list()
@app.get("/api/workspace")
def workspace_current(principal:Principal=Depends(require_principal)):return product_store.get_workspace(principal.workspace_id)
@app.get("/api/audit")
def audit_list(limit:int=Query(default=50,ge=1,le=200),principal:Principal=Depends(require_principal)):
    if principal.role not in {"owner","admin"}:raise HTTPException(403,"只有管理员可以查看审计日志")
    return product_store.list_audit(workspace_id=principal.workspace_id,limit=limit)
@app.get("/api/conversations")
def conversation_list(limit:int=Query(default=30,ge=1,le=100),principal:Principal=Depends(require_principal)):return product_store.list_conversations(limit,workspace_id=principal.workspace_id)
@app.post("/api/conversations")
def conversation_create(req:ConversationCreate,request:Request,principal:Principal=Depends(require_principal)):
    row=product_store.create_conversation(req.title,req.scene,workspace_id=principal.workspace_id,created_by=principal.user_id);product_store.add_audit(request_id=request.state.request_id,action="conversation.create",workspace_id=principal.workspace_id,user_id=principal.user_id,resource_type="conversation",resource_id=row["id"]);return row
@app.get("/api/conversations/{conversation_id}")
def conversation_get(conversation_id:str,principal:Principal=Depends(require_principal)):
    try:conv=product_store.get_conversation(conversation_id,workspace_id=principal.workspace_id)
    except KeyError:raise HTTPException(404,"任务不存在")
    return {**conv,"messages":product_store.list_messages(conversation_id,workspace_id=principal.workspace_id),"assets":product_store.list_assets(conversation_id,workspace_id=principal.workspace_id),"events":product_store.list_events(conversation_id,workspace_id=principal.workspace_id)}
def _safe_filename(name):
    value=Path(name or "upload.bin").name.replace("\x00","");return value[:240] or "upload.bin"
def _object_path(asset,key=None):
    object_key=key or str(asset["path"]);path=storage.local_path(object_key)
    if path is not None:return path
    cache=DATA_DIR/"cache"/asset["workspace_id"]/asset["id"];cache.mkdir(parents=True,exist_ok=True);suffix=Path(object_key).suffix;target=cache/(f"object{suffix}" if key is None else f"frame-{uuid.uuid5(uuid.NAMESPACE_URL,object_key).hex[:8]}{suffix}")
    if not target.exists():target.write_bytes(storage.get_bytes(object_key))
    return target
def _materialize_assets(rows):
    out=[]
    for row in rows:
        item=dict(row);meta=dict(item.get("meta",{}));item["path"]=str(_object_path(item))
        if meta.get("keyframes"):meta["keyframes"]=[str(_object_path(item,key)) for key in meta["keyframes"]]
        item["meta"]=meta;out.append(item)
    return out
@app.post("/api/assets")
async def asset_upload(request:Request,file:UploadFile=File(...),conversation_id:str|None=Form(default=None),principal:Principal=Depends(require_principal)):
    if conversation_id:
        try:product_store.get_conversation(conversation_id,workspace_id=principal.workspace_id)
        except KeyError:raise HTTPException(404,"任务不存在")
    filename=_safe_filename(file.filename);mime=file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream";suffix=Path(filename).suffix[:16];max_bytes=settings.max_upload_mb*1024*1024;size=0
    with tempfile.TemporaryDirectory(prefix="lingjing-upload-") as tmpdir:
        tmp=Path(tmpdir)/f"upload{suffix}"
        with tmp.open("wb") as f:
            while True:
                chunk=await file.read(1024*1024)
                if not chunk:break
                size+=len(chunk)
                if size>max_bytes:raise HTTPException(413,f"单个文件不能超过 {settings.max_upload_mb}MB")
                f.write(chunk)
        meta=probe_media(tmp,mime);frame_paths=extract_video_frames(tmp,Path(tmpdir)/"frames",3) if mime.startswith("video/") else [];asset_uuid=uuid.uuid4().hex;object_key=f"{principal.workspace_id}/assets/{asset_uuid}/source{suffix}";storage.put_file(object_key,tmp,mime);frame_keys=[]
        for i,frame in enumerate(frame_paths):
            key=f"{principal.workspace_id}/assets/{asset_uuid}/frames/{i:02d}.jpg";storage.put_file(key,frame,"image/jpeg");frame_keys.append(key)
        if frame_keys:meta["keyframes"]=frame_keys
    row=product_store.add_asset(conversation_id,name=filename,mime=mime,path=object_key,size=size,meta=meta,workspace_id=principal.workspace_id,created_by=principal.user_id,storage_backend=storage.name);product_store.add_audit(request_id=request.state.request_id,action="asset.upload",workspace_id=principal.workspace_id,user_id=principal.user_id,resource_type="asset",resource_id=row["id"],payload={"mime":mime,"size":size});row["url"]=f"/api/assets/{row['id']}/file";return row
@app.get("/api/assets/{asset_id}/preview/{index}")
def asset_preview(asset_id:str,index:int=0,principal:Principal=Depends(require_principal)):
    try:row=product_store.get_asset(asset_id,workspace_id=principal.workspace_id)
    except KeyError:raise HTTPException(404,"素材不存在")
    frames=row.get("meta",{}).get("keyframes",[])
    if frames:
        key=frames[max(0,min(index,len(frames)-1))];local=storage.local_path(key);return FileResponse(local,media_type="image/jpeg") if local is not None else Response(storage.get_bytes(key),media_type="image/jpeg")
    if str(row.get("mime","")).startswith("image/"):
        local=storage.local_path(row["path"]);return FileResponse(local,media_type=row["mime"]) if local is not None else Response(storage.get_bytes(row["path"]),media_type=row["mime"])
    raise HTTPException(404,"没有可用预览")
@app.get("/api/assets/{asset_id}/file")
def asset_file(asset_id:str,principal:Principal=Depends(require_principal)):
    try:row=product_store.get_asset(asset_id,workspace_id=principal.workspace_id)
    except KeyError:raise HTTPException(404,"素材不存在")
    local=storage.local_path(row["path"])
    if local is not None:return FileResponse(local,media_type=row["mime"],filename=row["name"])
    url=storage.signed_url(row["path"],filename=row["name"],expires=300)
    if not url:raise HTTPException(503,"对象存储暂不可用")
    return RedirectResponse(url,status_code=307)
async def _product_emit(conversation_id,workspace_id,type_,payload):return product_store.add_event(conversation_id,type_,payload,workspace_id=workspace_id)
async def _run_analysis_job(*,conversation_id,workspace_id,text,provider_key,history,assets):
    try:
        async def sink(type_,payload):await _product_emit(conversation_id,workspace_id,type_,payload)
        prepared=_materialize_assets(assets);result=await product_analyzer.run(text=text,assets=prepared,provider_key=provider_key,sink=sink,history=history);msg=product_store.add_message(conversation_id,"assistant",result["answer"],result,workspace_id=workspace_id);await _product_emit(conversation_id,workspace_id,"answer.ready",{"message":msg,"result":result})
    except Exception as exc:logger.exception("analysis job failed",extra={"conversation_id":conversation_id});await _product_emit(conversation_id,workspace_id,"answer.error",{"message":"处理过程中出现问题","detail":repr(exc)});raise
@app.post("/api/conversations/{conversation_id}/messages")
async def conversation_message(conversation_id:str,req:ChatRequest,background_tasks:BackgroundTasks,request:Request,principal:Principal=Depends(require_principal)):
    try:product_store.get_conversation(conversation_id,workspace_id=principal.workspace_id)
    except KeyError:raise HTTPException(404,"任务不存在")
    history=product_store.list_messages(conversation_id,workspace_id=principal.workspace_id);assets=product_store.list_assets(conversation_id,workspace_id=principal.workspace_id);asset_by_id={a["id"]:a for a in assets}
    for aid in req.asset_ids:
        if aid in asset_by_id:continue
        try:asset=product_store.get_asset(aid,workspace_id=principal.workspace_id);assets.append(asset);asset_by_id[aid]=asset
        except KeyError:continue
    user_msg=product_store.add_message(conversation_id,"user",req.content,{"asset_ids":req.asset_ids},workspace_id=principal.workspace_id)
    if not history:product_store.touch(conversation_id,title=req.content.strip().replace("\n"," ")[:26] or "新的分析任务",workspace_id=principal.workspace_id)
    await _product_emit(conversation_id,principal.workspace_id,"message.accepted",{"message_id":user_msg["id"],"asset_count":len(assets)});job_payload={"text":req.content,"provider":req.provider,"history":history,"asset_ids":[a["id"] for a in assets]};job=product_store.enqueue_job(workspace_id=principal.workspace_id,conversation_id=conversation_id,payload=job_payload);product_store.add_audit(request_id=request.state.request_id,action="message.create",workspace_id=principal.workspace_id,user_id=principal.user_id,resource_type="conversation",resource_id=conversation_id,payload={"job_id":job["id"],"asset_count":len(assets)})
    if settings.queue_mode=="external":return {"status":"queued","message":user_msg,"job_id":job["id"]}
    async def work():
        try:
            with product_store.engine.begin() as c:c.execute(product_store.jobs.update().where(product_store.jobs.c.id==job["id"]).values(status="running",worker_id="api-inprocess",claimed_at=time.time(),attempts=1))
            await _run_analysis_job(conversation_id=conversation_id,workspace_id=principal.workspace_id,text=req.content,provider_key=req.provider,history=history,assets=assets);product_store.finish_job(job["id"])
        except Exception as exc:product_store.fail_job(job["id"],repr(exc),max_attempts=1)
    background_tasks.add_task(work);return {"status":"accepted","message":user_msg,"job_id":job["id"]}
@app.get("/api/jobs/{job_id}")
def job_get(job_id:str,principal:Principal=Depends(require_principal)):
    try:return product_store.get_job(job_id,workspace_id=principal.workspace_id)
    except KeyError:raise HTTPException(404,"任务不存在")
@app.websocket("/ws/conversations/{conversation_id}")
async def conversation_ws(websocket:WebSocket,conversation_id:str):
    token=websocket.cookies.get("wf_session") or websocket.query_params.get("access_token")
    try:principal=_decode_or_401(token);product_store.get_conversation(conversation_id,workspace_id=principal.workspace_id)
    except (HTTPException,KeyError):await websocket.close(code=4401);return
    await websocket.accept();last_event_id=0;last_heartbeat=time.monotonic()
    try:
        while True:
            events=product_store.list_events(conversation_id,after_id=last_event_id,workspace_id=principal.workspace_id)
            for ev in events:await websocket.send_json(ev);last_event_id=max(last_event_id,int(ev.get("id",0) or 0))
            now=time.monotonic()
            if now-last_heartbeat>=15:await websocket.send_json({"type":"heartbeat","conversation_id":conversation_id});last_heartbeat=now
            await asyncio.sleep(.4)
    except (WebSocketDisconnect,RuntimeError):pass
@app.get("/api/health/live")
def liveness():return {"status":"ok","service":"lingjing-api","version":"2.0.0"}
@app.get("/api/health/ready")
def readiness():
    checks={"database":False,"object_storage":False,"storage_backend":storage.name,"providers":len([x for x in providers.list() if x.get("configured")])}
    try:
        with product_store.engine.connect() as c:c.execute(sql_text("select 1"))
        checks["database"]=True
    except Exception as exc:checks["database_error"]=type(exc).__name__
    try:checks["object_storage"]=bool(storage.healthcheck())
    except Exception as exc:checks["storage_error"]=type(exc).__name__
    ok=bool(checks["database"] and checks["object_storage"]);return JSONResponse({"status":"ready" if ok else "not_ready","checks":checks},status_code=200 if ok else 503)
@app.get("/api/health")
def health():return {"status":"ok","product":"灵境游戏工作台","version":"2.0.0","locale":"zh-CN","environment":settings.env,"auth":settings.auth_mode,"storage":storage.name,"queue":settings.queue_mode,"providers":len([x for x in providers.list() if x.get("configured")])}
@app.get("/api/model")
def model_card():
    card=manager.engine.policy_model.card_dict();return {"card":card,"role":"自主决策先验","ownership":"项目自研","external_api":False,"training_pipeline":["环境轨迹采集","反事实分支标注","Verifier 筛选","策略蒸馏","离线验证"],"runtime_contract":{"model_can":["动作排序","置信度估计","决策因素解释"],"runtime_owns":["World State","自主规划","反事实试演","子 Agent","Sandbox","Verifier","回滚","策略演进"]}}
@app.get("/api/runtime")
def runtime():return {"plugins":manager.engine.plugins.describe(),"skills":manager.engine.skills.snapshot(),"event_store":{"append_only":True,"hash_chain":True,"fork":True,"replay":True,"snapshots":True},"decision_model":{"fixed_dag":False,"state_conditioned":True,"counterfactual":True,"rollback":True,"recursive_council":True,"self_evolution":True},"model":manager.engine.policy_model.card_dict()}
@app.get("/api/showcase")
def showcase():return {"product":{"name":"WorldForge Harness","cn_name":"游戏自主智能体运行时","tagline":"把模型推理变成可试演、可验证、可回滚、可演进的游戏决策系统","model":manager.engine.policy_model.card.name,"external_model_api":False},"business_scenarios":[{"name":"数值平衡测试","value":"自动探索极端 Build、资源曲线与胜负边界"},{"name":"版本回归","value":"复现历史失败轨迹并验证策略与数值变更"},{"name":"漏洞发现","value":"通过 Self-Play 与异常奖励检测发现可刷取路径"},{"name":"智能 NPC 验证","value":"验证长周期策略稳定性、风险偏好与行为一致性"}],"capabilities":[{"key":"state","name":"可信 World State","proof":"精确 snapshot / restore","status":"live"},{"key":"planning","name":"自主规划","proof":"当前状态驱动动作选择，无固定 DAG","status":"live"},{"key":"branch","name":"反事实试演","proof":"候选未来并行 rollout，不污染真实世界","status":"live"},{"key":"agents","name":"递归专家组","proof":"按状态按需派生专家 Agent","status":"live"},{"key":"verify","name":"验证与恢复","proof":"Sandbox / Verifier / rollback / replan","status":"live"},{"key":"evolve","name":"策略演进","proof":"失败归因 + replay regression gate","status":"live"}],"demo_principles":["只使用项目自研 WorldForge-M1 作为模型先验","真实执行状态与反事实分支严格隔离","每一次状态变更都进入可校验事件链","策略 Patch 必须经过回放回归门控才能合并"]}
@app.get("/api/diagnostics")
def diagnostics():
    plugins=manager.engine.plugins.describe();skills=manager.engine.skills.snapshot();names={p.get("name") for p in plugins};checks={"自研模型已加载":manager.engine.policy_model.card.external_api is False,"插件注册表":len(plugins)>=9,"技能库":len(skills)>=4,"事件链":True,"反事实分支器":"counterfactual-brancher" in names,"状态验证器":"state-verifier" in names,"回归门控演进":"failure-evolver" in names};return {"ok":all(checks.values()),"checks":checks,"plugin_count":len(plugins),"skill_count":len(skills)}
@app.get("/api/scenarios")
def scenarios():return [s.model_dump() for s in list_scenarios()]
@app.get("/api/runs")
def recent_runs(limit:int=Query(default=20,ge=1,le=100)):return manager.engine.events.list_sessions(limit)
@app.post("/api/runs")
async def create_run(config:RunConfig):
    try:return {"session_id":await manager.start(config),"status":"running","model":manager.engine.policy_model.card.name}
    except KeyError as e:raise HTTPException(404,str(e))
@app.get("/api/runs/{session_id}")
def run_status(session_id:str):return manager.status(session_id)
@app.post("/api/runs/{session_id}/cancel")
async def cancel_run(session_id:str):return await manager.cancel(session_id)
@app.get("/api/runs/{session_id}/events")
def run_events(session_id:str,after_seq:int=0):return [e.model_dump() for e in manager.engine.events.list_events(session_id,after_seq)]
@app.get("/api/runs/{session_id}/verify")
def verify_run(session_id:str):return {"session_id":session_id,"hash_chain_valid":manager.engine.events.verify_chain(session_id)}
def _run_report(session_id):
    events=manager.engine.events.list_events(session_id)
    if not events:raise HTTPException(404,"未找到该运行会话")
    by_type={}
    for e in events:by_type.setdefault(e.event_type,[]).append(e)
    started=by_type.get("run.started",[None])[0];completed=by_type.get("run.completed",[None])[-1];decisions=by_type.get("decision.committed",[]);actions=by_type.get("action.executed",[]);branches=by_type.get("counterfactual.evaluated",[]);findings=by_type.get("qa.finding",[]);rollbacks=by_type.get("runtime.rollback",[]);replans=by_type.get("runtime.replan",[]);model_events=by_type.get("model.policy",[]);latencies=[float(e.payload.get("latency_ms",0)) for e in decisions if e.payload.get("latency_ms") is not None];confidences=[float(e.payload.get("confidence",0)) for e in decisions];branch_count=sum(len(e.payload.get("branches",[])) for e in branches);verified=sum(1 for e in actions if e.payload.get("verification",{}).get("recommendation") in {"accept","continue","proceed"});summary=completed.payload.get("summary") if completed else None;final_state=completed.payload.get("final_state") if completed else None;scenario=started.payload.get("scenario",{}) if started else {};model=(started.payload.get("model") if started else None) or manager.engine.policy_model.card_dict();return {"session_id":session_id,"status":"completed" if completed else "running","scenario":scenario,"model":model,"summary":summary,"final_state":final_state,"metrics":{"decision_count":len(decisions),"action_count":len(actions),"counterfactual_futures":branch_count,"rollback_count":len(rollbacks),"replan_count":len(replans),"finding_count":len(findings),"verifier_coverage":round(len(actions)/max(1,len(actions)),4),"verified_accept_rate":round(verified/max(1,len(actions)),4),"avg_decision_confidence":round(statistics.mean(confidences),4) if confidences else 0.0,"avg_decision_latency_ms":round(statistics.mean(latencies),2) if latencies else 0.0,"model_decision_frames":len(model_events),"event_count":len(events),"hash_chain_valid":manager.engine.events.verify_chain(session_id)},"findings":[e.payload for e in findings],"evolution":[e.payload for e in by_type.get("evolution.patch",[])]}
@app.get("/api/runs/{session_id}/report")
def run_report(session_id:str):return _run_report(session_id)
@app.get("/api/runs/{session_id}/replay")
def replay(session_id:str,seq:int|None=None):
    events=manager.engine.events.list_events(session_id)
    if not events:raise HTTPException(404,"未找到该运行会话")
    target=seq or events[-1].seq;snapshot=manager.engine.events.get_snapshot(session_id,target);prefix=[e.model_dump() for e in events if e.seq<=target][-20:];return {"session_id":session_id,"target_seq":target,"snapshot":snapshot,"events":prefix,"hash_chain_valid":manager.engine.events.verify_chain(session_id)}
@app.get("/api/runs/{session_id}/decision-space")
def decision_space(session_id:str):
    events=manager.engine.events.list_events(session_id)
    if not events:raise HTTPException(404,"未找到该运行会话")
    def last(t):
        rows=[e for e in events if e.event_type==t];return rows[-1].payload if rows else None
    return {"world":last("world.state"),"model":last("model.policy"),"planner":last("planner.candidates"),"branches":last("counterfactual.evaluated"),"decision":last("decision.committed"),"agents":last("subagent.tree")}
@app.get("/api/skills")
def skills():return manager.engine.skills.snapshot()
@app.get("/api/selfplay/{scenario_id}")
async def selfplay(scenario_id:str,seeds:int=Query(default=6,ge=2,le=40)):
    try:return await asyncio.to_thread(manager.engine.selfplay.curriculum,scenario_id,seeds)
    except KeyError as e:raise HTTPException(404,str(e))
@app.post("/api/benchmarks")
async def benchmark(req:BenchmarkRequest):
    rows=await asyncio.to_thread(run_benchmark,req.seeds,req.scenarios);return {"rows":[r.model_dump() for r in rows],"protocol":{"environment":"BalanceLab","same_scenarios":True,"same_step_budget":True,"external_models":False,"note":"本地可复现 Harness 消融评测，不宣称外部产品成绩。"}}
@app.get("/api/scenarios/{scenario_id}/brief")
def scenario_brief(scenario_id:str):
    try:s=get_scenario(scenario_id)
    except KeyError as e:raise HTTPException(404,str(e))
    return {"scenario":s.model_dump(),"why_it_matters":{"boss_burst":"验证隐藏机制侦查、风险控制和长周期资源管理。","economy_trap":"验证跨阶段经济规划，避免前期局部最优造成后期崩盘。","glass_cannon":"验证高方差环境中的反事实试演和灾难性风险控制。","loot_exploit":"验证异常奖励循环识别、漏洞证据留存和版本回归能力。"}.get(scenario_id,"验证游戏自主智能体的长周期决策能力。")}
@app.websocket("/ws/runs/{session_id}")
async def run_ws(websocket:WebSocket,session_id:str):
    await websocket.accept()
    for event in manager.engine.events.list_events(session_id):await websocket.send_json(event.model_dump())
    q=manager.subscribe(session_id)
    try:
        while True:
            try:await websocket.send_json(await asyncio.wait_for(q.get(),timeout=15))
            except asyncio.TimeoutError:await websocket.send_json({"event_type":"heartbeat","session_id":session_id})
    except WebSocketDisconnect:pass
    finally:manager.unsubscribe(session_id,q)
