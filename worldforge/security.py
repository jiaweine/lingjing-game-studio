from __future__ import annotations
import time
from dataclasses import dataclass
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError,VerifyMismatchError
from worldforge.settings import Settings
_hasher=PasswordHasher(time_cost=2,memory_cost=65536,parallelism=2)
@dataclass(frozen=True)
class Principal:user_id:str;workspace_id:str;email:str;role:str='member'
def hash_password(password):
 if len(password)<10:raise ValueError('密码至少需要 10 个字符')
 return _hasher.hash(password)
def verify_password(encoded,password):
 try:return _hasher.verify(encoded,password)
 except (VerifyMismatchError,InvalidHashError):return False
def create_access_token(principal,settings):
 now=int(time.time());payload={'sub':principal.user_id,'workspace_id':principal.workspace_id,'email':principal.email,'role':principal.role,'iss':settings.jwt_issuer,'aud':settings.jwt_audience,'iat':now,'nbf':now-5,'exp':now+settings.access_token_minutes*60};return jwt.encode(payload,settings.jwt_secret,algorithm='HS256')
def decode_access_token(token,settings):
 p=jwt.decode(token,settings.jwt_secret,algorithms=['HS256'],audience=settings.jwt_audience,issuer=settings.jwt_issuer,options={'require':['exp','iat','sub','workspace_id']});return Principal(str(p['sub']),str(p['workspace_id']),str(p.get('email','')),str(p.get('role','member')))
