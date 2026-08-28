"""Сессия: автомат, устаревшая цель, капча, личность, разрушающее действие.

Здесь проверяется не то, что код выполняется, а то, что он ОТКАЗЫВАЕТСЯ
выполняться там, где обещал отказаться. Поэтому почти каждый тест ниже
заканчивается проверкой `dom.clicks == []`: главное доказательство защиты — что
внешнего эффекта не случилось.
"""
from __future__ import annotations

import pytest

from browser_kit import (FEED_TEXT, IDENTITY, feed_page, flatten, login_session,
                         ready_session, session)
from social_farm.browser import (BrokenUi, BrowserState, ChallengeKind, FixtureDom,
                                 FixtureElement, IdentityMismatch, SecretRef,
                                 detect_challenge)
from social_farm.browser.capabilities import FailureKind
from social_farm.domain.errors import ErrorClass, ProviderError


# ------------------------------------------------------------------ запуск

async def test_start_verifies_identity_before_becoming_ready():
    dom, sess = ready_session()
    assert await sess.start() is BrowserState.READY
    assert sess.observed_identity == IDENTITY
    verified = [r for r in sess.audit.records if r.action == "identity.verify"]
    assert verified and verified[0].result == "ok"


async def test_a_foreign_account_in_the_context_stops_the_session():
    """Вход в чужой аккаунт по ошибке видят все подписчики. Поэтому — стоп."""
    dom, sess = ready_session(identity="chuzhoj_akkaunt")
    with pytest.raises(IdentityMismatch) as exc:
        await sess.start()
    assert exc.value.observed == "chuzhoj_akkaunt"
    assert sess.state is BrowserState.STOPPED
    mismatch = [r for r in sess.audit.records if r.result == "mismatch"]
    assert mismatch, "несовпадение личности обязано остаться в аудите"


async def test_a_stopped_session_performs_no_actions():
    dom, sess = ready_session(identity="chuzhoj_akkaunt")
    with pytest.raises(IdentityMismatch):
        await sess.start()
    with pytest.raises(RuntimeError):
        await sess.click("media.publish.image")
    assert dom.clicks == []


async def test_a_page_without_identity_asks_for_a_human():
    """На странице входа личности нет — значит, входа не было."""
    dom, sess = login_session()
    assert await sess.start() is BrowserState.LOGIN_REQUIRED


async def test_login_never_happens_automatically():
    """В автомате нет ребра `LOGIN_REQUIRED → AUTHENTICATED`, и это читается
    буквально: вход всегда завершает человек."""
    from social_farm.browser.states import TRANSITIONS
    assert BrowserState.AUTHENTICATED not in TRANSITIONS[BrowserState.LOGIN_REQUIRED]


def element(dom, **match):
    """Элемент страницы по признакам, а не по номеру: номер меняется вместе с
    фикстурой и делает тест хрупким там, где проверяется не он."""
    for item in dom.page.elements:
        if all(getattr(item, key) == value for key, value in match.items()):
            return item
    raise AssertionError(f"на странице фикстуры нет элемента {match}")


# ------------------------------------------------------------------ капча

CAPTCHA_PAGE_MARKUP = '<div class="g-recaptcha" data-sitekey="x"></div>'


async def test_captcha_leads_to_a_human_not_to_an_attempt():
    """Капча — это осознанно поставленный контроль доступа. Мы её не проходим."""
    dom, sess = ready_session()
    await sess.start()
    dom.page.markup = CAPTCHA_PAGE_MARKUP
    with pytest.raises(ProviderError) as exc:
        await sess.click("media.publish.image")
    assert exc.value.error_class is ErrorClass.BROWSER_REQUIRES_TAKEOVER
    assert sess.state is BrowserState.TAKEOVER_REQUIRED
    assert dom.clicks == [], "по странице с капчей не должно быть ни одного нажатия"
    assert dom.fills == []
    assert "не проходится" in (exc.value.user_action or "")


