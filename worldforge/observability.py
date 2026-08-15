from __future__ import annotations
import json,logging,time,uuid
from collections import defaultdict,deque
from threading import Lock
from fastapi import HTTPException,Request
from starlette.middleware.base import BaseHTTPMiddleware
logger=logging.getLogger('worldforge.http')
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
 async def dispatch(self,request,call_next):
  response=await call_next(request);response.headers.setdefault('X-Content-Type-Options','nosniff');response.headers.setdefault('X-Frame-Options','DENY');response.headers.setdefault('Referrer-Policy','strict-origin-when-cross-origin');response.headers.setdefault('Permissions-Policy','camera=(), microphone=(), geolocation=()');response.headers.setdefault('Cross-Origin-Resource-Policy','same-origin');response.headers.setdefault('Content-Security-Policy',"default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self' ws: wss:; media-src 'self' blob:;");return response
class RequestContextMiddleware(BaseHTTPMiddleware):
 def __init__(self,app,*,log_requests=True):super().__init__(app);self.log_requests=log_requests
 async def dispatch(self,request,call_next):
  request_id=request.headers.get('x-request-id') or uuid.uuid4().hex;request.state.request_id=request_id;started=time.perf_counter();response=await call_next(request);response.headers['X-Request-ID']=request_id
  if self.log_requests:logger.info(json.dumps({'request_id':request_id,'method':request.method,'path':request.url.path,'status':response.status_code,'duration_ms':round((time.perf_counter()-started)*1000,2)},ensure_ascii=False))
  return response
class SlidingWindowRateLimiter:
 def __init__(self,limit_per_minute):self.limit=limit_per_minute;self._hits=defaultdict(deque);self._lock=Lock()
 def check(self,key):
  now=time.time();cutoff=now-60
  with self._lock:
   q=self._hits[key]
   while q and q[0]<cutoff:q.popleft()
   if len(q)>=self.limit:raise HTTPException(429,'请求过于频繁，请稍后再试',headers={'Retry-After':str(max(1,int(60-(now-q[0]))))})
   q.append(now)
