"""SEC-03 (TZ-02 §2.2, приёмка 4.3): 6-я неудачная попытка за минуту → 429; после окна — снова 200;
20 неудач за час → lockout с событием; успешный вход сбрасывает счётчик."""
from __future__ import annotations

from bcc.login_guard import LoginRateLimiter


async def test_login_rate_limit_end_to_end(env):
    client = env.client
    for _ in range(5):
        assert (await client.post("/api/login", json={"token": "wrong"})).status_code == 401
    r = await client.post("/api/login", json={"token": "wrong"})
    assert r.status_code == 429 and "повторите" in r.json()["error"].get("hint", "")
    # даже правильный токен под лимитом не пускает — перебор не должен «угадывать» в окне
    assert (await client.post("/api/login", json={"token": env.svc.auth.token})).status_code == 429
    events = await env.svc.bus.recent(50)
    assert any(e.get("kind") == "auth.rate_limited" for e in events)
    # окно прошло (часы — инъекция, без sleep)
    env.svc.login_guard.clock = lambda base=env.svc.login_guard.clock: base() + 61.0
    r = await client.post("/api/login", json={"token": env.svc.auth.token})
    assert r.status_code == 200 and r.json()["ok"]


def test_limiter_lockout_and_reset():
    t = {"now": 1000.0}
    lim = LoginRateLimiter(clock=lambda: t["now"])
    for i in range(5):
        assert lim.check("c")[0]; lim.failure("c")
    ok, retry = lim.check("c")
    assert not ok and retry > 0
    t["now"] += 61
    assert lim.check("c")[0]
    locked = False
    for i in range(30):
        t["now"] += 65                         # по одной неудаче в минуту → корзина не мешает, час набирается
        assert lim.check("c")[0]
        if lim.failure("c"):
            locked = True
            break
    assert locked and not lim.check("c")[0] and lim.check("c")[1] > 800
    t["now"] += 901
    assert lim.check("c")[0]
    lim.failure("c"); lim.success("c")
    assert lim.check("c") == (True, 0.0)
