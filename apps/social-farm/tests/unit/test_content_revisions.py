"""Неизменяемость ревизий и одобрение, привязанное к точному содержимому.

Инвариант, ради которого всё это существует: **опубликовать можно только то,
что видел человек**. Он держится не на дисциплине, а на том, что любая правка
меняет хеш, а одобрение несёт хеш.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from social_farm.domain.content import (Approval, ContentError, ContentRevision,
                                        canonical_json, content_hash)
from social_farm.domain.identity import idempotency_key


def _revision(**over) -> ContentRevision:
    data = dict(id="p1:rev1", project_id="p1", revision_no=1, caption="привет",
                assets=({"id": "a1", "checksum_sha256": "aaa"},),
                target_account_ids=("acct",), schedule_at="2026-09-01T10:00:00Z")
    data.update(over)
    return ContentRevision(**data)


# ------------------------------------------------------------------ хеш

def test_hash_ignores_key_order_but_not_content():
    """Канонизация нужна, чтобы одобрение не слетало само по себе от
    перестановки ключей — и только для этого."""
    a = content_hash(project_id="p", revision_no=1, caption="текст",
                     assets=[{"id": "a", "checksum_sha256": "1"},
                             {"id": "b", "checksum_sha256": "2"}],
                     target_account_ids=["x", "y"])
    b = content_hash(project_id="p", revision_no=1, caption="текст",
                     assets=[{"id": "b", "checksum_sha256": "2"},
                             {"id": "a", "checksum_sha256": "1"}],
                     target_account_ids=["y", "x"])
    assert a == b
    assert a != content_hash(project_id="p", revision_no=1, caption="текст!",
                             assets=[{"id": "a", "checksum_sha256": "1"},
                                     {"id": "b", "checksum_sha256": "2"}],
                             target_account_ids=["x", "y"])


def test_swapped_file_under_the_same_id_changes_the_hash():
    """Иначе подменённый файл прошёл бы под старым одобрением — и это была бы
    публикация того, чего человек не видел, при формально верной проверке."""
    before = content_hash(project_id="p", revision_no=1, caption="c",
                          assets=[{"id": "a1", "checksum_sha256": "старый"}])
    after = content_hash(project_id="p", revision_no=1, caption="c",
                         assets=[{"id": "a1", "checksum_sha256": "новый"}])
    assert before != after


def test_time_is_normalised_to_utc_in_the_hash():
    """Смена часового пояса не должна делать одобрение недействительным."""
    assert content_hash(project_id="p", revision_no=1, caption="c",
                        schedule_at="2026-09-01T12:00:00+02:00") == \
           content_hash(project_id="p", revision_no=1, caption="c",
                        schedule_at="2026-09-01T10:00:00Z")


def test_canonical_json_is_compact_and_sorted():
    assert canonical_json({"b": 1, "a": [3, 2]}) == '{"a":[3,2],"b":1}'


# ------------------------------------------------------------------ ревизии

def test_a_revision_cannot_be_mutated():
    revision = _revision()
    with pytest.raises(Exception):
        revision.caption = "другое"          # frozen dataclass


def test_editing_creates_a_new_revision_and_drops_approval():
    """Отдельного механизма «отозвать одобрение» нет — он получается сам."""
    approved = _revision().approve("owner")
    assert approved.approved is True

    edited = approved.next_revision(caption="исправленный текст")
    assert edited.revision_no == 2
    assert edited.supersedes_revision_id == approved.id
    assert edited.approved is False, "одобрение перенеслось на изменённый контент"
    assert edited.content_hash != approved.content_hash


def test_computed_fields_cannot_be_forced_on_the_next_revision():
    with pytest.raises(ContentError, match="вычисляются"):
        _revision().next_revision(approved_at="2026-01-01T00:00:00Z")
    with pytest.raises(ContentError, match="вычисляются"):
        _revision().next_revision(revision_no=99)


def test_approval_requires_an_actor():
    with pytest.raises(ContentError, match="кто одобрил"):
        _revision().approve("")


# ------------------------------------------------------------------ проверка перед действием

def _approval(revision: ContentRevision, **over) -> Approval:
    data = dict(id="ap1", job_id="j1", status="APPROVED",
                content_revision_id=revision.id,
                approved_content_hash=revision.content_hash,
                capability="media.publish.image", account_id="acct",
                policy_version=3, requested_at="2026-08-28T00:00:00Z")
    data.update(over)
    return Approval(**data)


def test_approval_matching_the_revision_passes():
    revision = _revision()
    _approval(revision).validate_for(revision, capability="media.publish.image",
                                     account_id="acct", policy_version=3)


def test_approval_does_not_survive_an_edit():
    revision = _revision()
    approval = _approval(revision)
    edited = revision.next_revision(caption="подменённый текст")
    with pytest.raises(ContentError, match="содержимое изменилось|ревизию"):
        approval.validate_for(edited, capability="media.publish.image",
                              account_id="acct", policy_version=3)


def test_approval_does_not_transfer_to_another_action_or_account():
    revision = _revision()
    approval = _approval(revision)
    with pytest.raises(ContentError, match="на действие"):
        approval.validate_for(revision, capability="media.delete",
                              account_id="acct", policy_version=3)
    with pytest.raises(ContentError, match="для аккаунта"):
        approval.validate_for(revision, capability="media.publish.image",
                              account_id="acct_other", policy_version=3)


def test_expired_approval_is_refused():
    revision = _revision()
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    with pytest.raises(ContentError, match="истекло"):
        _approval(revision, expires_at=past).validate_for(
            revision, capability="media.publish.image", account_id="acct",
            policy_version=3)


def test_approval_is_void_when_policy_version_went_backwards():
    revision = _revision()
    with pytest.raises(ContentError, match="версия политики"):
        _approval(revision, policy_version=5).validate_for(
            revision, capability="media.publish.image", account_id="acct",
            policy_version=4)


# ------------------------------------------------------------------ идемпотентность

def test_the_same_intent_gives_the_same_key():
    """Случайный ключ не защищает ни от чего: при повторе он был бы другим."""
    args = dict(account_id="acct", capability="media.publish.image",
                payload={"caption": "привет", "assets": ["a1"]},
                schedule_at="2026-09-01T10:00:00Z")
    assert idempotency_key(**args) == idempotency_key(**args)


def test_different_time_slot_is_a_different_intent():
    base = dict(account_id="acct", capability="media.publish.image",
                payload={"caption": "привет"})
    assert idempotency_key(**base, schedule_at="2026-09-01T10:00:00Z") != \
           idempotency_key(**base, schedule_at="2026-09-01T18:00:00Z")


def test_key_covers_every_external_effect_not_only_publishing():
    """Спека даёт формулу только для публикации, а ключ нужен всему,
    что меняет внешний мир."""
    reply = idempotency_key(account_id="acct", capability="comments.reply",
                            target_ref="comment_42", payload={"text": "спасибо"})
    other = idempotency_key(account_id="acct", capability="comments.reply",
                            target_ref="comment_43", payload={"text": "спасибо"})
    assert reply != other, "ответы разным комментариям получили один ключ"
    assert reply.startswith("acct:comments.reply:comment_42:")


def test_key_requires_account_and_capability():
    with pytest.raises(ValueError):
        idempotency_key(account_id="", capability="media.publish.image", payload={})
