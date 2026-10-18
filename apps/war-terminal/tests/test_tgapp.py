"""Owner-only Telegram Mini App: initData auth (positive + negative controls), session proxy whitelist, dry-run only,
no token/owner-id leaks, static UI, pult menu-button tool. Uses a THROWAWAY bot token and a fake owner id only."""
import importlib.util
import json
import pathlib
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from conftest import TOK, TODAY
from fixtures import fake_get, fake_post
from warterminal import tgapp
from warterminal.api import create_app

FAKE_TOKEN = "123456789:TEST-throwaway-token-not-real_xyzXYZ"
FAKE_OWNER = 987654321012
OTHER_USER = 111222333444
ROOT = pathlib.Path(__file__).resolve().parents[1]
ROLE_TOK = {r: f"role-token-{r}-{'q' * 24}" for r in TOK}    # long, unique: a leak check on "tv" would hit Czech words


def defence_fixtures(retrieved_at: str, today: date) -> list[dict]:
    """Test-only Czech defence/security tenders with provenance fields, written into a temp DB (titles are marked TEST)."""
    rows = [
        ("TGT-001", "Dodávka balistických vest pro Policii ČR (TEST)", ["35113400"], 4800000, "CZK", 9, "Policejní prezidium ČR"),
        ("TGT-002", "Munice 9 mm Luger pro výcvik (TEST)", ["35330000"], 12500000, "CZK", 26, "Ministerstvo obrany ČR"),
        ("TGT-003", "Kamerový systém ochrany areálu nemocnice (TEST)", ["35125300"], 2100000, "CZK", 12, "Fakultní nemocnice Brno"),
        ("TGT-004", "Perimetrická ochrana a vjezdové závory (TEST)", ["35121000"], 180000, "EUR", 33, "Letiště Praha, a. s."),
        ("TGT-005", "Ochranné oděvy pro hasiče <script>alert(1)</script> (TEST)", ["35113000"], None, None, 41, "HZS Jihomoravského kraje"),
        ("TGT-006", "Uzavřená zakázka — zabezpečovací technika (TEST)", ["35120000"], 900000, "CZK", -2, "Město Ostrava"),
    ]
    out = []
    for nid, title, cpv, val, cur, days, buyer in rows:
        out.append({"id": f"test:{nid}", "official_notice_id": nid, "title": title, "jurisdiction": "CZ", "country": "CZ", "category": "security_defence",
                    "status": "open" if days >= 0 else "closed", "deadline": f"{(today + timedelta(days=days)).isoformat()}T10:00:00Z",
                    "estimated_value": val, "currency": cur, "source_url": f"https://nen.nipez.cz/verejne-zakazky/detail-zakazky/{nid}",
                    "source_id": "cz_nen", "retrieved_at": retrieved_at, "published_at": today.isoformat(), "source_version": "test", "checksum": nid,
                    "data_quality": "ok", "buyer": buyer, "cpv": cpv})
    return out


def write_companion(tmp: pathlib.Path, *, token=FAKE_TOKEN, owner=FAKE_OWNER, role="owner") -> tuple[pathlib.Path, pathlib.Path]:
    cfg, env = tmp / "config.json", tmp / "companion.env"
    cfg.write_text(json.dumps({"people": [{"role": role, "user_id": owner, "chat_id": owner, "agent_id": 1},
                                          {"role": "friend", "user_id": OTHER_USER, "chat_id": OTHER_USER}]}), encoding="utf-8")
    env.write_text(f'TG_COMPANION_BOT_TOKEN="{token}"\nTG_COMPANION_CORE_TOKEN=other\n', encoding="utf-8")
    return cfg, env


def init_data(user_id=FAKE_OWNER, *, token=FAKE_TOKEN, auth_date=None, **extra) -> str:
    fields = {"query_id": "AAH-test", "user": {"id": user_id, "first_name": "Owner", "language_code": "ru"},
              "auth_date": int(auth_date if auth_date is not None else time.time())} | extra
    return tgapp.sign_init_data(fields, token)


