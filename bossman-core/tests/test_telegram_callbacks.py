import time
import pytest
from bossman.notifications.models import ActionKind,NotificationAction
from bossman.notifications.store import CallbackRejected,SQLiteNotificationStore

# Идентификатор намеренно длинный и характерный: токен — 24 символа
# `secrets.token_urlsafe`, и короткая цель вроде "42" встречается в нём
# случайно примерно раз на 180 прогонов (шанс ~0.5%). Это делало тест
# флаки, ничего не проверяя дополнительно.
TARGET_ID="approval-0123456789abcdef"

def action(target_id:str=TARGET_ID):
    return NotificationAction(ActionKind.APPROVE,"approval",target_id,"Approve",
                              f"approval:{target_id}",60)

def test_callback_is_opaque_bound_and_single_use(tmp_path):
    s=SQLiteNotificationStore(tmp_path/"n.db")
    token=s.create_callback(action(),"123")
    assert TARGET_ID not in token
    with pytest.raises(CallbackRejected):s.consume_callback(token,"999")
    got=s.consume_callback(token,"123")
    assert got["target_id"]==TARGET_ID
    with pytest.raises(CallbackRejected):s.consume_callback(token,"123")

def test_callback_token_not_stored_plaintext(tmp_path):
    s=SQLiteNotificationStore(tmp_path/"n.db")
    token=s.create_callback(action(),"123")
    raw=(tmp_path/"n.db").read_bytes()
    assert token.encode() not in raw
