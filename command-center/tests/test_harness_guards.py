"""Harness guards G1-G6 (03.11.2026): each test reproduces the real incident of the day as a fixture and proves the
guard stops it; negative controls prove the guard does not fire on the healthy case."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from bcc.features import studio_pc as sp
from bcc.guards import config_refs, content_policy, foreground, long_input, media_provenance, publish_gate
from bcc.providers import ChatResult

from .conftest import FakeAdapter
from .helpers import make_stack
from .test_v21_tool_loop import FINISHED, _run_task


# ---------------------------------------------------------------- G1 foreground guard

def test_g1_chat_window_is_refused_even_when_the_regex_would_match():
    """Incident: a paste meant for «Save as» went into a 2,237-member Telegram group."""
    r = foreground.check_title("SwapMe Dev Chat (2,237 members) – Telegram", r".*")
    assert r and foreground.GUARD_TAG in r and "мессенджер" in r
    for t in ("WhatsApp", "Discord | #general", "general (Channel) - Slack", "Microsoft Teams"):
        assert foreground.check_title(t), t


def test_g1_unexpected_window_refused_expected_window_allowed():
    assert foreground.check_title("Untitled - Notepad", r"Save As|Сохранить как")
    assert foreground.check_title("Save As", r"Save As|Сохранить как") is None
    assert foreground.check_title("", r".*"), "a window without a title is never typed into"
    assert foreground.check_title("Telegram", None, allow_chat=True) is None, "explicit chat target is allowed"
    assert "регулярное" in foreground.check_title("x", r"(")


def test_g1_studio_script_asserts_foreground_before_every_key_batch():
    script = sp.build_script("run_lua", hwnd=99, lua="print(1)")
    body = script.split("try {", 1)[1]
    # every SendWait is IMMEDIATELY preceded by an AssertFg on THIS hwnd with the Studio title regex
    chunks = body.split("[System.Windows.Forms.SendKeys]::SendWait(")[:-1]
    assert len(chunks) == 3
    for c in chunks:
        assert c.rstrip().endswith("AssertFg 99 'Roblox Studio|\\.rbxlx?\\b' $false;"), c[-120:]
    assert "telegram" in script.lower(), "the chat denylist is inside the PowerShell guard"
    for act in ("play", "stop"):
        s = sp.build_script(act, hwnd=7)
        assert s.count("AssertFg 7 ") == s.count("SendWait("), act
    s_copy = sp.build_script("copy_output", hwnd=7)
    assert s_copy.count("AssertFg 7 ") == s_copy.count("SendWait(") + 1, "copy_output asserts fg before each key and before clipboard read"


def test_g1_studio_input_raises_when_guard_fires(tmp_path):
    out = "FOREGROUND_GUARD: chat app in front 'Chat – Telegram' - nothing typed"
    s = sp.StudioPC(state_file=tmp_path / "o.json", ps=lambda script: out,
                    lister=lambda: [{"pid": 42, "hwnd": 99, "title": "a.rbxlx - Roblox Studio"}])
    s._set_owned(42)
    with pytest.raises(PermissionError, match="FOREGROUND_GUARD"):
        s.play()
    assert s.receipts[-1]["ok"] is False and "Telegram" in s.receipts[-1]["refused"]


@pytest.mark.skipif(not Path("C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe").exists(),
                    reason="Windows PowerShell only")
def test_g1_powershell_guard_parses_and_refuses_a_wrong_hwnd():
    """The real PowerShell guard (no keys sent): hwnd 1 is never the foreground window -> refusal text."""
    script = (sp._PS_INPUT + foreground.PS_GUARD + "\ntry { " + foreground.ps_assert(1, "Roblox Studio")
              + "; 'TYPED' } catch { Write-Output $_.Exception.Message }\n")
    r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       capture_output=True, text=True, timeout=60)
    assert "FOREGROUND_GUARD" in r.stdout and "TYPED" not in r.stdout, (r.stdout, r.stderr)


# ---------------------------------------------------------------- G2 empty-result recovery

def _big_code(n_lines: int = 1200) -> str:
    filler = [f"local filler_{i} = {i} -- padding padding padding padding" for i in range(n_lines)]
    filler[700:704] = ["function MatchService.reward(player)",
                       "    if winsToday < MatchConfig.MAX_PAID_WINS_PER_DAY then",
                       "        give(player, MatchConfig.DAILY_MATCH_POINT_CAP)",
                       "    end"]
    return "\n".join(filler)


def test_g2_excerpt_keeps_relevant_lines_and_fits():
    code = _big_code()
    assert len(code) > 40_000
    ex = long_input.relevant_excerpt(code, "почему падает награда MatchConfig MAX_PAID_WINS_PER_DAY reward")
    assert len(ex) <= long_input.MAX_EXCERPT_CHARS
    assert "702:     if winsToday < MatchConfig.MAX_PAID_WINS_PER_DAY then" in ex


def test_g2_reduce_messages_only_for_long_inputs():
    short = [{"role": "user", "content": "привет"}]
    assert long_input.reduce_messages(short) is None
    msgs = [{"role": "system", "content": "s"},
            {"role": "user", "content": "Найди баг в награде MatchConfig.MAX_PAID_WINS_PER_DAY:\n" + _big_code()}]
    small, info = long_input.reduce_messages(msgs)
    assert info["chars_before"] > 40_000 and info["chars_after"] <= 12_000
    assert small[1]["content"].endswith(long_input.STRICT_FORMAT)
    assert msgs[1]["content"].startswith("Найди баг"), "original messages untouched"
    assert long_input.is_empty_answer(ChatResult(text="  "))
    assert not long_input.is_empty_answer(ChatResult(text="ok"))
    assert long_input.enabled_for({"permissions": {"free_pool_first": True}})
    assert not long_input.enabled_for({"permissions": {"free_pool_first": True, "empty_recovery": False}})
    assert not long_input.enabled_for({"permissions": {}})


class _EmptyOnLong(FakeAdapter):
    """Jev's behaviour: empty answer when the input is long, a real answer on an excerpt."""
    def __init__(self):
        super().__init__()
        self.sizes: list[int] = []

    async def chat(self, model, messages, **kw):
        self.calls += 1
        size = sum(len(m.get("content") or "") for m in messages if isinstance(m.get("content"), str))
        self.sizes.append(size)
        return ChatResult(text="" if size > 20_000 else "MatchConfig.MAX_PAID_WINS_PER_DAY не определён",
                          tokens_in=5, tokens_out=5, model=model)


