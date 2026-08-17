from __future__ import annotations
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any
from sqlalchemy import BigInteger, Column, Float, Integer, MetaData, String, Table, Text, create_engine, insert, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
DEMO_WORKSPACE_ID='workspace-demo';DEMO_USER_ID='user-demo'
def _id(prefix:str)->str:return f'{prefix}-{uuid.uuid4().hex[:16]}'
def _slug(value:str)->str:
 value=re.sub('[^a-zA-Z0-9\\u4e00-\\u9fff]+','-',value.strip()).strip('-').lower();return value[:36] or f'workspace-{uuid.uuid4().hex[:6]}'
class ConversationStore:
 def __init__(self,db_path:str|Path|None=None,asset_dir:str|Path='assets',*,database_url:str|None=None,auto_create_schema:bool=True,seed_dev_identity:bool=True):
  self.asset_dir=Path(asset_dir);self.asset_dir.mkdir(parents=True,exist_ok=True)
  if database_url:url=database_url
  else:
   path=Path(db_path or 'product.db');path.parent.mkdir(parents=True,exist_ok=True);url=f'sqlite:///{path.as_posix()}'
  kwargs={'pool_pre_ping':True}
  if url.startswith('sqlite'):kwargs['connect_args']={'check_same_thread':False}
  self.engine:Engine=create_engine(url,**kwargs);self.metadata=MetaData();self._define_tables()
  if auto_create_schema:self.metadata.create_all(self.engine)
  if seed_dev_identity:self.ensure_dev_identity()
 def _define_tables(self):
  self.users=Table('users',self.metadata,Column('id',String(64),primary_key=True),Column('email',String(320),nullable=False,unique=True),Column('name',String(120),nullable=False),Column('password_hash',Text,nullable=False),Column('status',String(32),nullable=False,default='active'),Column('created_at',Float,nullable=False))
  self.workspaces=Table('workspaces',self.metadata,Column('id',String(64),primary_key=True),Column('name',String(160),nullable=False),Column('slug',String(80),nullable=False,unique=True),Column('plan',String(32),nullable=False,default='team'),Column('created_at',Float,nullable=False))
  self.memberships=Table('memberships',self.metadata,Column('workspace_id',String(64),primary_key=True),Column('user_id',String(64),primary_key=True),Column('role',String(32),nullable=False),Column('created_at',Float,nullable=False))
  self.conversations=Table('conversations',self.metadata,Column('id',String(64),primary_key=True),Column('workspace_id',String(64),nullable=False,index=True),Column('created_by',String(64),nullable=False),Column('title',String(240),nullable=False),Column('scene',String(80),nullable=False),Column('created_at',Float,nullable=False),Column('updated_at',Float,nullable=False))
  self.messages=Table('messages',self.metadata,Column('id',String(64),primary_key=True),Column('conversation_id',String(64),nullable=False,index=True),Column('role',String(24),nullable=False),Column('content',Text,nullable=False),Column('payload',Text,nullable=False,default='{}'),Column('created_at',Float,nullable=False))
  self.assets=Table('assets',self.metadata,Column('id',String(64),primary_key=True),Column('workspace_id',String(64),nullable=False,index=True),Column('created_by',String(64),nullable=False),Column('conversation_id',String(64),nullable=True,index=True),Column('name',String(512),nullable=False),Column('mime',String(160),nullable=False),Column('path',Text,nullable=False),Column('storage_backend',String(32),nullable=False,default='local'),Column('size',BigInteger,nullable=False),Column('meta',Text,nullable=False,default='{}'),Column('created_at',Float,nullable=False))
  self.task_events=Table('task_events',self.metadata,Column('id',Integer,primary_key=True,autoincrement=True),Column('workspace_id',String(64),nullable=False,index=True),Column('conversation_id',String(64),nullable=False,index=True),Column('type',String(96),nullable=False),Column('payload',Text,nullable=False),Column('created_at',Float,nullable=False))
  self.audit_logs=Table('audit_logs',self.metadata,Column('id',Integer,primary_key=True,autoincrement=True),Column('workspace_id',String(64),nullable=True,index=True),Column('user_id',String(64),nullable=True),Column('request_id',String(64),nullable=False,index=True),Column('action',String(120),nullable=False),Column('resource_type',String(80),nullable=True),Column('resource_id',String(96),nullable=True),Column('payload',Text,nullable=False,default='{}'),Column('created_at',Float,nullable=False))
  self.jobs=Table('analysis_jobs',self.metadata,Column('id',String(64),primary_key=True),Column('workspace_id',String(64),nullable=False,index=True),Column('conversation_id',String(64),nullable=False,index=True),Column('status',String(32),nullable=False,index=True),Column('payload',Text,nullable=False),Column('attempts',Integer,nullable=False,default=0),Column('worker_id',String(96),nullable=True),Column('last_error',Text,nullable=True),Column('created_at',Float,nullable=False),Column('available_at',Float,nullable=False),Column('claimed_at',Float,nullable=True),Column('completed_at',Float,nullable=True))
 @staticmethod
 def _dict(row):return dict(row._mapping if hasattr(row,'_mapping') else row)
 @staticmethod
 def _json_row(row,field='payload'):
  d=ConversationStore._dict(row);d[field]=json.loads(d.get(field) or '{}');return d
 def ensure_dev_identity(self):
  now=time.time()
  with self.engine.begin() as c:
   if c.execute(select(self.workspaces.c.id).where(self.workspaces.c.id==DEMO_WORKSPACE_ID)).first() is None:c.execute(insert(self.workspaces).values(id=DEMO_WORKSPACE_ID,name='本地演示空间',slug='local-demo',plan='dev',created_at=now))
   if c.execute(select(self.users.c.id).where(self.users.c.id==DEMO_USER_ID)).first() is None:c.execute(insert(self.users).values(id=DEMO_USER_ID,email='demo@local.lingjing',name='本地用户',password_hash='!dev-only',status='active',created_at=now))
   if c.execute(select(self.memberships.c.user_id).where((self.memberships.c.workspace_id==DEMO_WORKSPACE_ID)&(self.memberships.c.user_id==DEMO_USER_ID))).first() is None:c.execute(insert(self.memberships).values(workspace_id=DEMO_WORKSPACE_ID,user_id=DEMO_USER_ID,role='owner',created_at=now))
 def create_user_workspace(self,*,email,name,password_hash,workspace_name):
  now=time.time();uid=_id('user');wid=_id('ws');email=email.strip().lower();slug=f'{_slug(workspace_name)}-{uuid.uuid4().hex[:5]}'
  try:
   with self.engine.begin() as c:
    c.execute(insert(self.users).values(id=uid,email=email,name=name.strip() or email.split('@')[0],password_hash=password_hash,status='active',created_at=now));c.execute(insert(self.workspaces).values(id=wid,name=workspace_name.strip() or '我的工作区',slug=slug,plan='team',created_at=now));c.execute(insert(self.memberships).values(workspace_id=wid,user_id=uid,role='owner',created_at=now))
  except IntegrityError as exc:raise ValueError('该邮箱已注册') from exc
  return {'user_id':uid,'workspace_id':wid,'email':email,'name':name,'role':'owner'}
 def get_user_auth(self,email):
  email=email.strip().lower()
  with self.engine.connect() as c:
   row=c.execute(select(self.users).where(self.users.c.email==email)).first()
   if not row:return None
   user=self._dict(row);membership=c.execute(select(self.memberships).where(self.memberships.c.user_id==user['id']).order_by(self.memberships.c.created_at)).first()
   if not membership:return None
   m=self._dict(membership);user.update({'workspace_id':m['workspace_id'],'role':m['role']});return user
 def get_workspace(self,workspace_id):
  with self.engine.connect() as c:row=c.execute(select(self.workspaces).where(self.workspaces.c.id==workspace_id)).first()
  if not row:raise KeyError(workspace_id)
  return self._dict(row)
 def create_conversation(self,title='新的分析任务',scene='battle_review',*,workspace_id=DEMO_WORKSPACE_ID,created_by=DEMO_USER_ID):
  cid=_id('cv');now=time.time()
  with self.engine.begin() as c:c.execute(insert(self.conversations).values(id=cid,workspace_id=workspace_id,created_by=created_by,title=title,scene=scene,created_at=now,updated_at=now))
  return self.get_conversation(cid,workspace_id=workspace_id)
 def list_conversations(self,limit=50,*,workspace_id=DEMO_WORKSPACE_ID):
  with self.engine.connect() as c:rows=c.execute(select(self.conversations).where(self.conversations.c.workspace_id==workspace_id).order_by(self.conversations.c.updated_at.desc()).limit(limit)).fetchall()
  return [self._dict(r) for r in rows]
 def get_conversation(self,cid,*,workspace_id=DEMO_WORKSPACE_ID):
  with self.engine.connect() as c:row=c.execute(select(self.conversations).where((self.conversations.c.id==cid)&(self.conversations.c.workspace_id==workspace_id))).first()
  if not row:raise KeyError(cid)
  return self._dict(row)
 def touch(self,cid,*,title=None,workspace_id=DEMO_WORKSPACE_ID):
  values={'updated_at':time.time()};values.update({'title':title} if title else {})
  with self.engine.begin() as c:
   result=c.execute(update(self.conversations).where((self.conversations.c.id==cid)&(self.conversations.c.workspace_id==workspace_id)).values(**values))
   if result.rowcount==0:raise KeyError(cid)
 def add_message(self,cid,role,content,payload=None,*,workspace_id=DEMO_WORKSPACE_ID):
  self.get_conversation(cid,workspace_id=workspace_id);mid=_id('msg');now=time.time();payload=payload or {}
  with self.engine.begin() as c:c.execute(insert(self.messages).values(id=mid,conversation_id=cid,role=role,content=content,payload=json.dumps(payload,ensure_ascii=False),created_at=now));c.execute(update(self.conversations).where(self.conversations.c.id==cid).values(updated_at=now))
  return {'id':mid,'conversation_id':cid,'role':role,'content':content,'payload':payload,'created_at':now}
 def list_messages(self,cid,*,workspace_id=DEMO_WORKSPACE_ID):
  self.get_conversation(cid,workspace_id=workspace_id)
  with self.engine.connect() as c:rows=c.execute(select(self.messages).where(self.messages.c.conversation_id==cid).order_by(self.messages.c.created_at,self.messages.c.id)).fetchall()
  return [self._json_row(r) for r in rows]
 def add_asset(self,cid,*,name,mime,path,size,meta,workspace_id=DEMO_WORKSPACE_ID,created_by=DEMO_USER_ID,storage_backend='local'):
  if cid:self.get_conversation(cid,workspace_id=workspace_id)
  aid=_id('asset');now=time.time()
  with self.engine.begin() as c:
   c.execute(insert(self.assets).values(id=aid,workspace_id=workspace_id,created_by=created_by,conversation_id=cid,name=name,mime=mime,path=path,storage_backend=storage_backend,size=size,meta=json.dumps(meta,ensure_ascii=False),created_at=now))
   if cid:c.execute(update(self.conversations).where(self.conversations.c.id==cid).values(updated_at=now))
  return self.get_asset(aid,workspace_id=workspace_id)
 def get_asset(self,aid,*,workspace_id=DEMO_WORKSPACE_ID):
  with self.engine.connect() as c:row=c.execute(select(self.assets).where((self.assets.c.id==aid)&(self.assets.c.workspace_id==workspace_id))).first()
  if not row:raise KeyError(aid)
  return self._json_row(row,'meta')
 def list_assets(self,cid,*,workspace_id=DEMO_WORKSPACE_ID):
  self.get_conversation(cid,workspace_id=workspace_id)
  with self.engine.connect() as c:rows=c.execute(select(self.assets).where((self.assets.c.conversation_id==cid)&(self.assets.c.workspace_id==workspace_id)).order_by(self.assets.c.created_at)).fetchall()
  return [self._json_row(r,'meta') for r in rows]
 def add_event(self,cid,type_,payload,*,workspace_id=DEMO_WORKSPACE_ID):
  self.get_conversation(cid,workspace_id=workspace_id);now=time.time()
  with self.engine.begin() as c:result=c.execute(insert(self.task_events).values(workspace_id=workspace_id,conversation_id=cid,type=type_,payload=json.dumps(payload,ensure_ascii=False),created_at=now));eid=result.inserted_primary_key[0]
  return {'id':eid,'workspace_id':workspace_id,'conversation_id':cid,'type':type_,'payload':payload,'created_at':now}
 def list_events(self,cid,after_id=0,*,workspace_id=DEMO_WORKSPACE_ID):
  self.get_conversation(cid,workspace_id=workspace_id)
  with self.engine.connect() as c:rows=c.execute(select(self.task_events).where((self.task_events.c.conversation_id==cid)&(self.task_events.c.workspace_id==workspace_id)&(self.task_events.c.id>after_id)).order_by(self.task_events.c.id)).fetchall()
  return [self._json_row(r) for r in rows]
 def add_audit(self,*,request_id,action,workspace_id=None,user_id=None,resource_type=None,resource_id=None,payload=None):
  with self.engine.begin() as c:c.execute(insert(self.audit_logs).values(workspace_id=workspace_id,user_id=user_id,request_id=request_id,action=action,resource_type=resource_type,resource_id=resource_id,payload=json.dumps(payload or {},ensure_ascii=False),created_at=time.time()))
 def list_audit(self,*,workspace_id,limit=100):
  with self.engine.connect() as c:rows=c.execute(select(self.audit_logs).where(self.audit_logs.c.workspace_id==workspace_id).order_by(self.audit_logs.c.id.desc()).limit(limit)).fetchall()
  return [self._json_row(r) for r in rows]
 def enqueue_job(self,*,workspace_id,conversation_id,payload):
  self.get_conversation(conversation_id,workspace_id=workspace_id);jid=_id('job');now=time.time()
  with self.engine.begin() as c:c.execute(insert(self.jobs).values(id=jid,workspace_id=workspace_id,conversation_id=conversation_id,status='queued',payload=json.dumps(payload,ensure_ascii=False),attempts=0,created_at=now,available_at=now))
  return self.get_job(jid,workspace_id=workspace_id)
 def get_job(self,job_id,*,workspace_id):
  with self.engine.connect() as c:row=c.execute(select(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.workspace_id==workspace_id))).first()
  if not row:raise KeyError(job_id)
  return self._json_row(row)
 def latest_job(self,conversation_id,*,workspace_id):
  with self.engine.connect() as c:row=c.execute(select(self.jobs).where((self.jobs.c.conversation_id==conversation_id)&(self.jobs.c.workspace_id==workspace_id)).order_by(self.jobs.c.created_at.desc()).limit(1)).first()
  return self._json_row(row) if row else None
 def cancel_job(self,job_id,*,workspace_id):
  with self.engine.begin() as c:c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.workspace_id==workspace_id)&(self.jobs.c.status.in_(('queued','running')))).values(status='cancelled',completed_at=time.time()))
  return self.get_job(job_id,workspace_id=workspace_id)
 def claim_job(self,worker_id):
  now=time.time()
  with self.engine.begin() as c:
   row=c.execute(select(self.jobs.c.id).where((self.jobs.c.status=='queued')&(self.jobs.c.available_at<=now)).order_by(self.jobs.c.created_at).limit(1)).first()
   if not row:return None
   job_id=row[0];result=c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.status=='queued')).values(status='running',worker_id=worker_id,claimed_at=now,attempts=self.jobs.c.attempts+1))
   if result.rowcount==0:return None
   claimed=c.execute(select(self.jobs).where(self.jobs.c.id==job_id)).first()
  return self._json_row(claimed)
 def finish_job(self,job_id):
  with self.engine.begin() as c:c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.status=='running')).values(status='completed',completed_at=time.time(),last_error=None))
 def fail_job(self,job_id,error,*,retry_delay=15.,max_attempts=3):
  with self.engine.begin() as c:
   row=c.execute(select(self.jobs.c.attempts).where(self.jobs.c.id==job_id)).first();attempts=int(row[0]) if row else max_attempts;values={'last_error':error[:4000]}
   if attempts<max_attempts:values.update(status='queued',worker_id=None,claimed_at=None,available_at=time.time()+retry_delay*attempts)
   else:values.update(status='failed',completed_at=time.time())
   c.execute(update(self.jobs).where((self.jobs.c.id==job_id)&(self.jobs.c.status=='running')).values(**values))
