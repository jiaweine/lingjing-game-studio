from __future__ import annotations
import os,secrets
from dataclasses import dataclass
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def _bool(name,default=False):
 v=os.getenv(name);return default if v is None else v.strip().lower() in {'1','true','yes','on'}
def _list(name,default=''):return [x.strip() for x in os.getenv(name,default).split(',') if x.strip()]
@dataclass(frozen=True)
class Settings:
 env:str;data_dir:Path;database_url:str;auth_mode:str;jwt_secret:str;jwt_issuer:str;jwt_audience:str;access_token_minutes:int;secure_cookies:bool;cors_origins:list[str];trusted_hosts:list[str];rate_limit_per_minute:int;max_upload_mb:int;queue_mode:str;storage_backend:str;s3_bucket:str|None;s3_region:str|None;s3_endpoint_url:str|None;s3_access_key:str|None;s3_secret_key:str|None;request_log:bool;auto_create_schema:bool
 @property
 def production(self):return self.env=='production'
def load_settings():
 env=os.getenv('WORLDFORGE_ENV','development').strip().lower();data_dir=Path(os.getenv('WORLDFORGE_DATA',ROOT/'outputs'/'runtime'));database_url=os.getenv('DATABASE_URL',f"sqlite:///{(data_dir/'product.db').as_posix()}");auth_mode=os.getenv('WORLDFORGE_AUTH_MODE','required' if env=='production' else 'dev').strip().lower()
 if auth_mode not in {'dev','required'}:raise RuntimeError("WORLDFORGE_AUTH_MODE must be 'dev' or 'required'")
 secret=os.getenv('WORLDFORGE_JWT_SECRET','').strip()
 if not secret:
  if env=='production':raise RuntimeError('WORLDFORGE_JWT_SECRET is required in production')
  secret=secrets.token_urlsafe(48)
 return Settings(env,data_dir,database_url,auth_mode,secret,os.getenv('WORLDFORGE_JWT_ISSUER','lingjing-game-studio'),os.getenv('WORLDFORGE_JWT_AUDIENCE','lingjing-web'),int(os.getenv('WORLDFORGE_ACCESS_TOKEN_MINUTES','720')),_bool('WORLDFORGE_SECURE_COOKIES',env=='production'),_list('WORLDFORGE_CORS_ORIGINS','http://localhost:8765,http://127.0.0.1:8765'),_list('WORLDFORGE_TRUSTED_HOSTS','localhost,127.0.0.1,testserver' if env!='production' else ''),max(10,int(os.getenv('WORLDFORGE_RATE_LIMIT_PER_MINUTE','120'))),max(1,int(os.getenv('WORLDFORGE_MAX_UPLOAD_MB','120'))),os.getenv('WORLDFORGE_QUEUE_MODE','inprocess').strip().lower(),os.getenv('WORLDFORGE_STORAGE_BACKEND','local').strip().lower(),os.getenv('S3_BUCKET') or None,os.getenv('S3_REGION') or None,os.getenv('S3_ENDPOINT_URL') or None,os.getenv('S3_ACCESS_KEY') or None,os.getenv('S3_SECRET_KEY') or None,_bool('WORLDFORGE_REQUEST_LOG',True),_bool('WORLDFORGE_AUTO_CREATE_SCHEMA',env!='production'))
settings=load_settings()
