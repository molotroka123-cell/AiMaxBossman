from __future__ import annotations
import hashlib,json,secrets,sqlite3,threading,time
from dataclasses import asdict
from pathlib import Path
from .models import ActionKind,Notification,NotificationAction,QueueStatus,Severity

class CallbackRejected(RuntimeError): pass

class SQLiteNotificationStore:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self._lock=threading.RLock();self._init()

    def _connect(self):
        c=sqlite3.connect(self.path,timeout=30,isolation_level=None)
        c.row_factory=sqlite3.Row;c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=FULL");c.execute("PRAGMA busy_timeout=30000")
        return c

    def _init(self):
        with self._connect() as c:c.executescript("""
        CREATE TABLE IF NOT EXISTS queue(
          id TEXT PRIMARY KEY,event_type TEXT NOT NULL,severity TEXT NOT NULL,
          title TEXT NOT NULL,body TEXT NOT NULL,dedupe_key TEXT UNIQUE NOT NULL,
          context_json TEXT NOT NULL,actions_json TEXT NOT NULL,status TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0,next_attempt REAL NOT NULL,
          created_at REAL NOT NULL,updated_at REAL NOT NULL,last_error TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_queue_ready ON queue(status,next_attempt,created_at);
        CREATE TABLE IF NOT EXISTS callback_tokens(
          token_hash TEXT PRIMARY KEY,action TEXT NOT NULL,target_type TEXT NOT NULL,
          target_id TEXT NOT NULL,chat_id TEXT NOT NULL,fingerprint TEXT NOT NULL,
          expires_at REAL NOT NULL,used_at REAL,created_at REAL NOT NULL
        );
        """)

    def enqueue(self,n:Notification)->bool:
        now=time.time()
        actions=[{"kind":a.kind.value,"target_type":a.target_type,"target_id":a.target_id,
                  "label":a.label,"fingerprint":a.fingerprint,"expires_in_s":a.expires_in_s}
                 for a in n.actions]
        with self._lock,self._connect() as c:
            cur=c.execute("""INSERT OR IGNORE INTO queue(id,event_type,severity,title,body,
                dedupe_key,context_json,actions_json,status,attempts,next_attempt,
                created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (n.id,n.event_type,n.severity.value,n.title,n.body,n.dedupe_key,
                 json.dumps(n.context,ensure_ascii=False,default=str),
                 json.dumps(actions,ensure_ascii=False),
                 QueueStatus.PENDING.value,0,now,n.created_at,now))
            return cur.rowcount==1

    def recover_sending(self)->int:
        with self._lock,self._connect() as c:
            cur=c.execute("UPDATE queue SET status=?,updated_at=? WHERE status=?",
                          (QueueStatus.PENDING.value,time.time(),QueueStatus.SENDING.value))
            return cur.rowcount

    def claim_next(self,now:float|None=None)->Notification|None:
        now=now or time.time()
        with self._lock,self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row=c.execute("""SELECT * FROM queue WHERE status=? AND next_attempt<=?
                    ORDER BY created_at LIMIT 1""",(QueueStatus.PENDING.value,now)).fetchone()
                if not row:c.execute("COMMIT");return None
                cur=c.execute("UPDATE queue SET status=?,attempts=attempts+1,updated_at=? WHERE id=? AND status=?",
                              (QueueStatus.SENDING.value,now,row["id"],QueueStatus.PENDING.value))
                if cur.rowcount!=1:c.execute("ROLLBACK");return None
                c.execute("COMMIT");return self._notification(row)
            except Exception:c.execute("ROLLBACK");raise

    def mark_sent(self,nid:str)->None:
        with self._connect() as c:c.execute("UPDATE queue SET status=?,updated_at=?,last_error=NULL WHERE id=?",
                                            (QueueStatus.SENT.value,time.time(),nid))

    def mark_retry(self,nid:str,error:str,*,delay_s:float,max_attempts:int)->None:
        with self._connect() as c:
            row=c.execute("SELECT attempts FROM queue WHERE id=?",(nid,)).fetchone()
            if not row:return
            if int(row["attempts"])>=max_attempts:
                c.execute("UPDATE queue SET status=?,updated_at=?,last_error=? WHERE id=?",
                          (QueueStatus.DEAD.value,time.time(),str(error)[:500],nid))
            else:
                c.execute("UPDATE queue SET status=?,next_attempt=?,updated_at=?,last_error=? WHERE id=?",
                          (QueueStatus.PENDING.value,time.time()+max(0,delay_s),time.time(),
                           str(error)[:500],nid))

    def attempts(self,nid:str)->int:
        with self._connect() as c:
            row=c.execute("SELECT attempts FROM queue WHERE id=?",(nid,)).fetchone()
        return int(row["attempts"]) if row else 0

    def counts(self)->dict:
        with self._connect() as c:rows=c.execute("SELECT status,count(*) n FROM queue GROUP BY status").fetchall()
        return {r["status"]:r["n"] for r in rows}

    def create_callback(self,a:NotificationAction,chat_id:str)->str:
        raw=secrets.token_urlsafe(18)
        h=hashlib.sha256(raw.encode()).hexdigest();now=time.time()
        exp=now+max(30,min(int(a.expires_in_s),86400))
        with self._lock,self._connect() as c:c.execute(
            "INSERT INTO callback_tokens VALUES(?,?,?,?,?,?,?,NULL,?)",
            (h,a.kind.value,a.target_type,a.target_id,str(chat_id),a.fingerprint,exp,now))
        return raw

    def consume_callback(self,raw_token:str,chat_id:str):
        h=hashlib.sha256(raw_token.encode()).hexdigest();now=time.time()
        with self._lock,self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                row=c.execute("SELECT * FROM callback_tokens WHERE token_hash=?",(h,)).fetchone()
                if not row:raise CallbackRejected("unknown callback")
                if row["used_at"] is not None:raise CallbackRejected("callback already used")
                if row["expires_at"]<=now:raise CallbackRejected("callback expired")
                if str(row["chat_id"])!=str(chat_id):raise CallbackRejected("callback chat mismatch")
                cur=c.execute("UPDATE callback_tokens SET used_at=? WHERE token_hash=? AND used_at IS NULL",
                              (now,h))
                if cur.rowcount!=1:raise CallbackRejected("callback race")
                c.execute("COMMIT")
                return {"action":row["action"],"target_type":row["target_type"],
                        "target_id":row["target_id"],"fingerprint":row["fingerprint"]}
            except Exception:c.execute("ROLLBACK");raise

    @staticmethod
    def _notification(row):
        actions=[]
        for a in json.loads(row["actions_json"]):
            actions.append(NotificationAction(ActionKind(a["kind"]),a["target_type"],
                a["target_id"],a["label"],a["fingerprint"],int(a.get("expires_in_s",900))))
        return Notification(row["id"],row["event_type"],Severity(row["severity"]),row["title"],
                            row["body"],row["dedupe_key"],json.loads(row["context_json"]),
                            actions,float(row["created_at"]))
