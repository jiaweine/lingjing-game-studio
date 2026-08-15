from __future__ import annotations
import os
from .anthropic import AnthropicProvider
from .base import BaseProvider,ProviderInfo
from .gemini import GeminiProvider
from .openai_compat import OpenAICompatProvider
class ProviderRegistry:
 def __init__(self):self.providers={};self._load()
 def _load(self):
  e=os.environ;self.providers={'openai':OpenAICompatProvider(key='openai',name='OpenAI',vendor='OpenAI',api_key=e.get('OPENAI_API_KEY'),base_url=e.get('OPENAI_BASE_URL','https://api.openai.com/v1'),model=e.get('OPENAI_MODEL'),multimodal=True,note='通用分析、图片理解与工具任务'),'deepseek':OpenAICompatProvider(key='deepseek',name='DeepSeek',vendor='DeepSeek',api_key=e.get('DEEPSEEK_API_KEY'),base_url=e.get('DEEPSEEK_BASE_URL','https://api.deepseek.com'),model=e.get('DEEPSEEK_MODEL','deepseek-v4-pro'),multimodal=False,note='适合文本推理与长上下文分析'),'qwen':OpenAICompatProvider(key='qwen',name='通义千问',vendor='阿里云百炼',api_key=e.get('DASHSCOPE_API_KEY'),base_url=e.get('QWEN_BASE_URL','https://dashscope.aliyuncs.com/compatible-mode/v1'),model=e.get('QWEN_MODEL','qwen3-vl-plus'),multimodal=True,note='适合中文、多模态与文档理解'),'doubao':OpenAICompatProvider(key='doubao',name='豆包',vendor='火山方舟',api_key=e.get('ARK_API_KEY'),base_url=e.get('DOUBAO_BASE_URL','https://ark.cn-beijing.volces.com/api/v3'),model=e.get('DOUBAO_MODEL'),multimodal=True,note='适合中文、多模态和游戏内容场景'),'anthropic':AnthropicProvider(e.get('ANTHROPIC_API_KEY'),e.get('ANTHROPIC_MODEL')),'gemini':GeminiProvider(e.get('GEMINI_API_KEY'),e.get('GEMINI_MODEL'))}
  if e.get('CUSTOM_BASE_URL'):self.providers['custom']=OpenAICompatProvider(key='custom',name='自定义模型',vendor='OpenAI-Compatible',api_key=e.get('CUSTOM_API_KEY'),base_url=e['CUSTOM_BASE_URL'],model=e.get('CUSTOM_MODEL'),multimodal=e.get('CUSTOM_MULTIMODAL','1')!='0',note='企业自建或私有化模型服务')
 def list(self):
  rows=[p.info.dict() for p in self.providers.values()];rows.insert(0,ProviderInfo('auto','自动选择','系统',None,True,True,True,True,'根据素材类型和可用服务自动选择').dict());rows.append(ProviderInfo('demo','内置演示','本地','Demo Engine',True,True,True,True,'无需密钥，用于完整体验与验收').dict());return rows
 def choose(self,preferred,assets):
  if preferred and preferred not in {'auto','demo'}:
   p=self.providers.get(preferred);return p if p and p.info.configured else None
  needs_mm=any(str(a.get('mime','')).startswith(('image/','video/','audio/')) for a in assets);order=['qwen','doubao','gemini','openai','anthropic','deepseek'] if needs_mm else ['deepseek','qwen','doubao','openai','anthropic','gemini']
  for key in order:
   p=self.providers.get(key)
   if p and p.info.configured and (not needs_mm or p.info.multimodal):return p
  return None
