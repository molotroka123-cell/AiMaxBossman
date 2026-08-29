from bossman.notifications.models import Notification,Severity
from bossman.notifications.store import SQLiteNotificationStore

def test_restart_recovers_sending_to_pending(tmp_path):
    path=tmp_path/"n.db";s=SQLiteNotificationStore(path)
    s.enqueue(Notification.create("x",Severity.INFO,"t","b",dedupe_key="1"))
    claimed=s.claim_next()
    assert claimed is not None
    s2=SQLiteNotificationStore(path)
    assert s2.recover_sending()==1
    assert s2.claim_next() is not None
