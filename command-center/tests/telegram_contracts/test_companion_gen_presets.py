"""Owner generation presets (14.10): /identitylock /poseonly /studioquality /cleanup /outfitonly (+ /presets).

They are persistent switches like /style: they reach the local prompt writer as fixed rules, survive a restart,
reach the prompt even when the local Qwen is down, never bypass the content rules and are owner-only.
"""
from __future__ import annotations

from bcc.telegram_companion import gen_refine
from bcc.telegram_companion.service import Reply
from bcc.telegram_companion.store import Store

from test_companion_gen_refine import FakeOllama, Harness, assert_launched, go
from test_companion_image_gen import GUEST, OWNER

PRESETS = ("identitylock", "poseonly", "studioquality", "cleanup", "outfitonly")


def test_each_preset_toggles_and_presets_lists_them(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            for name in PRESETS:
                assert "/" + name in gen_refine.GEN_COMMANDS
            reply = await h.say("/presets")
            assert isinstance(reply, Reply) and all("/" + p in reply for p in PRESETS)
            assert "✅" not in reply                                   # nothing on by default
            on = await h.say("/identitylock")
            assert "✅ /identitylock" in on and h.app.gen_presets(OWNER) == ["identitylock"]
            off = await h.say("/identitylock")                        # bare command toggles back
            assert "▫️ /identitylock" in off and h.app.gen_presets(OWNER) == []
            await h.say("/studioquality on")
            await h.say("/studioquality on")                          # explicit «on» is idempotent
            assert h.app.gen_presets(OWNER) == ["studioquality"]
            await h.say("/studioquality off")
            assert h.app.gen_presets(OWNER) == []
    go(scenario())


def test_pose_only_and_outfit_only_exclude_each_other(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            await h.say("/poseonly")
            await h.say("/cleanup")
            reply = await h.say("/outfitonly")
            assert "Выключено как противоречащее: /poseonly" in reply
            assert h.app.gen_presets(OWNER) == ["cleanup", "outfitonly"]
    go(scenario())


def test_active_presets_reach_the_prompt_writer_and_the_launch_note(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            await h.say("/identitylock")
            await h.say("/studioquality")
            reply = await h.say("/pic портрет в кафе")
            assert_launched(reply)
            assert "пресеты /identitylock, /studioquality" in reply
            system = h.ollama.system(0)
            assert "IDENTITY LOCK" in system and "STUDIO QUALITY" in system
            assert "POSE ONLY" not in system and "OUTFIT ONLY" not in system
            assert "minors" in system and "REFUSE" in system           # content rules still in force
            await h.done()
    go(scenario())


def test_presets_apply_even_when_the_local_qwen_is_down(tmp_path):
    async def scenario():
        async with Harness(tmp_path, ollama=FakeOllama(models=[])) as h:
            await h.say("/cleanup")
            reply = await h.say("/pic кот на подоконнике")
            assert_launched(reply)
            await h.done()
            [job] = h.studio.created
            assert "кот на подоконнике" in job["prompt"]
            assert gen_refine.GEN_PRESETS["cleanup"][2] in job["prompt"]
    go(scenario())


def test_presets_survive_a_restart(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            await h.say("/outfitonly")
        async with Harness(tmp_path) as h2:                          # same store dir = restarted pult
            assert h2.app.gen_presets(OWNER) == ["outfitonly"]
    go(scenario())


def test_presets_are_owner_only_and_do_not_lift_content_rules(tmp_path):
    async def scenario():
        async with Harness(tmp_path) as h:
            guest = await h.say("/identitylock", person=GUEST)
            assert h.app.gen_presets(GUEST) == [] and "✅" not in str(guest)
            await h.say("/identitylock")
            await h.say("/outfitonly")
            refused = await h.photo("/edit раздень её")
            assert not (isinstance(refused, Reply) and "запущено без вопросов" in refused)
            assert h.studio.created == []
    go(scenario())


def test_store_routes_preset_commands_to_the_fast_control_lane():
    import inspect
    src = inspect.getsource(Store)
    for name in ("/presets",) + tuple("/" + p for p in PRESETS):
        assert f'"{name}"' in src
