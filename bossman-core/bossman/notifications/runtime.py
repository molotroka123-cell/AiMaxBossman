from __future__ import annotations
import os,time
from pathlib import Path
from .bridge import EventBridge
from .dispatcher import NotificationDispatcher
from .models import ActionKind,Notification,NotificationAction,Severity
from .policy import sanitize
from .store import SQLiteNotificationStore
from .telegram_transport import TelegramTransport

def _settings():
    from ..config import settings
    return settings
def _workspace():
    try:return Path(_settings().workspace_dir)
    except Exception:return Path(os.environ.get("WORKSPACE_DIR","."))

STORE=SQLiteNotificationStore(Path(os.environ.get(
    "BOSSMAN_NOTIFICATION_DB",str(_workspace()/"_notifications"/"notifications.db")
)))
TELEGRAM=TelegramTransport(
    STORE,
    bot_token_provider=lambda:getattr(_settings(),"telegram_bot_token",""),
    chat_id_provider=lambda:getattr(_settings(),"telegram_chat_id",""),
    webhook_secret_provider=lambda:getattr(_settings(),"telegram_webhook_secret",""),
)
BRIDGE=EventBridge(STORE)
DISPATCHER=NotificationDispatcher(STORE,TELEGRAM)

async def enqueue_text(text:str,*,event_type="legacy.notify",severity=Severity.INFO):
    n=Notification.create(event_type,severity,"BOSSMAN",sanitize(text),
                          dedupe_key=f"{event_type}:{time.time_ns()}")
    STORE.enqueue(n)

async def enqueue_approval(approval_id:int,preview:str):
    aid=str(approval_id);fp=f"approval:{aid}"
    n=Notification.create("approval.created",Severity.WARNING,
        "BOSSMAN: требуется подтверждение",sanitize(preview),
        dedupe_key=f"approval:{aid}",context={"approval_id":aid},
        actions=[
            NotificationAction(ActionKind.APPROVE,"approval",aid,"✅ Да",fp),
            NotificationAction(ActionKind.DENY,"approval",aid,"❌ Нет",fp),
        ])
    STORE.enqueue(n)

async def handle_telegram_webhook(update:dict,secret_header:str):
    return await TELEGRAM.handle_webhook(update,secret_header)
