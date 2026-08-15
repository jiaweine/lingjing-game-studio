from __future__ import annotations
import base64,mimetypes
from pathlib import Path
from typing import Any
import httpx
from .base import BaseProvider,ProviderError,ProviderInfo
def _data_url(path,mime=None):
 p=Path(path);mime=mime or mimetypes.guess_type(p.name)[0] or 'application/octet-stream';return f'data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}'
class OpenAICompatProvider(BaseProvider):
 def __init__(self,*,key,name,vendor,api_key,base_url,model,multimodal,note='',extra_headers=None):
  self.api_key=api_key;self.base_url=base_url.rstrip('/');self.model=model;self.extra_headers=extra_headers or {};self.info=ProviderInfo(key,name,vendor,model,bool(api_key and model),multimodal,note=note)
 async def chat(self,*,messages,assets=None,temperature=.2,max_tokens=1400):
  if not self.info.configured:raise ProviderError(f'{self.info.name} 未配置')
  out=[dict(m) for m in messages];image_assets=[a for a in assets or [] if str(a.get('mime','')).startswith('image/')]
  if image_assets and self.info.multimodal and out and out[-1].get('role')=='user':
   content=[{'type':'text','text':str(out[-1].get('content',''))}]
   for a in image_assets[:6]:content.append({'type':'image_url','image_url':{'url':_data_url(a['path'],a.get('mime'))}})
   out[-1]={'role':'user','content':content}
  headers={'Authorization':f'Bearer {self.api_key}','Content-Type':'application/json',**self.extra_headers};payload={'model':self.model,'messages':out,'temperature':temperature,'max_tokens':max_tokens}
  async with httpx.AsyncClient(timeout=90) as client:r=await client.post(f'{self.base_url}/chat/completions',headers=headers,json=payload)
  if r.status_code>=400:raise ProviderError(f'{self.info.name} 请求失败 {r.status_code}: {r.text[:300]}')
  try:return r.json()['choices'][0]['message']['content'] or ''
  except Exception as exc:raise ProviderError(f'{self.info.name} 返回格式异常') from exc
