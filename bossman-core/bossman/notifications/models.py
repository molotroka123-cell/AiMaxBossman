from __future__ import annotations
import hashlib,json,time,uuid
from dataclasses import dataclass,field
from enum import Enum
from typing import Any

class Severity(str,Enum): INFO="info";WARNING="warning";ERROR="error";CRITICAL="critical"
class QueueStatus(str,Enum): PENDING="pending";SENDING="sending";SENT="sent";DEAD="dead"
class ActionKind(str,Enum): APPROVE="approve";DENY="deny";STOP="stop"

@dataclass(slots=True,frozen=True)
class NotificationAction:
    kind:ActionKind
    target_type:str
    target_id:str
    label:str
    fingerprint:str
    expires_in_s:int=900

@dataclass(slots=True)
class Notification:
    id:str
    event_type:str
    severity:Severity
    title:str
    body:str
    dedupe_key:str
    context:dict[str,Any]=field(default_factory=dict)
    actions:list[NotificationAction]=field(default_factory=list)
    created_at:float=field(default_factory=time.time)

    @classmethod
    def create(cls,event_type:str,severity:Severity,title:str,body:str,*,
               dedupe_key:str|None=None,context:dict|None=None,actions=None):
        body=(body or "")[:3500];title=(title or "")[:300]
        if dedupe_key is None:
            raw=json.dumps({"e":event_type,"t":title,"b":body,"c":context or {}},
                           sort_keys=True,default=str).encode()
            dedupe_key=hashlib.sha256(raw).hexdigest()
        return cls("nt_"+uuid.uuid4().hex,event_type,severity,title,body,
                   dedupe_key[:300],context or {},list(actions or []))
