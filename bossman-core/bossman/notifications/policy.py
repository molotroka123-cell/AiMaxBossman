from __future__ import annotations
import re
from .models import ActionKind,Notification,NotificationAction,Severity

_SECRET_PATTERNS=[
    re.compile(r"(?i)\b(?:sk|sk-proj|sk-or-v1)-[A-Za-z0-9_-]{8,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{12,}\b"),
    re.compile(r"(?i)\b(?:api[_ -]?key|token|secret|password)\s*[:=]\s*\S+"),
]
def sanitize(text)->str:
    s=str(text or "")
    try:
        from ..obs import redact
        s=redact(s)
    except Exception:pass
    for p in _SECRET_PATTERNS:s=p.sub("[REDACTED]",s)
    return s[:3500]

def _id(data):
    for k in ("id","task_id","run_id","approval_id","job_id"): 
        if data.get(k) is not None:return str(data[k])
    return "unknown"

class NotificationPolicy:
    """Maps existing EventBus events to phone-worthy notifications only."""
    def from_event(self,event:dict)->Notification|None:
        kind=str(event.get("kind",""))
        if kind in {"task.completed","task.done","run.completed"}:
            i=_id(event);return Notification.create(kind,Severity.INFO,"BOSSMAN: задача завершена",
                sanitize(event.get("summary") or f"Задача {i} завершена"),
                dedupe_key=f"{kind}:{i}")
        if kind in {"task.failed","run.failed","job.failed"}:
            i=_id(event);return Notification.create(kind,Severity.ERROR,"BOSSMAN: ошибка задачи",
                sanitize(event.get("error") or event.get("reason") or f"Задача {i} упала"),
                dedupe_key=f"{kind}:{i}:{sanitize(event.get('error',''))[:80]}")
        if kind in {"approval.created","approval.required"}:
            aid=str(event.get("id") or event.get("approval_id") or "")
            if not aid:return None
            preview=sanitize(event.get("preview") or "Требуется подтверждение")
            fp=f"approval:{aid}:{event.get('kind','')}:{event.get('tool','')}"
            actions=[
                NotificationAction(ActionKind.APPROVE,"approval",aid,"✅ Approve",fp),
                NotificationAction(ActionKind.DENY,"approval",aid,"❌ Deny",fp),
            ]
            return Notification.create(kind,Severity.WARNING,"BOSSMAN: требуется подтверждение",
                preview,dedupe_key=f"approval:{aid}",context={"approval_id":aid},actions=actions)
        if kind=="budget.warning":
            scope=sanitize(event.get("scope"));subject=sanitize(event.get("subject"))
            return Notification.create(kind,Severity.WARNING,"BOSSMAN: бюджет 80%+",
                f"{scope}/{subject}: {sanitize(event.get('projected_usd'))} / {sanitize(event.get('limit_usd'))} USD",
                dedupe_key=f"budget-warning:{scope}:{subject}:{sanitize(event.get('limit_usd'))}")
        if kind in {"budget.exceeded","budget.approval_required"}:
            return Notification.create(kind,Severity.CRITICAL,"BOSSMAN: лимит бюджета",
                sanitize(event.get("reason") or "Cloud spend остановлен бюджетом"),
                dedupe_key=f"{kind}:{sanitize(event.get('context_fingerprint') or event.get('required_extra_usd'))}")
        if kind in {"service.degraded","service.failed"}:
            name=sanitize(event.get("service") or event.get("name") or "service")
            return Notification.create(kind,Severity.ERROR,"BOSSMAN: сервис деградировал",
                f"{name}: {sanitize(event.get('reason') or event.get('error'))}",
                dedupe_key=f"service:{kind}:{name}:{sanitize(event.get('reason'))[:80]}")
        if kind in {"emergency_lock","system.emergency_lock","remote.lock_all"}:
            return Notification.create(kind,Severity.CRITICAL,"BOSSMAN: EMERGENCY LOCK",
                sanitize(event.get("reason") or "Все чувствительные действия заблокированы"),
                dedupe_key=f"emergency:{event.get('ts','')[:16]}")
        return None