async def _g2_run(env, perms):
    import sqlalchemy as sa
    from bcc.db import agents as agents_t
    prompt = "Найди баг в награде MatchConfig.MAX_PAID_WINS_PER_DAY:\n" + _big_code()
    stack = await make_stack(env.client, max_steps=2, prompt=prompt)
    async with env.svc.db.session() as s:
        await s.execute(sa.update(agents_t).where(agents_t.c.id == stack["agent"]["id"]).values(permissions=perms))
        await s.commit()
    ad = _EmptyOnLong()
    env.svc.registry.adapter_factory = lambda m, p: ad
    status = await _run_task(env, stack["task"]["id"], timeout=15, until=FINISHED)
    task = (await env.client.get(f"/api/tasks/{stack['task']['id']}")).json()
    return status, task, ad


async def test_g2_engine_retries_empty_answer_on_excerpt(env):
    status, task, ad = await _g2_run(env, {"empty_recovery": True})
    assert status == "completed", (status, task)
    assert ad.sizes[0] > 40_000 and min(ad.sizes) <= 14_000, ad.sizes


async def test_g2_negative_control_without_opt_in_no_retry(env):
    status, task, ad = await _g2_run(env, {})
    assert status != "completed"
    assert all(s > 40_000 for s in ad.sizes), ad.sizes


# ---------------------------------------------------------------- G3 config refs