async def test_a_challenge_on_startup_calls_a_human_and_says_why():
    """Проверка прямо на входе — значит, входа не было и нужен человек.

    Состояния мало: владельцу нужна причина, по которой его позвали, и она
    обязана оказаться в аудите. Иначе он видит остановленную работу и не
    видит, что именно на экране.
    """
    dom, sess = login_session()
    dom.page.markup = CAPTCHA_PAGE_MARKUP
    assert await sess.start() is BrowserState.TAKEOVER_REQUIRED
    assert sess.challenge.kind is ChallengeKind.CAPTCHA
    asked = [r for r in sess.audit.records if r.action == "takeover.request"]
    assert asked, "передача человеку обязана остаться в аудите"
    assert "reCAPTCHA" in asked[0].detail
    assert asked[0].error_class == ErrorClass.BROWSER_REQUIRES_TAKEOVER.value
    assert dom.clicks == [] and dom.fills == []


async def test_captcha_failure_does_not_demote_the_capability():
    """Капча — не поломка интерфейса: счётчик детерминированных отказов не растёт."""
    dom, sess = ready_session()
    await sess.start()
    dom.page.markup = CAPTCHA_PAGE_MARKUP
    with pytest.raises(ProviderError):
        await sess.click("media.publish.image")
    record = sess.ledger.records["media.publish.image"]
    assert record.consecutive_failures == 0


@pytest.mark.parametrize("markup,text,kind", [
    ('<script src="https://hcaptcha.com/1/api.js">', "", ChallengeKind.CAPTCHA),
    ("<div id='cf-turnstile'></div>", "", ChallengeKind.CAPTCHA),
    ("", "Подтвердите, что вы человек", ChallengeKind.CAPTCHA),
    ("", "We detected an unusual login", ChallengeKind.SECURITY_CHECKPOINT),
    ("", "Enter the code we sent to your phone", ChallengeKind.TWO_FACTOR),
])
def test_known_challenges_are_recognized(markup, text, kind):
    found = detect_challenge(markup=markup, text=text)
    assert found.kind is kind
    assert found.present


def test_no_challenge_on_an_ordinary_page():
    assert not detect_challenge(markup="<h1>Лента</h1>", text=FEED_TEXT).present


async def test_takeover_completes_only_after_the_challenge_is_gone():
    dom, sess = ready_session()
    await sess.start()
    dom.page.markup = CAPTCHA_PAGE_MARKUP
    with pytest.raises(ProviderError):
        await sess.click("media.publish.image")
    # Человек ещё не закончил: проверка на месте.
    assert await sess.complete_takeover() is BrowserState.TAKEOVER_REQUIRED
    dom.page.markup = "<h1>Публикации</h1>"
    assert await sess.complete_takeover() is BrowserState.READY


async def test_takeover_that_ends_in_the_wrong_account_stops_the_session():
    """Самое частое место для чужого аккаунта: человек с несколькими вкладками."""
    dom, sess = ready_session()
    await sess.start()
    dom.page.markup = CAPTCHA_PAGE_MARKUP
    with pytest.raises(ProviderError):
        await sess.click("media.publish.image")
    dom.page.markup = "<h1>Публикации</h1>"
    element(dom, text=IDENTITY).text = "chuzhoj_akkaunt"
    with pytest.raises(IdentityMismatch):
        await sess.complete_takeover()
    assert sess.state is BrowserState.STOPPED


# ------------------------------------------------------------------ устаревшая цель

async def test_a_vanished_target_is_not_clicked():
    dom, sess = ready_session()
    await sess.start()
    target = await sess.plan("media.publish.image")
    element(dom, text="Поделиться").text = "Опубликовать"   # кнопку переименовали
    with pytest.raises(ProviderError) as exc:
        await sess.act(target)
    assert exc.value.error_class is ErrorClass.BROWSER_STALE_TARGET
    assert dom.clicks == [], "устаревшая цель не нажимается"
    assert sess.state is BrowserState.READY


