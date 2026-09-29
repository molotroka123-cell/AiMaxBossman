from bcc.features.coding_limit_saver_v16 import (
    CodingRouteIn,
    CandidateIn,
    GLM_MODEL,
    choose_writer,
    request_cache_key,
)


def c(id, cls, *, success=.5, latency=1.0, quota=1, ctx=100_000):
    return CandidateIn(
        id=id,
        writer_class=cls,
        capabilities={"coding"},
        verified_success=success,
        latency_s=latency,
        remaining_quota=quota,
        context_window=ctx,
    )


def test_free_or_local_writes_before_glm():
    req = CodingRouteIn(
        task_id="t",
        task_fingerprint="same-task-v1",
        candidates=[
            c("local-qwen", "LOCAL", success=.80, latency=3),
            c("free-nemotron", "FREE", success=.85, latency=4),
            c("glm", "GLM53_FLASH", success=.95, latency=1),
        ],
    )
    d = choose_writer(req)
    assert d.action == "WRITE_CODE"
    assert d.writer_class in {"LOCAL", "FREE"}
    assert d.writer_id == "free-nemotron"
    assert d.model is None


def test_glm_is_bounded_escalation_after_cheap_failures():
    req = CodingRouteIn(
        task_id="t",
        task_fingerprint="hard-task",
        free_attempts=2,
        glm_calls=0,
        max_glm_calls=2,
        candidates=[c("free", "FREE"), c("glm", "GLM53_FLASH", success=.9)],
    )
    d = choose_writer(req)
    assert d.writer_class == "GLM53_FLASH"
    assert d.model == GLM_MODEL

    exhausted = req.model_copy(update={"glm_calls": 2, "failed_writer_ids": {"free"}})
    d2 = choose_writer(exhausted)
    assert d2.action == "BLOCKED"


def test_aster_cannot_become_a_code_writer():
    # Schema itself rejects ASTER as a writer class; route with no legal writers blocks.
    req = CodingRouteIn(task_id="t", task_fingerprint="x", candidates=[])
    d = choose_writer(req)
    assert d.action == "BLOCKED"
    assert d.aster_mode == "AUDIT_ONLY"
    assert "Aster" in d.reason


def test_local_only_never_routes_external_free_or_glm():
    req = CodingRouteIn(
        task_id="private",
        task_fingerprint="private-code",
        privacy_class="LOCAL_ONLY",
        candidates=[c("free", "FREE"), c("glm", "GLM53_FLASH")],
    )
    assert choose_writer(req).action == "BLOCKED"

    req2 = req.model_copy(update={"candidates": [c("local", "LOCAL")]})
    assert choose_writer(req2).writer_id == "local"


def test_context_is_capped_and_identical_request_has_same_cache_key():
    req = CodingRouteIn(
        task_id="a",
        task_fingerprint="repo:bug:123",
        requested_context_tokens=90_000,
        candidates=[c("local", "LOCAL", ctx=100_000)],
    )
    d = choose_writer(req)
    assert d.context_token_cap == 30_000

    other = req.model_copy(update={"task_id": "b"})
    assert request_cache_key(req) == request_cache_key(other)


def test_no_quota_means_free_candidate_is_skipped():
    req = CodingRouteIn(
        task_id="t",
        task_fingerprint="quota",
        candidates=[
            c("free-empty", "FREE", quota=0),
            c("local", "LOCAL", success=.4),
        ],
    )
    assert choose_writer(req).writer_id == "local"