FIX_CONFIG = """--!strict
local MatchConfig = {}
MatchConfig.COUNTDOWN_SEC = 5
export type ModeSpec = { id: string }
local MODES = { snow = { id = "snow" } }
MatchConfig.MODES = MODES
function MatchConfig.get(id: string) return MODES[id] end
return MatchConfig
"""
FIX_SERVICE = """local Shared = game:GetService("ReplicatedStorage"):WaitForChild("Shared")
local MatchConfig = require(Shared:WaitForChild("MatchConfig"))
local x = MatchConfig.COUNTDOWN_SEC -- MatchConfig.IN_COMMENT is ignored
local s = "MatchConfig.IN_STRING is ignored"
local spec: MatchConfig.ModeSpec = MatchConfig.get("snow")
if winsToday < MatchConfig.MAX_PAID_WINS_PER_DAY then end
local room = MatchConfig.DAILY_MATCH_POINT_CAP - earned
local opt = MatchConfig.OPTIONAL_FIELD or 3
"""


def _luau_project(tmp_path: Path, config: str = FIX_CONFIG) -> Path:
    (tmp_path / "src" / "shared").mkdir(parents=True)
    (tmp_path / "src" / "server").mkdir(parents=True)
    (tmp_path / "src" / "shared" / "MatchConfig.luau").write_text(config, encoding="utf-8")
    (tmp_path / "src" / "server" / "MatchService.server.luau").write_text(FIX_SERVICE, encoding="utf-8")
    return tmp_path


def test_g3_missing_match_config_caps_are_reported(tmp_path):
    rep = config_refs.check(_luau_project(tmp_path))
    missing = {(m.field, m.line) for m in rep.missing}
    assert missing == {("MAX_PAID_WINS_PER_DAY", 6), ("DAILY_MATCH_POINT_CAP", 7)}, rep.as_dict()
    assert [g.field for g in rep.guarded] == ["OPTIONAL_FIELD"]
    assert not rep.ok and "G3 FAIL" in rep.summary_ru()


def test_g3_fixed_config_passes(tmp_path):
    fixed = FIX_CONFIG.replace("return MatchConfig", "MatchConfig.MAX_PAID_WINS_PER_DAY = 8\n"
                               "MatchConfig.DAILY_MATCH_POINT_CAP = 600\nreturn table.freeze(MatchConfig)")
    rep = config_refs.check(_luau_project(tmp_path, fixed))
    assert rep.ok, rep.summary_ru()


def test_g3_table_constructor_keys_and_metatable_skip(tmp_path):
    cfg = 'local Config\nConfig = {\n  Booth = { BaseSlots = 6 },\n  ["Rate"] = 2,\n  Speed = 1,\n}\nreturn Config\n'
    assert config_refs.luau_module_fields(cfg) == ({"Booth", "Rate", "Speed"}, False)
    dyn = "local M = setmetatable({}, {__index = function() return 0 end})\nreturn M\n"
    assert config_refs.luau_module_fields(dyn)[1] is True


def test_g3_two_requires_on_one_line_resolve_separately(tmp_path):
    """Live false positive found on swapme 03.11: `local A = require(..("A")); local B = require(..("B"))`
    was resolved as B for both."""
    (tmp_path / "A.luau").write_text("local A = {}\nA.TRACKS = {}\nreturn A\n", encoding="utf-8")
    (tmp_path / "B.luau").write_text("local B = {}\nB.x = 1\nreturn B\n", encoding="utf-8")
    (tmp_path / "s.server.luau").write_text(
        'local A = require(S:WaitForChild("A")); local B = require(S:WaitForChild("B"))\n'
        "print(A.TRACKS, B.x, B.TRACKS)\n", encoding="utf-8")
    rep = config_refs.check(tmp_path)
    assert [(m.module_var, m.field) for m in rep.missing] == [("B", "TRACKS")], rep.as_dict()


def test_g3_python_module_fields(tmp_path):
    (tmp_path / "settings.py").write_text("LIMIT = 3\ndef helper():\n    pass\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("import settings as cfg\nprint(cfg.LIMIT, cfg.helper, cfg.MISSING)\n",
                                     encoding="utf-8")
    rep = config_refs.check(tmp_path)
    assert [(m.file, m.field) for m in rep.missing] == [("app.py", "MISSING")]


# ---------------------------------------------------------------- G4 media provenance

def _fp(path: Path, env: list[float], dur: float, sha: str = "") -> media_provenance.Fingerprint:
    return media_provenance.Fingerprint(str(path), path.name, dur, sha or path.name, env)


