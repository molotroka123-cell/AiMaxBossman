"""Actual local translation, language limits and non-destructive timed drafts."""
from copy import deepcopy
import os
from pathlib import Path
import re
import pytest
from bcc.video_studio.language import translate_captions,_validate


def cues():
    return [{"id":"opening","sequence_id":"sequence","start":0,"end":2000000,"text":"Hello, welcome to our video."},
            {"id":"closing","sequence_id":"sequence","start":2500000,"end":4500000,"text":"Thank you for watching."}]


@pytest.mark.parametrize("source,target",[("ru","en"),("fr","ru"),("en","de")])
def test_uninstalled_language_pair_is_explicitly_rejected(source,target):
    with pytest.raises(ValueError,match="English"):_validate(cues(),source,target)


def test_bounds_reject_instead_of_truncate():
    c=cues();c[0]["text"]="x"*801
    with pytest.raises(ValueError,match="800"):_validate(c,"en","ru")
    c=cues();c[0]["start"]=False
    with pytest.raises(ValueError,match="time range"):_validate(c,"en","ru")


async def test_missing_model_has_no_cloud_fallback(tmp_path):
    import sys
    with pytest.raises(ValueError,match="missing"):
        await translate_captions(cues(),tmp_path,tmp_path/"absent",sys.executable)


async def test_real_cpu_english_to_russian_preserves_cues_and_uses_cache(tmp_path):
    model=os.environ.get("BOSSMAN_VIDEO_TRANSLATION_MODEL")
    runtime=os.environ.get("BOSSMAN_VIDEO_TRANSLATION_PYTHON")
    if not model or not runtime:pytest.skip("Optional Marian weights/runtime not configured")
    original=cues();before=deepcopy(original)
    out=await translate_captions(original,tmp_path,model,runtime)
    assert original==before and out["local"] and out["draft_only"] and out["egress"]=="none"
    assert "привет" in out["captions"][0]["text"].casefold()
    assert "спасибо" in out["captions"][1]["text"].casefold()
    for a,b in zip(original,out["captions"]):
        assert {k:v for k,v in a.items() if k!="text"}=={k:v for k,v in b.items() if k!="text"}
    again=await translate_captions(original,tmp_path,model,runtime)
    assert again["cached"] and again["captions"]==out["captions"]
