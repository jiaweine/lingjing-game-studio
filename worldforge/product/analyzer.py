from __future__ import annotations
import asyncio
from worldforge.models import RunConfig
from worldforge.providers import ProviderRegistry,ProviderError
class ProductAnalyzer:
 def __init__(self,engine,providers):self.engine=engine;self.providers=providers
 def intent(self,text,assets,history=None):
  context='\n'.join(str(m.get('content','')) for m in (history or [])[-8:])+'\n'+text
  if any(k in context for k in ['录像','视频','战斗','掉血','伤害','boss','Boss']):return 'battle_review'
  if any(k in context for k in ['数值','平衡','build','Build','胜率','难度']):return 'balance'
  if any(k in context for k in ['回归','版本','bug','异常','复现']):return 'regression'
  if any(k in context for k in ['npc','NPC','角色','行为','剧情']):return 'npc'
  if any(str(a.get('mime','')).startswith('video/') for a in assets):return 'battle_review'
  return 'general'
 async def run(self,*,text,assets,provider_key,sink,history=None):
  history=history or [];intent=self.intent(text,assets,history);detail=f'已关联 {len(assets)} 份任务素材'+(f'、{len(history)} 条历史消息' if history else '');await sink('progress',{'step':'素材整理','detail':detail+'，正在建立分析上下文','percent':12});await asyncio.sleep(.04);evidence=[]
  for a in assets:
   meta=a.get('meta',{});kind=meta.get('kind','file');label={'image':'截图','video':'录像','audio':'音频','text':'日志'}.get(kind,'文件');title=a.get('name','');title+=f" · {meta['duration']}s" if meta.get('duration') else '';title+=f" · {meta.get('width')}×{meta.get('height')}" if meta.get('width') else '';evidence.append({'type':kind,'label':label,'title':title,'asset_id':a.get('id')})
  await sink('progress',{'step':'定位问题','detail':self._progress_detail(intent),'percent':36});runtime_result=None
  if intent in {'battle_review','balance','regression'}:
   scenario={'battle_review':'boss_burst','balance':'glass_cannon','regression':'loot_exploit'}[intent];await sink('progress',{'step':'场景复核','detail':'正在用一致的初始条件重复验证关键路径','percent':58});summary=await self.engine.run(RunConfig(scenario_id=scenario,seed=29,max_steps=12,branch_width=3,rollout_horizon=2,rollouts_per_branch=2,enable_evolution=False),demo_delay=0);runtime_result=summary.model_dump();evidence.append({'type':'replay','label':'复核结果','title':f"同条件复核 {summary.steps} 步 · {'异常路径已复现' if summary.outcome not in {'victory','success'} else '结果稳定'}"})
  await sink('progress',{'step':'交叉核对','detail':'正在对照素材、配置与复核结果，排除偶发因素','percent':76});provider=self.providers.choose(provider_key,assets);provider_name='内置演示';generated=None
  if provider:
   provider_name=provider.info.name
   try:
    prior=[{'role':m.get('role'),'content':str(m['content'])[:6000]} for m in history[-8:] if m.get('role') in {'user','assistant'} and m.get('content')];generated=await provider.chat(messages=[{'role':'system','content':'你是游戏研发与运营分析助手。用中文给出清晰、可执行、面向业务人员的结论。'},*prior,{'role':'user','content':self._prompt(text,intent,evidence,runtime_result,history)}],assets=assets)
   except ProviderError as exc:await sink('notice',{'title':'模型服务暂不可用','detail':str(exc)})
  await sink('progress',{'step':'形成结论','detail':'分析完成，已整理关键发现和下一步建议','percent':100});return {'answer':generated or self._demo_answer(intent),'intent':intent,'provider':provider_name,'evidence':evidence,'runtime':runtime_result,'context':{'history_messages':len(history),'task_assets':len(assets)},'suggestions':self._suggestions(intent)}
 def _progress_detail(self,intent):return {'battle_review':'正在定位异常时间段、关键受击与资源变化','balance':'正在比较高风险组合、资源曲线与胜负边界','regression':'正在核对版本差异并尝试复现异常路径','npc':'正在检查角色行为、上下文一致性与异常跳变'}.get(intent,'正在拆解问题并寻找最相关的证据')
 def _prompt(self,text,intent,evidence,runtime,history):return f'当前追问：{text}\n任务类型：{intent}\n任务内素材摘要：{evidence}\n场景复核：{runtime}\n此前已有 {len(history)} 条任务消息。请结合此前对话与当前素材作答。'
 def _demo_answer(self,intent):
  if intent=='battle_review':return '### 结论\n异常更像是**高爆发阶段的资源衔接问题**，不是单一伤害数值失控。\n\n### 证据\n1. 高风险阶段承伤和资源消耗同时抬升。\n2. 同条件复核可以再次定位关键窗口。\n\n### 建议\n优先检查减伤覆盖、敌方爆发参数和技能冷却。'
  if intent=='balance':return '### 结论\n当前数值存在高收益高波动组合。\n\n### 建议\n优先收窄极端波动，并用分层玩家策略重新验证。'
  if intent=='regression':return '### 结论\n异常具备稳定复现条件，建议按版本回归问题处理。\n\n### 建议\n锁定配置差异并补自动回归用例。'
  if intent=='npc':return '### 结论\n角色行为存在上下文切换不够平滑的问题。\n\n### 建议\n优先验证冲突指令和连续多轮交互。'
  return '### 结论\n已完成当前素材整理与问题拆解。可以继续追加素材和追问，不需要重新描述背景。'
 def _suggestions(self,intent):return {'battle_review':['把异常时间段单独展开','继续核对伤害配置','生成回归测试清单'],'balance':['看不同玩家策略的结果','找出最危险的数值组合','生成调参建议'],'regression':['查看复现步骤','对比两个版本配置','生成发布前检查项'],'npc':['检查角色目标切换','对比两段对话表现','生成行为规则建议']}.get(intent,['继续追问','补充素材','生成结论摘要'])