def test_g4_renamed_reencoded_copy_is_flagged_and_never_uploadable(tmp_path):
    import math
    song = [abs(math.sin(i / 3.0)) * (0.5 + 0.5 * math.sin(i / 17.0)) for i in range(400)]
    registry = [_fp(Path("Artist - Real Song.mp3"), song, 200.0, "aaa").as_entry()]
    renamed = tmp_path / "Owner Track • Sparkle.mp3"
    renamed.write_bytes(b"x")
    quieter = [x * 0.6 for x in song]                        # re-encoded + volume change
    res = media_provenance.check([renamed], registry, fp_fn=lambda p: _fp(p, quieter, 200.4, "bbb"))
    assert not res["ok"] and res["flagged"][0]["renamed"] is True
    assert "ПЕРЕИМЕНОВАННАЯ" in media_provenance.summary_ru(res)
    with pytest.raises(media_provenance.FlaggedMedia):
        media_provenance.assert_uploadable([renamed], registry, fp_fn=lambda p: _fp(p, quieter, 200.4, "bbb"))


def test_g4_excerpt_and_exact_copy_and_negative_control(tmp_path):
    import math
    import random
    song = [abs(math.sin(i / 3.0)) * (0.5 + 0.5 * math.sin(i / 17.0)) for i in range(400)]
    entry = _fp(Path("Real Song.mp3"), song, 200.0, "sha-real").as_entry()
    clip = tmp_path / "clip.ogg"
    clip.write_bytes(b"x")
    assert media_provenance.compare(_fp(clip, song[100:160], 30.0, "other"), entry), "a 30 s cut is flagged"
    assert media_provenance.compare(_fp(clip, [], 1.0, "sha-real"), entry).reason.startswith("identical")
    rnd = random.Random(7)
    own = [rnd.random() for _ in range(400)]
    assert media_provenance.compare(_fp(clip, own, 200.0, "mine"), entry) is None, "own music passes"


def test_g4_envelope_from_pcm_is_volume_independent():
    import array
    loud = array.array("h", [1000, -1000] * 4000 + [8000, -8000] * 4000).tobytes()
    quiet = array.array("h", [500, -500] * 4000 + [4000, -4000] * 4000).tobytes()
    a = media_provenance.envelope_from_pcm(loud, window=4000)
    b = media_provenance.envelope_from_pcm(quiet, window=4000)
    assert a == pytest.approx(b) and max(a) == 1.0


@pytest.mark.skipif(not (media_provenance._tool("ffmpeg") and media_provenance._tool("ffprobe")),
                    reason="ffmpeg not installed")
def test_g4_real_ffmpeg_renamed_reencoded_copy(tmp_path):
    """Real decode path: a generated tune, its MP3 re-encode under a fictional title, and a different tune."""
    ff = media_provenance._tool("ffmpeg")
    src = tmp_path / "third_party" / "Real Artist - Song.wav"
    src.parent.mkdir()
    expr = "sin(2*PI*440*t)*(0.3+0.7*abs(sin(2*PI*0.7*t)))*lt(mod(t\\,3)\\,2)"   # commas escaped for lavfi
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", f"aevalsrc={expr}:s=22050:d=20", str(src)], check=True)
    fake = tmp_path / "upload" / "Owner Track • Gemini 2.mp3"
    fake.parent.mkdir()
    subprocess.run([ff, "-v", "error", "-i", str(src), "-filter:a", "volume=0.5", "-b:a", "96k", str(fake)],
                   check=True)
    other = tmp_path / "upload" / "own.wav"
    subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "aevalsrc=sin(2*PI*220*t)*abs(sin(2*PI*0.13*t)):s=22050:d=20",
                    str(other)], check=True)
    reg = tmp_path / "registry.json"
    assert media_provenance.build_registry(src.parent, reg) == 1
    res = media_provenance.check([fake.parent], media_provenance.load_registry(reg))
    assert res["checked"] == 2 and len(res["flagged"]) == 1, res
    assert res["flagged"][0]["file"].endswith("Gemini 2.mp3") and res["flagged"][0]["renamed"]


# ---------------------------------------------------------------- G5 content policy

