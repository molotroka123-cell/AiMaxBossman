"""@-references to attached photos (Higgsfield-style): parsing, rewriting to the model's own wording,
refusals, and the multi-reference sd-cli command. FAKE engine only, neutral prompts."""
from __future__ import annotations

import base64

import pytest

from bcc.direct_gen import ref_tags, sdcli

from .test_direct_gen_photo import PNG, FakeSd, create, finish, install, make_media

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


def test_tags_found_in_order_and_emails_or_escapes_are_not_tags():
    assert ref_tags.tags_in("лицо с @1 и одежду с @2, фон с @3") == ["1", "2", "3"]
    assert ref_tags.tags_in("mail me at a@b.com, price @@5") == []
    assert ref_tags.tags_in("(@лицо) и @1-й кадр") == ["лицо", "1"]


def test_rewrite_numbers_and_names_to_the_model_wording():
    refs = ref_tags.RefTags(3, ("лицо", "", "Фон"))
    out = ref_tags.rewrite("возьми лицо с @1 и одежду с @2, фон с @фон; @лицо улыбается; знак @@", refs, "Picture {n}")
    assert out == "возьми лицо с Picture 1 и одежду с Picture 2, фон с Picture 3; Picture 1 улыбается; знак @"
    assert ref_tags.rewrite("face of @2", ref_tags.RefTags(2), "<image{n}>") == "face of <image2>"
    assert ref_tags.rewrite("no tags at all", ref_tags.RefTags(0), "Picture {n}") == "no tags at all"


def test_unknown_tags_are_refused_in_russian():
    with pytest.raises(ref_tags.RefTagError, match=r"нет такой отсылки: @4.*@1, @2"):
        ref_tags.rewrite("@1 и @4", ref_tags.RefTags(2), "Picture {n}")
    with pytest.raises(ref_tags.RefTagError, match="@лицо"):
        ref_tags.rewrite("@лицо", ref_tags.RefTags(1), "Picture {n}")
    with pytest.raises(ref_tags.RefTagError, match="повторяется"):
        ref_tags.clean_names(["лицо", "ЛИЦО"], 2)
    with pytest.raises(ref_tags.RefTagError):
        ref_tags.clean_names(["12"], 1)                               # digits are positions, not names


def test_registry_says_which_models_take_several_references():
    assert sdcli.ref_capacity(sdcli.BY_ID["qwen-image-edit-2509"]) == 3
    assert sdcli.ref_capacity(sdcli.BY_ID["qwen-image-2.1-uc-edit"]) == 3
    assert sdcli.ref_capacity(sdcli.BY_ID["wan2.2-ti2v-5b"]) == 1      # one start frame
    assert sdcli.ref_capacity(sdcli.BY_ID["z-image-turbo"]) == 0
    assert sdcli.ref_label(sdcli.BY_ID["qwen-image-edit-2509"]) == "Picture {n}"
    assert sdcli.ref_label(sdcli.BY_ID["qwen-image-2.1-uc-edit"]) == "<image{n}>"
    assert sdcli.ref_label(sdcli.BY_ID["wan2.2-ti2v-5b"]) == "the start image"


def test_build_argv_repeats_r_in_the_order_of_the_tags(tmp_path):
    media, binary = make_media(tmp_path)
    spec = sdcli.BY_ID["qwen-image-edit-2509"]
    refs = [tmp_path / f"r{i}.png" for i in (1, 2, 3)]
    params = {"width": 512, "height": 512, "steps": 2, "seed": 1}
    argv = sdcli.build_argv(spec, binary, media, params, "Picture 1", "", tmp_path / "o.png", refs)
    assert [argv[i + 1] for i, a in enumerate(argv) if a == "-r"] == [str(r) for r in refs]
    assert argv.count("--llm_vision") == 1
    with pytest.raises(ValueError):
        sdcli.build_argv(spec, binary, media, params, "x", "", tmp_path / "o.png", refs + [tmp_path / "r4.png"])


