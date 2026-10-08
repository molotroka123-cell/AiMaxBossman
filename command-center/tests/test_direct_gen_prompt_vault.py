from bcc.direct_gen.prompt_vault import PromptVault


def _job(job_id, participant="owner", prompt="тайный промт 18+"):
    return {"job_id": job_id, "participant": participant, "model": "m", "kind": "photo", "created_at": "t",
            "raw_prompt": prompt, "effective_prompt": prompt, "negative": "", "retry_of": None}


def test_owner_prompt_is_stored_encrypted_and_searchable(tmp_path):
    v = PromptVault(tmp_path)
    v.record(_job("a" * 32))
    v.record(_job("b" * 32, prompt="другой"))
    assert "тайный".encode() not in v.file.read_bytes()
    assert [r["job_id"] for r in v.search("тайный")] == ["a" * 32]
    assert len(v.search()) == 2
    assert PromptVault(tmp_path).search("тайный")


def test_same_job_is_recorded_once_and_participants_are_not(tmp_path):
    v = PromptVault(tmp_path)
    v.record(_job("a" * 32))
    v.record(_job("a" * 32))
    v.record(_job("c" * 32, participant="f" * 64))
    assert [r["job_id"] for r in v.search()] == ["a" * 32]


def test_damaged_line_does_not_hide_the_rest(tmp_path):
    v = PromptVault(tmp_path)
    v.record(_job("a" * 32))
    with v.file.open("a", encoding="utf-8") as fh:
        fh.write("garbage\n")
    v.record(_job("b" * 32, prompt="ещё"))
    assert {r["job_id"] for r in v.search()} == {"a" * 32, "b" * 32}