DIFF = """diff --git a/src/server/Carnival.server.luau b/src/server/Carnival.server.luau
--- a/src/server/Carnival.server.luau
+++ b/src/server/Carnival.server.luau
@@ -10,0 +11,4 @@
+local veloModel = Instance.new("Model") -- VELO PEPPERMINT STORM can
+local pilot = makeNpc("Putin caricature")
+local prize = "Fox Points"
+-- a comment about a casino theme
"""


def test_g5_velo_and_politician_in_diff_fail():
    lines = content_policy.added_lines(DIFF)
    assert [ln for _, ln, _ in lines] == [11, 12, 13, 14]
    res = asyncio.run(content_policy.check(lines))
    cats = {(h.category, h.in_comment) for h in res.hits}
    assert ("tobacco_nicotine", False) in cats and ("politician", False) in cats and ("gambling", True) in cats
    assert ("real_person_likeness", False) in cats
    assert not res.ok and "G5 FAIL" in res.summary_ru()


def test_g5_negative_controls_game_words_pass():
    clean = ["local velocity = 5", "progress bar", "coin pouch", "supply airdrop", "local betterSpeed = 2",
             "трамплин на смотровую", "KartBoostPad", "alphabet soup", "Fox Points x2"]
    res = asyncio.run(content_policy.check([("a.luau", i, t) for i, t in enumerate(clean, 1)]))
    assert res.ok and not res.hits, res.summary_ru()


def test_g5_negated_mentions_are_warnings_but_brands_next_to_them_still_fail():
    """Live false positives 03.11 (asset manifests): «no currency/crypto symbols», `"crypto_logos_present": false`."""
    lines = [("m.json", 1, '"notes": "transparent background, no currency/crypto symbols"'),
             ("m.json", 2, '"crypto_logos_present": false'),
             ("a.luau", 3, 'local t = "без алкоголя и казино"'),
             ("a.luau", 4, 'local can = "VELO nicotine-free pouch"'),
             ("a.luau", 5, 'local s = "Это не просто игра, это казино"')]
    hits = content_policy.scan_lines(lines)
    by_line = {h.line: h for h in hits if h.category in ("crypto", "gambling", "alcohol", "tobacco_nicotine")}
    assert by_line[1].negated and by_line[2].negated and by_line[3].negated
    assert not by_line[4].negated and by_line[4].term == "VELO"
    assert not by_line[5].negated, "«не» far from the term does not excuse it"
    res = content_policy.PolicyResult(hits=hits)
    assert {h.line for h in res.blocking} == {4, 5}


def test_g5_comment_only_hit_is_a_warning_and_reviewer_flag_fails():
    lines = [("a.luau", 1, "local x = 1 -- like Minecraft")]
    res = asyncio.run(content_policy.check(lines))
    assert res.ok and res.hits and res.hits[0].in_comment

    async def flagging(prompt: str) -> str:
        assert "x = 1" in prompt
        return "<think>hmm</think>\nFLAG: real_brand — Minecraft"
    res2 = asyncio.run(content_policy.check(lines, reviewer=flagging))
    assert res2.reviewer == "FLAG" and not res2.ok

    async def broken(prompt: str) -> str:
        raise TimeoutError("model down")
    res3 = asyncio.run(content_policy.check([("a.luau", 1, "local VeloCan = 1")], reviewer=broken))
    assert res3.reviewer == "NOT_RUN" and not res3.ok, "a reviewer failure never hides a denylist hit"


# ---------------------------------------------------------------- G6 publish gate

def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, config: str = FIX_CONFIG) -> Path:
    root = _luau_project(tmp_path, config)
    (root / "tools").mkdir()
    (root / "tools" / "check.sh").write_text("echo building\necho 'GATE PASS'\n", encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "src", "tools")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return root


def test_g6_refuses_dirty_tree_and_missing_config_and_reports_ru(tmp_path):
    root = _repo(tmp_path)
    (root / "untracked.txt").write_text("x", encoding="utf-8")
    res = asyncio.run(publish_gate.evaluate(root, check_cmd=["bash", "tools/check.sh"], registry=[]))
    by = {s.name: s.ok for s in res.steps}
    assert by["Рабочее дерево закоммичено"] is False
    assert by["Сборочная проверка"] is True
    assert by["G3 ссылки на поля конфигов"] is False
    assert not res.ok and res.report_ru().startswith("Проверка публикации: ОТКАЗ")
    with pytest.raises(publish_gate.PublishRefused):
        asyncio.run(publish_gate.assert_publishable(root, check_cmd=["bash", "tools/check.sh"], registry=[]))


