"""Tiny client for the real mobile pult :8840 (/m/api/...). Token never printed."""
import json, sys
from pathlib import Path
import httpx

sys.path.insert(0, r"C:\Users\asd\Bossman\wt-final-20261023\apps\bossman-mobile")
from bossmobile.auth import load_or_create_pairing  # noqa: E402

BASE = "http://127.0.0.1:8840"
STATE = Path(__file__).with_name("session.json")


def client() -> httpx.Client:
    c = httpx.Client(base_url=BASE, timeout=200.0)
    if STATE.exists():
        st = json.loads(STATE.read_text())
        c.cookies.update(st["cookies"])
        c.headers["X-BM-CSRF"] = st["csrf"]
        if c.get("/m/api/session").status_code == 200:
            return c
        c.cookies.clear()
    tok = load_or_create_pairing(Path.home() / ".bossman-mobile")
    r = c.post("/m/api/login", json={"token": tok, "label": "Claude agent-mode proof 25.10"})
    r.raise_for_status()
    c.headers["X-BM-CSRF"] = r.json()["csrf"]
    STATE.write_text(json.dumps({"cookies": dict(c.cookies), "csrf": r.json()["csrf"]}))
    return c


if __name__ == "__main__":
    c = client()
    method, path = sys.argv[1], sys.argv[2]
    body = json.loads(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
    r = c.request(method, path, json=body)
    print(r.status_code)
    try:
        print(json.dumps(r.json(), ensure_ascii=False, indent=1)[:int(sys.argv[4]) if len(sys.argv) > 4 else 6000])
    except Exception:
        print(r.text[:3000])


def panel_headers(c: httpx.Client) -> dict:
    import re
    html = c.get("/panel/").text
    m = re.search(r"localStorage\.setItem\('bcc\.csrf',\s*(\".*?\")\)", html)
    return {"X-BCC-CSRF": json.loads(m.group(1)), "Origin": BASE}