class Tg:
    def __init__(self, tmp_path, monkeypatch, **companion):
        comp = tmp_path / "companion"
        comp.mkdir()
        cfg, env = write_companion(comp, **companion)
        monkeypatch.setenv("WAR_TERMINAL_TG_CONFIG", str(cfg))
        monkeypatch.setenv("WAR_TERMINAL_TG_ENV", str(env))
        self.app = create_app(tmp_path / "data", live=False, tokens=ROLE_TOK, post=fake_post(), get=fake_get, today=TODAY)
        self.store = self.app.state.store
        for d in defence_fixtures("2026-10-17T08:00:00+00:00", TODAY):
            self.store.upsert_tender(d)
        self.c = TestClient(self.app, base_url="https://testserver")      # Secure cookie is only sent over https
        self.texts: list[str] = []
        self.csrf = None

    def _log(self, r):
        self.texts.append(r.text + json.dumps(dict(r.headers)))
        return r

    def auth(self, data=None, raw=None):
        if raw is not None:
            return self._log(self.c.post("/tg/api/auth", content=raw, headers={"content-type": "application/json"}))
        r = self._log(self.c.post("/tg/api/auth", json={"init_data": init_data() if data is None else data}))
        if r.status_code == 200:
            self.csrf = r.json()["csrf"]
        return r

    def get(self, path, csrf=True, **kw):
        h = kw.pop("headers", {})
        if csrf and self.csrf:
            h["X-TG-CSRF"] = self.csrf
        return self._log(self.c.get("/tg/api/" + path, headers=h, **kw))

    def post(self, path, body, csrf=True):
        return self._log(self.c.post("/tg/api/" + path, json=body, headers={"X-TG-CSRF": self.csrf} if csrf and self.csrf else {}))


@pytest.fixture
def tg(tmp_path, monkeypatch):
    return Tg(tmp_path, monkeypatch)


# ------------------------------------------------------------------ algorithm
def test_check_init_data_official_algorithm_known_vector():
    """Independent re-implementation of the documented steps, so a bug shared by sign+check would still be caught."""
    import hashlib
    import hmac
    from urllib.parse import urlencode
    fields = {"auth_date": "1700000000", "query_id": "Q", "user": '{"id":42,"first_name":"A"}'}
    dcs = "auth_date=1700000000\nquery_id=Q\nuser={\"id\":42,\"first_name\":\"A\"}"
    key = hmac.new(b"WebAppData", b"1:abc", hashlib.sha256).digest()
    q = urlencode(dict(fields, hash=hmac.new(key, dcs.encode(), hashlib.sha256).hexdigest()))
    assert tgapp.check_init_data(q, "1:abc", now=1700000100)["user"]["id"] == 42
    assert tgapp.check_init_data(q, "1:abd", now=1700000100) is None                 # other token
    assert tgapp.check_init_data(q, "1:abc", now=1700000000 + 3601) is None          # older than 1 hour
    assert tgapp.check_init_data(q.replace("Q", "R"), "1:abc", now=1700000100) is None   # tampered field
    assert tgapp.check_init_data(q + "&query_id=Q", "1:abc", now=1700000100) is None    # duplicate key


# ------------------------------------------------------------------ auth: positive + negative controls
def test_owner_with_valid_signature_gets_short_lived_secure_cookie(tg):
    r = tg.auth()
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["ok"] is True and j["csrf"] and j["expires_in"] <= 3600 and j["access"] == "viewer+dry_run_assessment"
    sc = r.headers["set-cookie"].lower()
    assert "wt_tg=" in sc and "httponly" in sc and "secure" in sc and "samesite=none" in sc and "path=/tg/" in sc and "max-age=1800" in sc


@pytest.mark.parametrize("case", ["wrong_signature", "tampered_user", "expired", "future", "other_user", "missing", "empty", "garbage"])
def test_negative_controls_are_403_without_data(tg, case):
    now = int(time.time())
    data = {"wrong_signature": init_data(token="999:another-bot-token"),
            "tampered_user": init_data().replace("987654321012", "987654321013"),
            "expired": init_data(auth_date=now - 3601),
            "future": init_data(auth_date=now + 600),
            "other_user": init_data(OTHER_USER),            # correctly signed by the same bot, but not the owner
            "missing": None, "empty": "", "garbage": "a=%ZZ&&hash"}[case]
    r = tg.auth(raw=b"{}") if case == "missing" else tg.auth(data)
    assert r.status_code == 403
    assert r.json() == tgapp.DENY                         # one answer for every reason: no oracle, no data
    assert "set-cookie" not in r.headers
    assert tg.get("tenders", csrf=False).status_code == 401


def test_auth_date_just_inside_one_hour_passes(tg):
    assert tg.auth(init_data(auth_date=int(time.time()) - 3500)).status_code == 200


def test_not_configured_or_non_owner_config_fails_closed(tmp_path, monkeypatch):
    t = Tg(tmp_path, monkeypatch, role="friend")         # people[0] is not the owner -> no owner id
    r = t.auth()
    assert r.status_code == 503 and "csrf" not in r.text
    monkeypatch.setenv("WAR_TERMINAL_TG_ENV", str(tmp_path / "nope.env"))
    assert t.auth().status_code == 503


