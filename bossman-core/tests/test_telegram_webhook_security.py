import pytest
from bossman.notifications.store import CallbackRejected,SQLiteNotificationStore
from bossman.notifications.telegram_transport import TelegramTransport

async def noop(action,chat):pass

def transport(tmp_path):
    return TelegramTransport(SQLiteNotificationStore(tmp_path/"n.db"),
        bot_token_provider=lambda:"TEST_BOT_TOKEN_NOT_REAL",
        chat_id_provider=lambda:"123",
        webhook_secret_provider=lambda:"WEBHOOK_TEST_SECRET_NOT_REAL",
        action_handler=noop)

@pytest.mark.asyncio
async def test_wrong_webhook_secret_rejected_before_action(tmp_path):
    t=transport(tmp_path)
    with pytest.raises(CallbackRejected):
        await t.handle_webhook({"callback_query":{"data":"b:opaque","message":{"chat":{"id":123}}}},"wrong")

@pytest.mark.asyncio
async def test_legacy_raw_approve_callback_rejected(tmp_path):
    t=transport(tmp_path)
    with pytest.raises(CallbackRejected):
        await t.handle_webhook({"callback_query":{"data":"approve:42","message":{"chat":{"id":123}}}},"WEBHOOK_TEST_SECRET_NOT_REAL")

def test_transport_repr_never_contains_bot_token(tmp_path):
    t=transport(tmp_path)
    assert "TEST_BOT_TOKEN_NOT_REAL" not in repr(t)