async def test_a_changed_target_under_the_same_selector_is_not_used():
    """Селектор нашёл элемент, но это уже другой элемент.

    Между планом и действием проходит одобрение человека — именно там страница
    успевает измениться, и именно там слепое действие опаснее всего.
    """
    dom, sess = ready_session()
    await sess.start()
    target = await sess.plan("content.draft")
    element(dom, tag="textarea").value = "чужой черновик"  # в поле уже напечатали
    with pytest.raises(ProviderError) as exc:
        await sess.act(target, operation="fill", text="наш текст")
    assert exc.value.error_class is ErrorClass.BROWSER_STALE_TARGET
    assert dom.fills == []


async def test_stale_target_is_not_counted_as_broken_ui():
    """Гонка — не смена интерфейса. Иначе одна перерисовка отключала бы
    возможность, которая работает."""
    dom, sess = ready_session()
    await sess.start()
    target = await sess.plan("media.publish.image")
    element(dom, text="Поделиться").text = "Опубликовать"
    with pytest.raises(ProviderError):
        await sess.act(target)
    assert sess.ledger.records["media.publish.image"].consecutive_failures == 0


# ------------------------------------------------------------------ неоднозначность

async def test_an_ambiguous_target_is_refused_not_guessed():
    """«Do not click nearest alternative silently»: три кнопки «Удалить» — это
    три разные записи, и выбор наугад означает удаление не той."""
    page = feed_page()
    page.elements.append(FixtureElement(tag="button", text="Удалить",
                                        attributes={"id": "drop2"}))
    page.text = page.text + " Удалить публикацию?"
    dom = FixtureDom(page)
    sess = session(dom)
    await sess.start()
    with pytest.raises(BrokenUi) as exc:
        await sess.click("media.delete")
    assert exc.value.kind is FailureKind.TARGET_AMBIGUOUS
    assert dom.clicks == []


async def test_an_explicit_ordinal_resolves_ambiguity():
    """Номер — это явное решение вызывающего, а не догадка сессии."""
    page = feed_page()
    page.elements.append(FixtureElement(tag="button", text="Удалить",
                                        attributes={"id": "drop2"}))
    page.text = page.text + " Удалить публикацию?"
    dom = FixtureDom(page)

    def forget_confirmation(page_, element):
        page_.text = FEED_TEXT

    dom.on_click = forget_confirmation
    sess = session(dom)
    await sess.start()
    result = await sess.click("media.delete", ordinal=1)
    assert result["ok"]
    assert len(dom.clicks) == 1


# ------------------------------------------------------------------ разрушающее

async def test_a_destructive_action_without_confirmation_text_is_refused():
    """«Успешно нажато» без подтверждения на экране ничего не значит."""
    dom, sess = ready_session()
    await sess.start()
    with pytest.raises(BrokenUi) as exc:
        await sess.click("media.delete")
    assert exc.value.kind is FailureKind.CONFIRMATION_MISMATCH
    assert dom.clicks == []
    assert sess.state is BrowserState.BROKEN_UI


async def test_a_failed_postcondition_is_broken_ui_and_is_counted():
    """Диалог остался на экране — значит, нажатие не сделало того, что обещало."""
    page = feed_page()
    page.text = FEED_TEXT + " Удалить публикацию?"
    dom = FixtureDom(page)
    sess = session(dom)
    await sess.start()
    with pytest.raises(BrokenUi) as exc:
        await sess.click("media.delete")
    assert exc.value.kind is FailureKind.POSTCONDITION_FAILED
    assert len(dom.clicks) == 1, "нажатие произошло, а постусловие — нет"
    assert sess.state is BrowserState.BROKEN_UI
    assert sess.ledger.records["media.delete"].consecutive_failures == 1


async def test_broken_ui_is_left_only_by_an_explicit_decision():
    dom, sess = ready_session()
    await sess.start()
    with pytest.raises(BrokenUi):
        await sess.click("media.delete")
    assert sess.state is BrowserState.BROKEN_UI
    sess.recover_from_broken_ui()
    assert sess.state is BrowserState.READY


# ------------------------------------------------------------------ «refresh once»

class ReloadingDom(FixtureDom):
    """Фикстура, у которой цель появляется после обновления страницы."""

    def __init__(self, page, appears):
        super().__init__(page)
        self.appears = appears
        self.reloads = 0

    async def reload(self) -> None:
        await super().reload()
        self.reloads += 1
        self.page.elements.append(self.appears)