def test_owner_id_and_token_loaders(tmp_path):
    cfg, env = write_companion(tmp_path)
    assert tgapp.load_owner_id(cfg) == FAKE_OWNER and tgapp.load_bot_token(env) == FAKE_TOKEN
    cfg.write_text(json.dumps({"people": [{"role": "owner", "chat_id": 55}]}), encoding="utf-8")
    assert tgapp.load_owner_id(cfg) == 55                # chat_id fallback
    cfg.write_text("{broken", encoding="utf-8")
    assert tgapp.load_owner_id(cfg) is None
    assert tgapp.load_owner_id(tmp_path / "missing.json") is None


# ------------------------------------------------------------------ session + proxy
def test_owner_sees_czech_open_defence_tenders(tg):
    tg.auth()
    r = tg.get("tenders?country=CZE&category=security_defence&status=open")
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert {i["official_notice_id"] for i in items} == {"TGT-001", "TGT-002", "TGT-003", "TGT-004", "TGT-005"}
    lic = {i["official_notice_id"] for i in items if "controlled_goods" in i["assessment"]["open_gates"]}
    assert lic == {"TGT-001", "TGT-002"}                 # ballistic vests (name) + ammunition (CPV 353); the rest is civil security
    assert r.headers["cache-control"] == "no-store"
    key = next(i["key"] for i in items if i["official_notice_id"] == "TGT-002")
    ck = tg.get(f"tenders/{key}/checklist")
    assert ck.status_code == 200 and ck.json()["draft"] is True
    assert any("controlled_goods" in it["step"] for it in ck.json()["items"])
    assert tg.get(f"tenders/{key}").json()["tender"]["official_notice_id"] == "TGT-002"


def test_session_requires_cookie_and_csrf(tg):
    tg.auth()
    assert tg.get("tenders", csrf=False).status_code == 401                              # cookie alone (cross-site) is not enough
    assert tg.get("tenders", headers={"X-TG-CSRF": "x" * 32}, csrf=False).status_code == 401
    bare = TestClient(tg.app, base_url="https://testserver")
    assert bare.get("/tg/api/tenders", headers={"X-TG-CSRF": tg.csrf}).status_code == 401  # csrf alone, no cookie
    bare.cookies.set("wt_tg", "forged", domain="testserver.local", path="/tg/")
    assert bare.get("/tg/api/tenders", headers={"X-TG-CSRF": tg.csrf, "Cookie": "wt_tg=forged"}).status_code == 401


def test_client_role_tokens_do_not_open_the_tg_proxy(tg):
    r = tg.c.get("/tg/api/tenders", headers={"Authorization": f"Bearer {ROLE_TOK['owner']}"})
    assert r.status_code == 401


def test_session_expires(tg, monkeypatch):
    tg.auth()
    assert tg.get("tenders").status_code == 200
    real = time.time
    monkeypatch.setattr(tgapp, "_now", lambda: real() + tgapp.SESSION_TTL + 5)
    assert tg.get("tenders").status_code == 401


@pytest.mark.parametrize("method,path", [("GET", "approvals"), ("GET", "audit/events"), ("GET", "company/profile"), ("POST", "ingest"),
                                         ("POST", "actions/submit-bid"), ("POST", "approvals/1/decide"), ("GET", "learning/dataset"),
                                         ("DELETE", "tenders/CZ:TGT-001"), ("POST", "tenders"), ("GET", "../v1/approvals")])
def test_everything_outside_whitelist_is_403(tg, method, path):
    tg.auth()
    r = tg._log(tg.c.request(method, "/tg/api/" + path, headers={"X-TG-CSRF": tg.csrf}, json={"source": "ted"}))
    assert r.status_code in (403, 404), (path, r.status_code)
    assert r.status_code == 403 or path.startswith("..")
    assert tg.store.list_approvals() == []


def test_assessment_is_dry_run_only_and_stores_nothing(tg):
    tg.auth()
    ok = tg.post("assessments", {"tender_key": "CZ:TGT-003", "dry_run": True})
    assert ok.status_code == 200, ok.text
    assert ok.json()["dry_run"] is True and "id" not in ok.json() and "ai" not in ok.json()
    for bad in ({"tender_key": "CZ:TGT-003", "dry_run": False}, {"tender_key": "CZ:TGT-003"}, {"tender_key": "CZ:TGT-003", "dry_run": "true"},
                {"tender_key": "CZ:TGT-003", "dry_run": True, "use_ai": True}, {"tender_key": "CZ:TGT-003", "dry_run": True, "extra": 1}, ["x"]):
        r = tg.post("assessments", bad)
        assert r.status_code == 403, bad
    assert tg.store.docs("assessments") == []
    assert all(e["event"] != "assessment.created" for e in tg.store.audit_events(500))