async def test_multi_reference_job_rewrites_tags_and_passes_every_image(env, tmp_path):
    install(env, tmp_path, FakeSd)
    imgs = [base64.b64encode(PNG).decode(), base64.b64encode(JPEG).decode(), base64.b64encode(PNG).decode()]
    prompt = "возьми лицо с @1 и одежду с @одежда, фон с @3"
    r = await create(env, model="qwen-image-edit-2509", prompt=prompt, images_b64=imgs, ref_names=["", "одежда", ""])
    assert r.status_code == 202, r.text
    job = await finish(env, r.json()["job_id"])
    assert job["status"] == "completed"
    assert job["raw_prompt"] == prompt                                   # the owner's text is kept as typed
    assert job["effective_prompt"] == "возьми лицо с Picture 1 и одежду с Picture 2, фон с Picture 3"
    assert [x["as"] for x in job["refs"]] == ["Picture 1", "Picture 2", "Picture 3"]
    assert job["refs"][1]["name"] == "одежда" and all(x["used"] for x in job["refs"])
    argv = FakeSd.instances[-1].argv
    rs = [argv[i + 1] for i, a in enumerate(argv) if a == "-r"]
    assert len(rs) == 3 and rs[0].endswith("input.png") and rs[1].endswith("input_2.jpg") and rs[2].endswith("input_3.png")
    assert argv[argv.index("-p") + 1] == job["effective_prompt"]

    again = await env.client.post(f"/api/direct-gen/jobs/{job['job_id']}/retry", headers={"X-Participant": "owner"})
    redo = await finish(env, again.json()["job_id"])
    assert redo["effective_prompt"] == job["effective_prompt"] and len(redo["refs"]) == 3


async def test_qwen21_edit_uses_its_own_image_labels(env, tmp_path):
    install(env, tmp_path, FakeSd)
    r = await create(env, model="qwen-image-2.1-uc-edit", prompt="face from @1, dress from @2",
                     images_b64=[base64.b64encode(PNG).decode()] * 2)
    job = await finish(env, r.json()["job_id"])
    assert job["effective_prompt"] == "face from <image1>, dress from <image2>"


async def test_refusals_unknown_tag_too_many_images_and_single_ref_models(env, tmp_path):
    install(env, tmp_path, FakeSd)
    one = base64.b64encode(PNG).decode()
    r = await create(env, model="qwen-image-edit-2509", prompt="лицо с @2", images_b64=[one])
    assert r.status_code == 422 and r.json()["error"]["code"] == "unknown_ref_tag"
    assert "@2" in r.json()["error"]["message"]
    r = await create(env, model="qwen-image-edit-2509", prompt="x", images_b64=[one] * 4)
    assert r.json()["error"]["code"] == "too_many_images"
    r = await create(env, model="wan2.2-ti2v-5b", prompt="x", resolution="480x272", duration=1, images_b64=[one] * 2)
    assert r.json()["error"]["code"] == "too_many_images"
    r = await create(env, model="qwen-image-edit-2509", prompt="x", image_b64=one, images_b64=[one])
    assert r.json()["error"]["code"] == "image_invalid"
    assert not FakeSd.instances                                          # nothing reached the engine


async def test_text_only_models_keep_at_signs_untouched(env, tmp_path):
    install(env, tmp_path, FakeSd)
    r = await create(env, model="z-image-turbo", prompt="a sign that says @bossman")
    job = await finish(env, r.json()["job_id"])
    assert job["effective_prompt"] == "a sign that says @bossman" and job["refs"] == []


async def test_single_image_legacy_field_still_works_with_at1(env, tmp_path):
    install(env, tmp_path, FakeSd)
    r = await create(env, model="qwen-image-edit-2509", prompt="make @1 smile", image_b64=base64.b64encode(PNG).decode())
    job = await finish(env, r.json()["job_id"])
    assert job["effective_prompt"] == "make Picture 1 smile"
    argv = FakeSd.instances[-1].argv
    assert argv.count("-r") == 1