async def test_the_page_is_refreshed_once_and_not_in_a_loop():
    page = feed_page()
    missing = [e for e in page.elements if e.text != "Поделиться"]
    page.elements = missing
    dom = ReloadingDom(page, FixtureElement(tag="button", text="Поделиться",
                                            attributes={"id": "share"}))
    sess = session(dom)
    await sess.start()
    target = await sess.plan("media.publish.image")
    assert dom.reloads == 1, "обновление ровно одно: это не цикл"
    assert target.descriptor.accessible_name == "Поделиться"


async def test_a_target_that_never_appears_is_broken_ui():
    page = feed_page()
    page.elements = [e for e in page.elements if e.text != "Поделиться"]
    dom = FixtureDom(page)
    sess = session(dom)
    await sess.start()
    with pytest.raises(BrokenUi) as exc:
        await sess.plan("media.publish.image")
    assert exc.value.kind is FailureKind.TARGET_MISSING


# ------------------------------------------------------------------ ввод секрета

async def test_secret_is_never_accepted_as_a_string():
    dom, sess = login_session(secrets={"vault://login": "parol-2026"})
    await sess.start()
    with pytest.raises(TypeError):
        await sess.assist_fill_secret("login.password.fill", "parol-2026")
    assert dom.fills == []


async def test_secret_is_only_typed_into_a_secret_field():
    dom, sess = login_session(secrets={"vault://login": "parol-2026"})
    await sess.start()
    with pytest.raises(ValueError):
        await sess.assist_fill_secret("login.username.fill", SecretRef("vault://login"))
    assert dom.fills == []


async def test_secret_assistance_is_impossible_while_the_session_works():
    """Подстановка пароля — часть входа, который заканчивает человек, а не
    обычное действие в READY."""
    dom, sess = ready_session(secrets={"vault://login": "parol-2026"})
    await sess.start()
    with pytest.raises(RuntimeError):
        await sess.assist_fill_secret("login.password.fill", SecretRef("vault://login"))


async def test_assistance_refuses_a_page_that_is_not_the_login_page():
    """Учётные данные не вводятся на странице, которую мы не опознали."""
    page = feed_page()
    dom = FixtureDom(page)
    sess = session(dom)
    sess._state = BrowserState.LOGIN_REQUIRED
    with pytest.raises(BrokenUi) as exc:
        await sess.assist_fill_text("login.username.fill", "nashe_ateljie")
    assert exc.value.kind is FailureKind.CONFIRMATION_MISMATCH
    assert dom.fills == []


async def test_a_missing_secret_is_a_refusal_not_an_empty_string():
    dom, sess = login_session(secrets={})
    await sess.start()
    from social_farm.browser import SecretNotFound
    with pytest.raises(SecretNotFound):
        await sess.assist_fill_secret("login.password.fill", SecretRef("vault://net"))
    assert dom.fills == []


# ------------------------------------------------------------------ описание сессии

async def test_describe_matches_the_browser_session_schema():
    dom, sess = ready_session()
    await sess.start()
    row = sess.describe()
    assert set(row) == {"id", "account_id", "session_ref", "state",
                        "selector_pack_version", "last_verified_at",
                        "last_takeover_at"}
    assert row["state"] == "READY"
    assert row["selector_pack_version"] == "1.0.0"
    assert row["last_verified_at"]


async def test_audit_records_what_the_owner_will_need_in_a_month():
    dom = FixtureDom(feed_page())
    dom.on_click = lambda page, element: setattr(
        page, "text", page.text + " Опубликовано")
    sess = session(dom)
    await sess.start()
    result = await sess.click("media.publish.image", idempotency_key="idem-1")
    assert result["ok"]
    record = sess.audit.by_action("media.publish.image")[0]
    assert record.target_identity == "button[Поделиться]#0"
    assert record.target_fingerprint
    assert record.url_before and record.url_after
    assert record.idempotency_key == "idem-1"
    assert record.state_after == "READY"
    assert "<" not in flatten(sess.audit.dicts(sess.redactor)), "разметки в аудите нет"
