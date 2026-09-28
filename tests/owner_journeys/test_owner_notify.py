import json

from tools.owner_journeys import owner_notify as on

ORIG_SEND = on.CompanionTelegram.send


class Clock:
    def __init__(self, t=1_790_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


class FakeTransport:
    name = "fake"

    def __init__(self, results=None):
        self.sent, self.results = [], list(results or [])

    def known_secrets(self):
        return ("VERYSECRETVALUE123",)

    def send(self, text):
        if self.results:
            res = self.results.pop(0)
            if not res.get("ok"):
                return res
        self.sent.append(text)
        return {"ok": True, "message_id": 100 + len(self.sent)}


def _n(tmp_path, transport, clock, **lim):
    return on.Notifier(tmp_path, transport, on.Limits(**lim), clock=clock)


def test_secret_patterns_and_known_values_are_refused(tmp_path):
    tr, clock = FakeTransport(), Clock()
    n = _n(tmp_path, tr, clock)
    assert n.enqueue("k", "report with sk-or-v1-abcdefghijklmnop inside") == "REFUSED_SECRET"
    assert n.enqueue("k", "bot 123456789:AAEabcdefghijklmnopqrstuvwxyz0123456") == "REFUSED_SECRET"
    assert n.enqueue("k", "value VERYSECRETVALUE123 leaked") == "REFUSED_SECRET"
    assert n.flush() == [] and tr.sent == []
    assert n.status()["refused"] == 3


def test_coalesce_dedup_min_interval_and_daily_ceiling(tmp_path):
    tr, clock = FakeTransport(), Clock()
    n = _n(tmp_path, tr, clock, min_interval_s=600, max_per_day=3)
    n.enqueue("goal-report", "first")
    n.enqueue("goal-report", "second")          # same key -> newest wins
    assert [r["status"] for r in n.flush()] == ["SENT"] and tr.sent == ["second"]
    assert n.enqueue("goal-report", "second") == "DEDUP"
    n.enqueue("goal-report", "third")
    clock.t += 60
    assert n.flush() == [] and len(tr.sent) == 1   # routine message waits for the min interval
    n.enqueue("alert-stop", "stop!", urgent=True)
    assert [r["key"] for r in n.flush()] == ["alert-stop"]  # urgent goes first after 30 s
    clock.t += 700
    n.flush()
    assert tr.sent == ["second", "stop!", "third"]
    n.enqueue("goal-report", "fourth")
    clock.t += 700
    assert n.flush() == [] and len(tr.sent) == 3   # ceiling of 3 per day reached, stays queued
    clock.t += 86400
    n.flush()
    assert tr.sent[-1] == "fourth"


def test_network_down_keeps_the_queue_and_delivers_later(tmp_path):
    tr = FakeTransport(results=[{"ok": False, "permanent": False, "error": "URLError"}] * 2)
    clock = Clock()
    n = _n(tmp_path, tr, clock)
    n.enqueue("goal-report", "offline report")
    assert n.flush()[0]["status"] == "RETRY_LATER"
    assert n.flush() == []                       # backoff respected
    clock.t += 3600
    assert n.flush()[0]["status"] == "RETRY_LATER"
    clock.t += 3600
    # a restart: a new Notifier over the same durable outbox delivers it
    n2 = _n(tmp_path, tr, clock)
    res = n2.flush()
    assert res[0]["status"] == "SENT" and res[0]["message_id"] == 101 and tr.sent == ["offline report"]
    log = [json.loads(x) for x in (tmp_path / "notify" / "sent.jsonl").read_text(encoding="utf-8").splitlines()]
    assert log[-1]["status"] == "SENT" and "text" not in log[-1]


def test_reporting_off_drops_without_network(tmp_path):
    n = _n(tmp_path, on.NullTransport(), Clock())
    n.enqueue("goal-report", "x")
    assert n.flush()[0]["status"] == "DROPPED" and n.status()["pending"] == 0


def test_companion_transport_uses_only_the_owner_chat_and_sendmessage(tmp_path, monkeypatch):
    comp = tmp_path / "companion"
    comp.mkdir()
    (comp / "companion.env").write_text("JEFF_BOT_TOKEN=999:jeff\nTG_COMPANION_BOT_TOKEN=111:companion\n")
    (comp / "config.json").write_text(json.dumps({"people": [
        {"role": "guest", "user_id": 5, "chat_id": 5}, {"role": "owner", "user_id": 7, "chat_id": 7}]}))
    monkeypatch.setattr(on.CompanionTelegram, "send", ORIG_SEND)
    seen = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"ok": True, "result": {"message_id": 42}}).encode()

    def fake_urlopen(req, timeout=0):
        seen["url"], seen["data"] = req.full_url, req.data.decode()
        return Resp()

    monkeypatch.setattr(on.urllib.request, "urlopen", fake_urlopen)
    res = on.CompanionTelegram(comp).send("привет")
    assert res == {"ok": True, "message_id": 42, "permanent": False, "error": None}
    assert seen["url"].endswith("/bot111:companion/sendMessage") and "getUpdates" not in seen["url"]
    assert "chat_id=7" in seen["data"]
    assert on.CompanionTelegram(comp).known_secrets() == ("111:companion",)


def test_incomplete_companion_config_is_not_a_permanent_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(on.CompanionTelegram, "send", ORIG_SEND)
    res = on.CompanionTelegram(tmp_path).send("x")
    assert res["ok"] is False and res["permanent"] is False
