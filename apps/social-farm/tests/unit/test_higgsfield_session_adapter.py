"""Адаптер Higgsfield обязан работать ЧЕРЕЗ защиты сессии, а не рядом с ними.

Каждая проверка здесь отвечает на один вопрос: что случится с работой, когда
провайдер поведёт себя не так, как хочется. Правильный ответ ни разу не «ещё
раз то же самое».
"""
from __future__ import annotations

from pathlib import Path

import pytest

from social_farm.browser import BrowserState, ChallengeKind, FixtureDom
from social_farm.browser.capabilities import BrowserCapabilityState
from social_farm.generation.higgsfield_adapter import (DuplicateSubmission,
                                                       SubmissionNotObserved)
from social_farm.generation.higgsfield_browser_contracts import (
    BrowserGenerationRequest, BrowserGenerationState, MediaKind)
from social_farm.generation.higgsfield_selectors import ACTION_SUBMIT

import higgsfield_kit as kit


def request(tmp_path: Path, **kwargs) -> BrowserGenerationRequest:
    kwargs.setdefault("mission_id", "m-1")
    kwargs.setdefault("media_kind", MediaKind.VIDEO)
    kwargs.setdefault("prompt", "ночной город сверху, неон, дождь")
    kwargs.setdefault("output_workspace", tmp_path / "approved")
    kwargs.setdefault("duration_seconds", 5.0)
    return BrowserGenerationRequest(**kwargs)


# ------------------------------------------------------------------ подготовка

async def test_a_ready_page_becomes_ready_only_after_identity_is_verified(tmp_path):
    dom = FixtureDom(kit.ready_page())
    adapter = kit.adapter(dom, tmp_path / "q")
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.READY
    assert adapter.session.state is BrowserState.READY
    assert adapter.session.observed_identity == kit.IDENTITY


async def test_another_account_in_the_browser_never_generates(tmp_path):
    """Чужая рабочая область — чужая квота и файлы, которых владелец не увидит."""
    dom = FixtureDom(kit.ready_page(identity=kit.OTHER_IDENTITY))
    adapter = kit.adapter(dom, tmp_path / "q")
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.NEEDS_OWNER_AUTH
    assert observed.owner_action_required
    assert adapter.session.state is BrowserState.STOPPED
    assert dom.fills == [] and dom.clicks == []


async def test_a_sign_in_page_asks_the_owner_and_types_nothing(tmp_path):
    dom = FixtureDom(kit.auth_page())
    adapter = kit.adapter(dom, tmp_path / "q")
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.NEEDS_OWNER_AUTH
    assert observed.owner_action_required
    assert dom.fills == [] and dom.clicks == []


async def test_a_captcha_stops_the_job_and_hands_the_session_to_the_owner(tmp_path):
    """Капчу не проходят. Её называют владельцу и останавливаются."""
    dom = FixtureDom(kit.challenge_page())
    adapter = kit.adapter(dom, tmp_path / "q")
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.HUMAN_CHALLENGE
    assert observed.owner_action_required
    assert adapter.session.state is BrowserState.TAKEOVER_REQUIRED
    assert adapter.session.challenge.kind is ChallengeKind.CAPTCHA
    assert dom.clicks == []


async def test_a_missing_generate_control_is_ui_drift_not_a_retry(tmp_path):
    dom = FixtureDom(kit.drifted_page())
    adapter = kit.adapter(dom, tmp_path / "q")
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.UI_CHANGED
    assert observed.evidence["adapter_version"].startswith("higgsfield/")
    assert dom.clicks == []


async def test_three_ui_drifts_disable_the_capability_and_stop_the_attempts(tmp_path):
    """Третий отказ подряд — это сменившийся интерфейс, а не невезение."""
    dom = FixtureDom(kit.drifted_page())
    adapter = kit.adapter(dom, tmp_path / "q")
    for _ in range(3):
        assert (await adapter.prepare(request(tmp_path))).state \
            is BrowserGenerationState.UI_CHANGED

    record = adapter.session.ledger.records[ACTION_SUBMIT]
    assert record.state is BrowserCapabilityState.BROKEN_UI_VERSION
    assert adapter.session.ledger.cooling_down(ACTION_SUBMIT)

    # Четвёртая попытка даже не смотрит на страницу: возможность в паузе, и
    # повтор в сменившийся интерфейс — ровно то, чего пауза не допускает.
    generation_before = dom.generation
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.UI_CHANGED
    assert observed.evidence["cooling_down"] == "true"
    assert dom.generation == generation_before, "страница даже не перечитывалась"


async def test_a_rate_limit_is_reported_not_worked_around(tmp_path):
    dom = FixtureDom(kit.rate_limited_page())
    adapter = kit.adapter(dom, tmp_path / "q")
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.RATE_LIMITED
    assert dom.clicks == []


async def test_an_exhausted_plan_is_reported_and_nothing_is_purchased(tmp_path):
    dom = FixtureDom(kit.quota_page())
    adapter = kit.adapter(dom, tmp_path / "q")
    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.POLICY_BLOCKED
    assert dom.clicks == []


# ------------------------------------------------------------------ отправка