def test_g6_passes_on_clean_fixed_project_and_fails_without_gate_marker(tmp_path):
    fixed = FIX_CONFIG.replace("return MatchConfig", "MatchConfig.MAX_PAID_WINS_PER_DAY = 8\n"
                               "MatchConfig.DAILY_MATCH_POINT_CAP = 600\nMatchConfig.OPTIONAL_FIELD = 1\n"
                               "return MatchConfig")
    root = _repo(tmp_path, fixed)
    res = asyncio.run(publish_gate.evaluate(root, check_cmd=["bash", "tools/check.sh"], registry=[]))
    assert res.ok, res.report_ru()
    assert res.can_publish is False, "static 5/5 check alone must never authorize publish"
    assert "ПРОЙДЕНА СТАТИЧЕСКАЯ ЧАСТЬ" in res.report_ru()
    assert "можно публиковать" not in res.report_ru()
    bad = asyncio.run(publish_gate.evaluate(root, check_cmd=["bash", "-c", "echo GATE FAIL"], registry=[]))
    assert not bad.ok and "НЕТ" in next(s.detail for s in bad.steps if s.name == "Сборочная проверка")
    skip = asyncio.run(publish_gate.evaluate(root, skip_build=True, registry=[]))
    assert not skip.ok


def test_g3_dynamic_bracket_access_boundary_vs_static_dot_access(tmp_path):
    """Applicability boundary: G3 checks static `Module.FIELD` access and does not crash on dynamic `Module[key]`."""
    (tmp_path / "Cfg.luau").write_text("local Cfg = { A = 1 }\nreturn Cfg\n", encoding="utf-8")
    (tmp_path / "Use.server.luau").write_text(
        'local Cfg = require(script.Parent:WaitForChild("Cfg"))\n'
        "local k = 'DYNAMIC'\n"
        "print(Cfg[k], Cfg.A, Cfg.STATIC_MISSING)\n",
        encoding="utf-8",
    )
    rep = config_refs.check(tmp_path)
    assert [(m.module_var, m.field) for m in rep.missing] == [("Cfg", "STATIC_MISSING")]


def test_g5_paid_random_items_requires_policy_service_and_odds_disclosure():
    """Roblox Paid Random Items policy: Robux/paid-currency random chest requires ArePaidRandomItemsRestricted + odds."""
    bad_lines = [
        ("Shop.luau", 10, 'MarketplaceService:PromptProductPurchase(player, 12345)'),
        ("Shop.luau", 11, 'local reward = rollRandomChest("paid_swaps_chest")'),
    ]
    res_bad = asyncio.run(content_policy.check(bad_lines))
    assert not res_bad.ok
    assert any(h.category == "paid_random_items" for h in res_bad.blocking), res_bad.as_dict()
    assert res_bad.as_dict()["reviewer_advisory_only"] is True

    good_lines = bad_lines + [
        ("Shop.luau", 12, 'local policy = PolicyService:GetPolicyInfoForPlayerAsync(player).ArePaidRandomItemsRestricted'),
        ("Shop.luau", 13, 'local oddsText = "Шанс выпадения (probabilities): Common 70%, Rare 30%"'),
    ]
    res_good = asyncio.run(content_policy.check(good_lines))
    assert res_good.ok and not any(h.category == "paid_random_items" for h in res_good.hits), res_good.as_dict()


def _valid_studio_receipt(tmp_path: Path, sha: str) -> Path:
    import struct
    import zlib
    # Build a valid 64x64 RGB PNG (>100 bytes, not 1x1)
    w, h = 64, 64
    raw = b"".join(b"\x00" + (b"\x10\x20\x30" * w) for _ in range(h))
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    scr = tmp_path / "studio_live.png"
    scr.write_bytes(png)
    rec = tmp_path / "studio_receipt.json"
    rec.write_text(json.dumps({
        "verdict": "PASS",
        "simulated": False,
        "pid": 42424,
        "build_sha": sha,
        "screenshot": str(scr),
        "steps": [
            {"step": "play", "ok": True},
            {"step": "screenshot", "ok": True},
            {"step": "stop", "ok": True},
        ],
    }), encoding="utf-8")
    return rec


