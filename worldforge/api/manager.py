from __future__ import annotations
import asyncio
from collections import defaultdict
from pathlib import Path
from typing import Any
from worldforge.models import RunConfig,RunSummary,RuntimeEvent
from worldforge.runtime import WorldForgeEngine
class RunManager:
    def __init__(self,data_dir:str|Path)->None:
        data_dir=Path(data_dir);data_dir.mkdir(parents=True,exist_ok=True);self.engine=WorldForgeEngine(data_dir/'worldforge.db');self.tasks={};self.summaries={};self.queues=defaultdict(list)
    async def start(self,config:RunConfig)->str:
        import uuid
        session_id=f"wf-{uuid.uuid4().hex[:10]}"
        async def sink(event:RuntimeEvent)->None:
            for q in list(self.queues.get(session_id,[])):
                try:q.put_nowait(event.model_dump())
                except asyncio.QueueFull:pass
        async def execute()->None:
            try:self.summaries[session_id]=await self.engine.run(config,session_id=session_id,sink=sink)
            except Exception as exc:
                ev=self.engine.events.append(session_id,"run.failed",{"error":repr(exc)});await sink(ev);raise
        self.tasks[session_id]=asyncio.create_task(execute(),name=session_id);return session_id
    def status(self,session_id):
        task=self.tasks.get(session_id);summary=self.summaries.get(session_id);events=self.engine.events.list_events(session_id)
        if summary:status="completed"
        elif task and task.cancelled():status="cancelled"
        elif task and task.done():
            try:status="failed" if task.exception() else "completed"
            except asyncio.CancelledError:status="cancelled"
        elif task:status="running"
        elif events:status="completed" if any(e.event_type=="run.completed" for e in events) else "stored"
        else:status="unknown"
        return {"session_id":session_id,"status":status,"summary":summary.model_dump() if summary else None,"event_count":len(events),"last_event":events[-1].model_dump() if events else None}
    async def cancel(self,session_id):
        task=self.tasks.get(session_id)
        if not task:return {"session_id":session_id,"status":"unknown"}
        if task.done():return self.status(session_id)
        task.cancel();ev=self.engine.events.append(session_id,"run.cancelled",{"reason":"operator_stop"})
        for q in list(self.queues.get(session_id,[])):
            try:q.put_nowait(ev.model_dump())
            except asyncio.QueueFull:pass
        return {"session_id":session_id,"status":"cancelled"}
    def subscribe(self,session_id):q=asyncio.Queue(maxsize=500);self.queues[session_id].append(q);return q
    def unsubscribe(self,session_id,q):
        if q in self.queues.get(session_id,[]):self.queues[session_id].remove(q)
