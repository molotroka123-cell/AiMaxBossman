"""PoC: (A) CC review_gate verification spoofing by echoing criteria; (B) core auth fail-closed (BUG-004 relevance)."""
import asyncio, sys, types
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\command-center")
sys.path.insert(0, r"C:\AiMaxBossman-claude-bossman-control-v03-43igbk\bossman-core")

# ---- A: review_gate._verdict deterministic fallback ----
from bcc.features import review_gate
async def a():
    review = {"criteria": "all tests pass and no security issues"}  # no reviewer_agent_id
    # attacker/worker answer just echoes the criteria string, does nothing real
    ans = "I confirm: all tests pass and no security issues (nothing actually executed)"
    passed, why = await review_gate._verdict(None, review, ans)
    print("[A] echo-criteria answer -> passed=%s (%s)" % (passed, why))
    honest = "I could not run the tests; environment missing."
    p2, w2 = await review_gate._verdict(None, review, honest)
    print("[A] honest-failure answer -> passed=%s (%s)" % (p2, w2))
    print("[A] VERDICT:", "SPOOFABLE (echo => PASS without evidence)" if passed and not p2 else "ok")
asyncio.run(a())

# ---- B: core DeviceService authenticate fail-closed, no principal cache ----
from bossman.remote_client.service import DeviceService
from bossman.remote_client.store import InMemoryDeviceStore
from bossman.remote_client.auth import SCOPE_ADMIN, SCOPE_CHAT
from bossman.errors import AuthDenied, DeviceRevoked
async def b():
    svc = DeviceService(InMemoryDeviceStore())
    did, raw = await svc.enroll("dev", [SCOPE_CHAT])
    p = await svc.authenticate(f"Bearer {raw}")
    print("[B] valid token -> device=%s scopes=%s admin?=%s" % (p.device_id, sorted(p.scopes), p.has_scope(SCOPE_ADMIN)))
    for bad in ["", "Bearer", "Bearer wrongtoken", "Basic x", None]:
        try:
            await svc.authenticate(bad); print("[B] BYPASS with %r" % bad)
        except (AuthDenied, DeviceRevoked) as e:
            print("[B] denied %r -> %s" % (bad, type(e).__name__))
    await svc.revoke_device(did)
    try:
        await svc.authenticate(f"Bearer {raw}"); print("[B] BYPASS after revoke!")
    except DeviceRevoked:
        print("[B] revoked token -> DeviceRevoked (fail-closed)")
    # no cached principal object across calls
    import bossman.remote_client.service as m
    print("[B] module-level principal cache present:", any(k for k in dir(m) if 'cache' in k.lower()))
asyncio.run(b())