def test_token_and_owner_id_never_leak(tg):
    tg.auth(init_data(OTHER_USER))
    tg.auth(init_data(token="1:x"))
    tg.auth()
    tg.get("tenders?country=CZE&category=security_defence&status=open")
    tg.get("tenders/CZ:TGT-001/checklist")
    tg.post("assessments", {"tender_key": "CZ:TGT-001", "dry_run": True})
    tg.post("assessments", {"tender_key": "CZ:TGT-001", "dry_run": False})
    blob = "\n".join(tg.texts) + json.dumps(tg.store.audit_events(500))
    for secret in (FAKE_TOKEN, FAKE_TOKEN.split(":")[1], str(FAKE_OWNER), str(OTHER_USER), ROLE_TOK["viewer"], ROLE_TOK["analyst"]):
        assert secret not in blob, "secret leaked"
    events = [e["event"] for e in tg.store.audit_events(500)]
    assert "tg.auth.ok" in events and "tg.auth.denied" in events


def test_auth_brake_after_many_failures(tg):
    for _ in range(tgapp.FAIL_LIMIT + 1):
        tg.auth("bad")
    assert tg.auth().status_code == 429


# ------------------------------------------------------------------ static UI
def test_static_ui_served_and_safe(tg):
    r = tg.c.get("/tg/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    html = r.text
    assert 'src="https://telegram.org/js/telegram-web-app.js"' in html
    assert "Только мониторинг. Подача заявок и контакты с заказчиками — только после юридической проверки и вашего решения." in html
    scripts = [s for s in html.split("<script")[1:]]
    assert len(scripts) == 2 and "app.js" in scripts[1]                # only telegram.org + own script
    js = (ROOT / "warterminal" / "ui" / "tg" / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "eval(" not in js
    for needle in ("Telegram.WebApp", "tg.ready()", "tg.expand()", "themeParams", "MainButton", "BackButton", "openLink",
                   "country=CZE&category=security_defence&status=open", "controlled_goods", "/checklist", "dry_run: true"):
        assert needle in js, needle
    assert tg.c.get("/tg", follow_redirects=False).status_code in (301, 302, 307)


def test_main_api_unchanged_for_role_tokens(tg):
    assert tg.c.get("/api/v1/tenders").status_code == 401
    assert tg.c.get("/api/v1/tenders", headers={"Authorization": f"Bearer {ROLE_TOK['viewer']}"}).status_code == 200


# ------------------------------------------------------------------ pult menu-button tool
def _tool():
    spec = importlib.util.spec_from_file_location("set_pult_menu_button", ROOT / "tools" / "set_pult_menu_button.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_menu_button_tool_dry_run_by_default(tmp_path, monkeypatch, capsys):
    cfg, env = write_companion(tmp_path)
    monkeypatch.setenv("WAR_TERMINAL_TG_CONFIG", str(cfg))
    monkeypatch.setenv("WAR_TERMINAL_TG_ENV", str(env))
    m = _tool()
    calls = []
    assert m.main(["--url", "https://win-iqprhtsajl9.tail0f845b.ts.net"], sender=lambda *a: calls.append(a)) == 0
    out = capsys.readouterr().out
    assert calls == [] and "DRY RUN" in out and "https://win-iqprhtsajl9.tail0f845b.ts.net/tg/" in out and '"web_app"' in out
    assert FAKE_TOKEN not in out and str(FAKE_OWNER) not in out
    p = m.build_payload("https://h.example.ts.net/", 5)
    assert p == {"chat_id": 5, "menu_button": {"type": "web_app", "text": "Оборонка", "web_app": {"url": "https://h.example.ts.net/tg/"}}}
    assert m.webapp_url("https://h.example.ts.net/tg") == "https://h.example.ts.net/tg/"


def test_menu_button_tool_refuses_non_https_and_apply_targets_owner_only(tmp_path, monkeypatch, capsys):
    cfg, env = write_companion(tmp_path)
    monkeypatch.setenv("WAR_TERMINAL_TG_CONFIG", str(cfg))
    monkeypatch.setenv("WAR_TERMINAL_TG_ENV", str(env))
    m = _tool()
    calls = []
    for bad in ("http://win-iqprhtsajl9.tail0f845b.ts.net", "ftp://x", "https://", "https://u:p@h.ts.net", "https://h.ts.net/?a=1", "javascript:alert(1)"):
        assert m.main(["--url", bad, "--apply"], sender=lambda *a: calls.append(a)) == 2, bad
    assert calls == []
    assert m.main(["--url", "https://h.example.ts.net", "--apply"], sender=lambda tok, p: calls.append((tok, p)) or {"ok": True}) == 0
    assert len(calls) == 1 and calls[0][0] == FAKE_TOKEN and calls[0][1]["chat_id"] == FAKE_OWNER
    assert FAKE_TOKEN not in capsys.readouterr().out
