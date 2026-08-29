import time
import pytest
from bossman.notifications.models import ActionKind,NotificationAction
from bossman.notifications.store import CallbackRejected,SQLiteNotificationStore

def action():
    return NotificationAction(ActionKind.APPROVE,"approval","42","Approve","approval:42",60)

def test_callback_is_opaque_bound_and_single_use(tmp_path):
    s=SQLiteNotificationStore(tmp_path/"n.db")
    token=s.create_callback(action(),"123")
    assert "42" not in token
    with pytest.raises(CallbackRejected):s.consume_callback(token,"999")
    got=s.consume_callback(token,"123")
    assert got["target_id"]=="42"
    with pytest.raises(CallbackRejected):s.consume_callback(token,"123")

def test_callback_token_not_stored_plaintext(tmp_path):
    s=SQLiteNotificationStore(tmp_path/"n.db")
    token=s.create_callback(action(),"123")
    raw=(tmp_path/"n.db").read_bytes()
    assert token.encode() not in raw