def test_g6_assert_publishable_refuses_every_unmet_condition_and_passes_only_when_all_10_hold(tmp_path):
    fixed = FIX_CONFIG.replace("return MatchConfig", "MatchConfig.MAX_PAID_WINS_PER_DAY = 8\n"
                               "MatchConfig.DAILY_MATCH_POINT_CAP = 600\nMatchConfig.OPTIONAL_FIELD = 1\n"
                               "return MatchConfig")
    root = _repo(tmp_path / "game", fixed)
    prev_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    (root / "tools" / "test.sh").write_text("echo '12 passed'\n", encoding="utf-8")
    _git(root, "add", "tools/test.sh")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add tests")
    head_sha = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    studio_rec = _valid_studio_receipt(tmp_path, head_sha)

    base_kwargs = dict(
        root=root,
        check_cmd=["bash", "tools/check.sh"],
        test_cmd=["bash", "tools/test.sh"],
        registry=[],
        owner_approved=True,
        expected_sha=head_sha,
        licenses_verified=True,
        studio_receipt=studio_rec,
        rollback_ref=prev_sha,
        stop_active=False,
    )

    # 1. All 10 conditions satisfied -> can_publish is True and report says "ПРОЙДЕНА — можно публиковать"
    ok_res = asyncio.run(publish_gate.assert_publishable(**base_kwargs))
    assert ok_res.ok and ok_res.can_publish
    assert "ПРОЙДЕНА — можно публиковать" in ok_res.report_ru()

    # 2. Negative checks: each single unmet release condition must raise PublishRefused
    negative_mutations = [
        ({"stop_active": True}, "Проверка STOP"),
        ({"owner_approved": False}, "Подтверждение владельца"),
        ({"expected_sha": "0000000000000000000000000000000000000000"}, "Рабочее дерево закоммичено"),
        ({"test_cmd": None}, "Автотесты проекта"),
        ({"test_cmd": ["bash", "-c", "exit 1"]}, "Автотесты проекта"),
        ({"studio_receipt": None}, "Живой Studio playtest"),
        ({"rollback_ref": None}, "Точка отката"),
        ({"rollback_ref": head_sha}, "Точка отката"),
    ]
    for patch_kw, failed_step in negative_mutations:
        kw = {**base_kwargs, **patch_kw}
        with pytest.raises(publish_gate.PublishRefused) as exc_info:
            asyncio.run(publish_gate.assert_publishable(**kw))
        by_name = {s.name: s.ok for s in exc_info.value.result.steps}
        assert by_name[failed_step] is False, (patch_kw, by_name)
        assert exc_info.value.result.can_publish is False
        assert "можно публиковать" not in exc_info.value.result.report_ru()

    # 3. Simulated Studio receipt or 1x1 stub screenshot -> refused
    sim_rec = tmp_path / "sim_receipt.json"
    sim_rec.write_text(json.dumps({
        "verdict": "PASS",
        "simulated": True,
        "build_sha": head_sha,
        "screenshot": str(tmp_path / "studio_live.png"),
        "steps": [{"step": "play", "ok": True}, {"step": "screenshot", "ok": True}, {"step": "stop", "ok": True}],
    }), encoding="utf-8")
    with pytest.raises(publish_gate.PublishRefused) as sim_exc:
        asyncio.run(publish_gate.assert_publishable(**{**base_kwargs, "studio_receipt": sim_rec}))
    assert dict((s.name, s.ok) for s in sim_exc.value.result.steps)["Живой Studio playtest"] is False



