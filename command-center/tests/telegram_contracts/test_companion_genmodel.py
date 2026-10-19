"""MC22-16: unified /genmodel selector across the 4 generation roles (prompt, image, edit, video).

Verifies:
- Owner-only access; guest is rejected.
- 4 roles displayed with current model, readiness, and switch buttons.
- Preference persists across restart in the encrypted Store.
- /imgmodel and /edit_engine stay compatible with /genmodel image and /genmodel edit.
- Snapshot isolation: changing model while a job is queued leaves the already-queued job on its
  snapshotted model while the new job uses the newly selected model.
- Honest reporting when an uncensored video profile is not ready in Studio API.
"""
from __future__ import annotations

import asyncio

import httpx

from bcc.telegram_companion.adapters import Core, Models, Telegram
from bcc.telegram_companion.service import IMAGE_MODELS, VIDEO_MODEL, Companion, Reply
from bcc.telegram_companion.store import Store

from test_companion_gen_refine import (
    EDIT_PROMPT,
    GUEST,
    IMAGE_PROMPT,
    OWNER,
    PNG,
    EditStudio,
    FakeOllama,
    Harness,
    MultiStudio,
    PhotoTelegram,
    assert_launched,
    cfg,
    go,
    labels,
)


def test_genmodel_owner_only_and_shows_four_roles(tmp_path):
    async def scenario():
        async with Harness(tmp_path, studio=EditStudio(file_bytes=PNG)) as h:
            # Guest is denied
            guest_out = await h.say("/genmodel", person=GUEST)
            assert "только для владельца" in guest_out

            # Owner sees all 4 roles
            overview = await h.say("/genmodel")
            assert isinstance(overview, Reply)
            for section in ("Промпт", "Фото", "Редактирование", "Видео"):
                assert section in overview
            assert "uncensored" in overview
            btn_labels = labels(overview)
            assert "🧠 Промпт" in btn_labels
            assert "🎨 Фото" in btn_labels
            assert "🖌 Редактирование" in btn_labels
            assert "🎬 Видео" in btn_labels
            assert Store.lane({"text": "/genmodel"}) == "control"
            assert Store.lane({"text": "/genmodel image sdcpp:flux1-schnell"}) == "control"
    go(scenario())


def test_genmodel_persists_across_restart_and_syncs_with_imgmodel_and_edit_engine(tmp_path):
    async def scenario():
        async with Harness(tmp_path, studio=EditStudio(file_bytes=PNG)) as h:
            # 1. Switch prompt model
            r_prompt = await h.say("/genmodel prompt bossman-community-qwen-uncensored:latest")
            assert isinstance(r_prompt, Reply)
            assert "bossman-community-qwen-uncensored:latest" in r_prompt

            # 2. Switch image model via /genmodel image -> reflected in /imgmodel
            r_img = await h.say("/genmodel image sdcpp:flux1-schnell")
            assert isinstance(r_img, Reply)
            assert "FLUX.1-schnell" in r_img
            assert h.app.image_model_for(OWNER) == "sdcpp:flux1-schnell"
            img_menu = await h.say("/imgmodel")
            assert "FLUX.1-schnell" in img_menu

            # 3. Switch edit model via /genmodel edit -> reflected in /edit_engine
            r_edit = await h.say("/genmodel edit uncensored")
            assert isinstance(r_edit, Reply)
            assert "z_image_nsfw_v2" in r_edit
            assert h.app.edit_engine_for(OWNER) == "uncensored"

            # 4. Video role honestly reports uncensored not ready in Studio when asked for a14b
            r_vid = await h.say("/genmodel video wan-a14b")
            assert isinstance(r_vid, Reply)
            assert "Uncensored для этой роли не готов" in r_vid
            assert VIDEO_MODEL in r_vid

        # Restart companion over the same Store directory
        async with Harness(tmp_path, studio=EditStudio(file_bytes=PNG)) as h2:
            assert h2.app.prompt_model_for(OWNER) == "bossman-community-qwen-uncensored:latest"
            assert h2.app.image_model_for(OWNER) == "sdcpp:flux1-schnell"
            assert h2.app.edit_engine_for(OWNER) == "uncensored"
            assert h2.app.video_model_for(OWNER) == VIDEO_MODEL
    go(scenario())


def test_genmodel_snapshot_isolation_between_queued_and_new_jobs(tmp_path):
    """MC22-16 acceptance: owner selects model -> job 1 is queued with snapshot -> setting changes ->
    job 1 still runs with the old model, job 2 runs with the new model."""
    async def scenario():
        ollama = FakeOllama(["PROMPT: " + IMAGE_PROMPT, "PROMPT: " + IMAGE_PROMPT])
        async with Harness(tmp_path, ollama=ollama, studio=MultiStudio(file_bytes=PNG)) as h:
            # Hold the generation slot so job 1 stays queued while we switch models
            h.app.image_job = {"id": 999, "cancel": False, "who": "busy"}

            await h.say("/genmodel image sdcpp:z-image-turbo")
            r1 = await h.say("/pic первый кадр на старой модели")
            assert_launched(r1, position=2)
            assert "Z-Image-Turbo" in r1

            # Change the setting while job 1 is waiting in the queue
            await h.say("/genmodel image sdcpp:flux1-schnell")
            await h.say("/genmodel prompt bossman-community-qwen-uncensored:latest")

            r2 = await h.say("/pic второй кадр на новой модели")
            assert_launched(r2, position=3)
            assert "FLUX.1-schnell" in r2
            assert h.ollama.chats[1]["model"] == "bossman-community-qwen-uncensored:latest"

            # Release the slot and let both queued items finish
            h.app.image_job = None
            await h.done()

            assert len(h.studio.created) == 2
            assert h.studio.created[0]["model"] == "sdcpp:z-image-turbo"
            assert h.studio.created[1]["model"] == "sdcpp:flux1-schnell"
    go(scenario())
