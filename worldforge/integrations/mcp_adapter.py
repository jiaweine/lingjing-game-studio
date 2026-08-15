from dataclasses import dataclass
from typing import Any,Callable
@dataclass
class ToolSpec:name:str;description:str;input_schema:dict[str,Any]
class MCPGameAdapter:
 def __init__(self):self._tools={}
 def register(self,spec,handler):self._tools[spec.name]=(spec,handler)
 def list_tools(self):return [s.__dict__ for s,_ in self._tools.values()]
 def call(self,name,arguments):
  if name not in self._tools:raise KeyError(name)
  return self._tools[name][1](**arguments)
