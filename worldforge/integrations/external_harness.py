import httpx
from worldforge.models import ActionKind
class ExternalHarnessClient:
 def __init__(self,endpoint,timeout=60):self.endpoint=endpoint.rstrip('/');self.timeout=timeout
 def act(self,*,session_id,state,legal,goal,step,token_budget=4096,tool_budget=8):
  payload={'session_id':session_id,'observation':state.model_dump(),'legal_actions':legal,'goal':goal.model_dump(),'budget':{'step':step,'token_budget':token_budget,'tool_budget':tool_budget}}
  with httpx.Client(timeout=self.timeout) as c:r=c.post(f'{self.endpoint}/act',json=payload);r.raise_for_status();data=r.json()
  action=ActionKind(data['action'])
  if action.value not in legal:raise ValueError(f'illegal action {action.value}')
  return action