async def test_a_submission_is_accepted_only_with_two_independent_signals(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    assert (await adapter.prepare(task)).state is BrowserGenerationState.READY

    receipt = await adapter.submit(task)
    signals = set(receipt.evidence["signals"].split(","))
    assert len(signals) >= 2, signals
    assert "job_card_appeared" in signals
    assert receipt.job_id == task.job_id
    assert len(dom.clicks) == 1, "кнопка нажимается ровно один раз"


async def test_a_click_alone_is_not_proof_and_is_never_repeated(tmp_path):
    """Страница не изменилась ничем. Значит, неизвестно, ушла ли работа."""
    dom = kit.on(kit.ready_page(), on_click=kit.submitting(observable=False))
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    with pytest.raises(SubmissionNotObserved):
        await adapter.submit(task)
    assert len(dom.clicks) == 1, "повторной отправки при неизвестности не бывает"


async def test_one_signal_is_not_enough_to_call_a_job_submitted(tmp_path):
    """Самый опасный случай: похоже на отправку и ею не является.

    Текст «Generating» на странице ничего не доказывает сам по себе — он мог
    остаться от прошлой работы. Признак должен быть не один, иначе «отправлено»
    означает «на экране было подходящее слово».
    """
    dom = kit.on(kit.ready_page(), on_click=kit.submitting(signals=1))
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    with pytest.raises(SubmissionNotObserved) as refusal:
        await adapter.submit(task)
    assert "progress_text" in str(refusal.value), "признак был, но один"
    assert len(dom.clicks) == 1


async def test_the_same_job_is_never_submitted_twice(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    await adapter.submit(task)
    with pytest.raises(DuplicateSubmission):
        await adapter.submit(task)
    assert len(dom.clicks) == 1


async def test_the_prompt_reaches_the_form_and_the_duration_too(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path, aspect_ratio="9:16", duration_seconds=5.0)
    await adapter.prepare(task)
    await adapter.submit(task)
    typed = [value for _, value in dom.fills]
    assert task.prompt in typed
    assert "9:16" in typed
    assert "5.0" in typed


async def test_an_image_job_does_not_type_a_duration(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path, media_kind=MediaKind.IMAGE, duration_seconds=None)
    await adapter.prepare(task)
    await adapter.submit(task)
    duration = [element for element in dom.page.elements
                if element.attributes.get("data-testid") == "duration-input"]
    assert duration and duration[0].value == "", "у изображения нет длительности"
    typed = [value for _, value in dom.fills]
    assert task.prompt in typed


# ------------------------------------------------------------------ ожидание

async def test_waiting_is_reported_as_waiting(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    receipt = await adapter.submit(task)
    observed = await adapter.poll(task, receipt)
    assert observed.state is BrowserGenerationState.WAITING_PROVIDER


async def test_a_ready_result_is_seen_without_pressing_anything(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting(then_ready=True))
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    receipt = await adapter.submit(task)
    clicks_before = len(dom.clicks)
    observed = await adapter.poll(task, receipt)
    assert observed.state is BrowserGenerationState.OUTPUT_READY
    assert len(dom.clicks) == clicks_before, "наблюдение ничего не нажимает"


async def test_a_captcha_mid_flight_pauses_the_session_not_only_the_job(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    receipt = await adapter.submit(task)

    dom.page.markup = dom.page.markup.replace("</body>", '<div class="h-captcha"></div></body>')
    observed = await adapter.poll(task, receipt)
    assert observed.state is BrowserGenerationState.HUMAN_CHALLENGE
    assert adapter.session.state is BrowserState.TAKEOVER_REQUIRED


async def test_a_provider_failure_is_reported_as_a_failure(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    receipt = await adapter.submit(task)
    dom.page.text = dom.page.text + " Generation failed"
    observed = await adapter.poll(task, receipt)
    assert observed.state is BrowserGenerationState.FAILED


async def test_a_rate_limit_after_submission_is_reported_honestly(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    receipt = await adapter.submit(task)
    dom.page.text = dom.page.text + " Rate limit reached"
    observed = await adapter.poll(task, receipt)
    assert observed.state is BrowserGenerationState.RATE_LIMITED


# ------------------------------------------------------------------ журнал

async def test_every_step_of_the_job_is_in_the_session_audit(tmp_path):
    """Половина работы, которой нет в журнале, — это половина работы, которой
    через месяц не объяснить."""
    dom = kit.on(kit.ready_page(), on_click=kit.submitting())
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    await adapter.submit(task)

    actions = [record.action for record in adapter.session.audit.records]
    assert "identity.verify" in actions
    assert "generation.submit" in actions
    submitted = adapter.session.audit.by_action("generation.submit")
    assert submitted[-1].result == "ok"
    assert submitted[-1].idempotency_key == task.job_id


async def test_an_unobserved_submission_leaves_a_record_saying_so(tmp_path):
    dom = kit.on(kit.ready_page(), on_click=kit.submitting(observable=False))
    adapter = kit.adapter(dom, tmp_path / "q")
    task = request(tmp_path)
    await adapter.prepare(task)
    with pytest.raises(SubmissionNotObserved):
        await adapter.submit(task)
    record = adapter.session.audit.by_action("generation.submit")[-1]
    assert record.result == "not_observed"
    assert record.error_class == "SUBMISSION_NOT_OBSERVED"


async def test_a_session_opened_on_some_other_page_still_reaches_ready(tmp_path):
    """В живом браузере владельца открыто что угодно.

    Личность читается на той странице, которая открыта в момент запуска. Если
    адаптер не приведёт сессию на страницу генератора сам, работа получит
    «войдите» при полностью действующем входе.
    """
    page = kit.ready_page()
    elsewhere = FixtureDom(page)
    page.url = "https://fixture.higgsfield.local/settings/billing"
    adapter = kit.adapter(elsewhere, tmp_path / "q")
    assert adapter.session.landing_url == kit.GENERATION_URL

    observed = await adapter.prepare(request(tmp_path))
    assert observed.state is BrowserGenerationState.READY
    assert kit.GENERATION_URL in elsewhere.navigations
