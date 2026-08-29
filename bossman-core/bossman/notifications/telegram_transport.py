from __future__ import annotations
import hmac,os
from typing import Awaitable,Callable
import httpx
from .models import ActionKind,Notification
from .store import CallbackRejected,SQLiteNotificationStore

API="https://api.telegram.org"

class TelegramTransportError(RuntimeError):pass

ActionHandler=Callable[[dict,str],Awaitable[None]]

class TelegramTransport:
    def __init__(self,store:SQLiteNotificationStore,*,bot_token_provider,chat_id_provider,
                 webhook_secret_provider,action_handler:ActionHandler|None=None):
        self.store=store;self._token=bot_token_provider;self._chat=chat_id_provider
        self._secret=webhook_secret_provider;self._action_handler=action_handler or self._default_action

    def enabled(self)->bool:return bool(self._token() and self._chat())

    def __repr__(self)->str:return "TelegramTransport(enabled=%s)"%self.enabled()

    async def _post(self,method:str,payload:dict):
        token=self._token()
        if not token:raise TelegramTransportError("telegram disabled")
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                r=await client.post(f"{API}/bot{token}/{method}",json=payload)
        except Exception as exc:
            # Never persist exception text containing the bot-token URL.
            raise TelegramTransportError(type(exc).__name__) from exc
        if r.status_code<200 or r.status_code>=300:
            raise TelegramTransportError(f"telegram HTTP {r.status_code}")
        try:return r.json()
        except Exception:return {"ok":True}

    async def send(self,n:Notification)->None:
        if not self.enabled():raise TelegramTransportError("telegram disabled")
        chat=str(self._chat())
        keyboard=[]
        if n.actions:
            row=[]
            for a in n.actions:
                opaque=self.store.create_callback(a,chat)
                row.append({"text":a.label[:50],"callback_data":"b:"+opaque})
            keyboard=[row]
        text=f"{n.title}\n\n{n.body}"[:4000]
        payload={"chat_id":chat,"text":text,"disable_web_page_preview":True}
        if keyboard:payload["reply_markup"]={"inline_keyboard":keyboard}
        await self._post("sendMessage",payload)

    async def handle_webhook(self,update:dict,secret_header:str)->dict:
        expected=self._secret() or ""
        if not expected or not hmac.compare_digest(expected,secret_header or ""):
            raise CallbackRejected("telegram webhook denied")
        cb=update.get("callback_query") or {}
        data=str(cb.get("data") or "")
        if not data.startswith("b:") or len(data)>64:
            raise CallbackRejected("unsupported callback")
        msg=cb.get("message") or {};chat=str((msg.get("chat") or {}).get("id") or "")
        if not chat or chat!=str(self._chat()):
            raise CallbackRejected("telegram chat denied")
        allowed=os.environ.get("TELEGRAM_ALLOWED_USER_IDS","").strip()
        if allowed:
            allowed_ids={x.strip() for x in allowed.split(",") if x.strip()}
            uid=str((cb.get("from") or {}).get("id") or "")
            if uid not in allowed_ids:raise CallbackRejected("telegram user denied")
        action=self.store.consume_callback(data[2:],chat)
        await self._action_handler(action,chat)
        cbid=cb.get("id")
        if cbid:
            try:await self._post("answerCallbackQuery",{"callback_query_id":cbid,"text":"BOSSMAN: принято"})
            except TelegramTransportError:pass
        return {"ok":True}

    async def _default_action(self,action:dict,chat_id:str)->None:
        if action["target_type"]!="approval":
            raise CallbackRejected("unsupported telegram action target")
        if action["action"] not in {ActionKind.APPROVE.value,ActionKind.DENY.value}:
            raise CallbackRejected("unsupported approval action")
        try:
            from .. import approvals
        except Exception as exc:
            raise CallbackRejected("approval service unavailable") from exc
        row=await approvals.decide(int(action["target_id"]),
                                   action["action"]==ActionKind.APPROVE.value,
                                   f"tg:chat:{chat_id}")
        if not row:raise CallbackRejected("approval already decided or absent")