def test_g6_media_without_registry_is_refused_and_content_hit_in_diff(tmp_path):
    root = _repo(tmp_path)
    base = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (root / "assets").mkdir()
    (root / "assets" / "track.mp3").write_bytes(b"ID3")
    (root / "src" / "server" / "Shop.luau").write_text('local name = "VELO Freeze"\n', encoding="utf-8")
    _git(root, "add", "assets", "src")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "add")
    res = asyncio.run(publish_gate.evaluate(root, check_cmd=["bash", "tools/check.sh"], base=base))
    by = {s.name: (s.ok, s.detail) for s in res.steps}
    assert by["G4 происхождение медиа"][0] is False and "реестр" in by["G4 происхождение медиа"][1]
    assert by["G5 политика контента"][0] is False and "VELO" in by["G5 политика контента"][1]


# ---------------------------------------------------------------- wiring

def test_guard_tools_registered_with_safe_effects(tmp_path, monkeypatch):
    from bcc.features import harness_guards as hg
    from bcc.tools import REGISTRY
    asyncio.run(hg.setup(None))
    specs = {n: REGISTRY.get(n) for n in hg.TOOL_NAMES}
    assert all(specs.values())
    assert specs["guard.publish_gate"].permission == "terminal.run" and specs["guard.publish_gate"].default_effect == "ask"
    for n in ("guard.config_refs", "guard.media_provenance", "guard.content_policy"):
        assert specs[n].category == "read" and specs[n].default_effect == "auto"
    assert hg.profile_tools(["code.read", "guard.config_refs"]) == ["code.read", "guard.config_refs",
                                                                     "guard.media_provenance", "guard.content_policy",
                                                                     "guard.publish_gate"]
    monkeypatch.setenv("BCC_GUARD_ROOTS", str(tmp_path / "work"))
    (tmp_path / "work" / "proj").mkdir(parents=True)
    assert hg.resolve_project(str(tmp_path / "work" / "proj")) == (tmp_path / "work" / "proj").resolve()
    with pytest.raises(PermissionError):
        hg.resolve_project(str(tmp_path))


def test_config_refs_tool_end_to_end(tmp_path, monkeypatch):
    from bcc.features import harness_guards as hg
    from bcc.tools import ToolContext
    monkeypatch.setenv("BCC_GUARD_ROOTS", str(tmp_path))
    root = _luau_project(tmp_path / "game")
    ctx = ToolContext(svc=None, task={}, run_id=0, agent={})
    res = asyncio.run(hg._config_refs({"path": str(root)}, ctx))
    assert "MAX_PAID_WINS_PER_DAY" in res.content and res.data["ok"] is False
    json.dumps(res.data, ensure_ascii=False)


def test_harness_profile_patch_unblocks_local_agent_tools():
    """Audit 05.11: Agent 80 had cloud_pc_access=False in permissions, causing chat_only() to strip all tools even on
    local model 102. harness_profile_patch removes cloud_pc_access=False while keeping cloud_pc_access != True."""
    from bcc.features import harness_guards as hg
    from bcc.tools import allowed_tools_for, chat_only
    agent80 = {
        "id": 80,
        "model_id": 102,
        "tools": ["code.read", "code.write"],
        "permissions": {"cloud_pc_access": False, "free_pool": [55, 71, 82], "free_pool_first": True},
    }
    assert chat_only(agent80) and allowed_tools_for({}, agent80) == []
    patched = {**agent80, **hg.harness_profile_patch(agent80)}
    assert not chat_only(patched)
    assert patched["permissions"].get("cloud_pc_access") is not True
    allowed = allowed_tools_for({}, patched)
    for t in ("code.read", "guard.config_refs", "guard.publish_gate", "pc.studio"):
        assert t in allowed, allowed


def test_parse_text_tool_calls_only_accepts_granted_tools():
    from bcc.engine import parse_text_tool_calls
    from bcc.features import harness_guards as hg
    from bcc.tools import REGISTRY
    asyncio.run(hg.setup(None))
    specs = [REGISTRY.get("guard.config_refs")]
    text = (
        'Проверю конфиги:\n'
        '<tool_call>{"name": "guard.config_refs", "arguments": {"path": "C:/Bossman/proj"}}</tool_call>\n'
        '<tool_call>{"name": "terminal.run", "arguments": {"command": "whoami"}}</tool_call>'
    )
    calls = parse_text_tool_calls(text, specs, step=2)
    assert len(calls) == 1
    assert calls[0].name == specs[0].api_name
    assert calls[0].arguments == {"path": "C:/Bossman/proj"}

