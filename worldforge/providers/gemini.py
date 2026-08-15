from __future__ import annotations
import base64
from pathlib import Path
import httpx
from .base import BaseProvider,ProviderError,ProviderInfo
class GeminiProvider(BaseProvider):
 def __init__(self,api_key,model):self.api_key,self.model=api_key,model;self.info=ProviderInfo('gemini','Gemini','Google',model,bool(api_key and model),True,True,True,'支持图片、视频、音频和长文档理解')
 async def chat(self,*,messages,assets=None,temperature=.2,max_tokens=1400):
  if not self.info.configured:raise ProviderError('Gemini 未配置')
  text='\n'.join(f"{m.get('role','user')}: {m.get('content','')}" for m in messages);parts=[{'text':text}]
  for a in (assets or [])[:6]:
   mime=str(a.get('mime',''))
   if mime.startswith(('image/','audio/','video/')) and Path(a['path']).stat().st_size<=18*1024*1024:parts.append({'inline_data':{'mime_type':mime,'data':base64.b64encode(Path(a['path']).read_bytes()).decode()}})
  url=f'https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}';payload={'contents':[{'role':'user','parts':parts}],'generationConfig':{'temperature':temperature,'maxOutputTokens':max_tokens}}
  async with httpx.AsyncClient(timeout=120) as client:r=await client.post(url,json=payload)
  if r.status_code>=400:raise ProviderError(f'Gemini 请求失败 {r.status_code}: {r.text[:300]}')
  try:return '\n'.join(x.get('text','') for x in r.json()['candidates'][0]['content']['parts'] if 'text' in x)
  except Exception as exc:raise ProviderError('Gemini 返回格式异常') from exc
