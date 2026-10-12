"""Tests for tools/ops_intake. Every fixture is SYNTHETIC (generated, no real client data).

Run from the repo root:  python -m pytest tests/ops_intake -q -p no:cacheprovider

The suite has negative controls on purpose: a rule that merges is always paired with a case that must NOT merge,
and every "flagged, not guessed" rule is paired with the value it must not invent.
"""
from __future__ import annotations

import ast
import csv
import datetime as dt
import hashlib
import itertools
import json
import os
import re
import subprocess
import sys
from decimal import Decimal as D
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
TOOLS = HERE.parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from ops_intake import cli, dedupe as dd, extract as ex, report, table  # noqa: E402

FIX = HERE / "fixtures"
TG_JSON = FIX / "telegram_result_synthetic.json"
T0 = dt.datetime(2026, 10, 11, 9, 0)           # a Sunday
_seq = itertools.count(1)


# ----------------------------------------------------------------------------- helpers
def item(text="", minutes=0, sender="", source="telegram_json", fields=None, structured=False, ts=True, src=None):
    n = next(_seq)
    when = T0 + dt.timedelta(minutes=minutes) if ts else None
    inb = ex.Inbound(src_id=src or f"t:{n}", source=source, text=text, ts_utc=when, ts_ref=when, sender=sender,
                     fields=dict(fields or {}), structured=structured)
    return ex.extract(inb)


def lead(minutes=0, **fields):
    text = fields.pop("text", "")
    return item(text, minutes, source="csv", fields=fields, structured=True)


def one(text, ref=T0):
    return ex.extract(ex.Inbound(src_id="t:1", source="telegram_json", text=text, ts_utc=ref, ts_ref=ref, sender="S1"))


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("ops_demo")
    info = cli.run(TG_JSON, out, staff=["user900"], lang="en")
    return {"out": out, "info": info, "rows": read_csv(out / "table.csv"),
            "by_src": {r["first_src_id"]: r for r in read_csv(out / "table.csv")}}


# ----------------------------------------------------------------------------- phones
@pytest.mark.parametrize("raw", ["+420 777 123 456", "+420777123456", "00420 777 123 456", "(+420) 777-123-456",
                                 "420777123456", "+420 777.123.456"])
def test_same_czech_phone_in_different_spellings_normalises_identically(raw):
    assert ex.normalize_phone(raw) == ("+420777123456", "")


@pytest.mark.parametrize("raw", ["8 (999) 123-45-67", "+7 999 123 45 67", "79991234567", "+7(999)1234567",
                                 "8-999-123-45-67"])
def test_russian_phone_forms_agree(raw):
    assert ex.normalize_phone(raw)[0] == "+79991234567"


@pytest.mark.parametrize("raw", ["777 123 456", "(999) 123-45-67", "+420 77 123", "12345", "+8 999 123 45 67"])
def test_phone_without_country_or_wrong_length_is_ambiguous_never_completed(raw):
    phone, reason = ex.normalize_phone(raw)
    assert phone == "" and reason


def test_missing_phone_is_flagged_not_guessed():
    e = one("Нужен монтаж видео, пишите в телеграм")
    assert e.phone == "" and e.phone_raw == ""
    assert "phone_missing" in e.flags and "phone_ambiguous" not in e.flags


def test_ambiguous_phone_keeps_raw_text_and_goes_to_review():
    e = one("Нужен сайт. Мой номер 777 123 456")
    assert e.phone == "" and e.phone_raw == "777 123 456" and "phone_ambiguous" in e.flags
    assert "phone_ambiguous" in table.review_reasons(e.flags, ("service",))


def test_two_phones_are_flagged_first_is_not_silently_chosen_as_only():
    e = one("Нужна озвучка, звоните +420 777 111 222 или +420 608 999 888")
    assert e.phone == "+420777111222" and e.phone_alt == ["+420608999888"] and "phone_multiple" in e.flags


@pytest.mark.parametrize("text", ["бюджет 150 000 000 Kč", "заказ №123456789", "срок 12.10.2026 15:30",
                                  "карта 4242 4242 4242 4242", "нужно 5 000 руб"])
def test_numbers_that_are_not_phones_are_not_read_as_phones(text):
    e = one(text)
    assert e.phone == "" and e.phone_raw == ""


# ----------------------------------------------------------------------------- amounts
@pytest.mark.parametrize("text,value,ccy", [
    ("5 000 Kč", D(5000), "CZK"), ("1.2k USDT", D(1200), "USDT"), ("15000 руб", D(15000), "RUB"),
    ("$250", D(250), "USD"), ("€ 99,50", D("99.50"), "EUR"), ("1 200,50 EUR", D("1200.50"), "EUR"),
    ("15 тыс руб", D(15000), "RUB"), ("3000р", D(3000), "RUB"), ("1,234,567.89 USD", D("1234567.89"), "USD"),
    ("800 usdt trc20", D(800), "USDT")])
def test_amount_forms_with_currency(text, value, ccy):
    r = ex.extract_amounts(text)
    assert r["amount"] == value and r["currency"] == ccy and not r["flags"]


@pytest.mark.parametrize("text", ["5.000 Kč", "2,500 EUR", "99.999 USD"])
def test_thousands_or_decimal_amount_is_flagged_and_left_empty(text):
    r = ex.extract_amounts(text)
    assert r["amount"] is None and "amount_ambiguous" in r["flags"] and r["raw"]


def test_amount_without_currency_keeps_value_but_never_a_currency():
    e = one("Need website, budget 5000. email: bob@example.com")
    assert e.amount == D(5000) and e.currency == "" and "amount_currency_missing" in e.flags


def test_bare_numbers_are_not_amounts():
    e = one("Нужно 3 видео за 3 дня, 20 фото")
    assert e.amount is None and e.amount_raw == "" and "amount_missing" in e.flags


def test_range_is_two_candidates_not_one_chosen_value():
    r = ex.extract_amounts("от 3000 до 5000 Kč")
    assert r["amount"] is None and "amount_multiple" in r["flags"]
    assert r["candidates"] == ["3000 CZK", "5000 CZK"]


def test_amounts_in_different_currencies_are_never_summed_or_converted():
    items = [item("Нужен сайт, 100 USD, +420 700 000 001", 0, "S1"), item("Нужен сайт, 100 EUR, +420 700 000 002", 1, "S2"),
             item("Нужен сайт, 5000 Kč, +420 700 000 003", 2, "S3"),
             item("Нужен сайт, 100 USD, +420 700 000 004", 3, "S4")]
    res = dd.dedupe(items)
    rows = table.build_rows(res)
    s = report.summarize(load=ex.LoadResult(items=[], source_type="telegram_json", total_items=4, unparsed=[]),
                         extracted=items, result=res, rows=rows, params={})
    assert {k: (v["requests"], v["sum"]) for k, v in s["budgets"].items()} == {
        "USD": (2, "200"), "EUR": (1, "100"), "CZK": (1, "5000")}
    assert not any(k.lower() in ("total", "all", "sum") for k in s["budgets"])


def test_duplicate_is_not_double_counted_in_budget():
    a = item("Нужен монтаж видео, 5000 Kč, +420 777 123 456", 0, "S1")
    b = item("Нужен монтаж видео, 5000 Kč, +420 777 123 456", 2, "S1")
    res = dd.dedupe([a, b])
    rows = table.build_rows(res)
    s = report.summarize(load=ex.LoadResult(items=[], source_type="telegram_json", total_items=2, unparsed=[]),
                         extracted=[a, b], result=res, rows=rows, params={})
    assert s["budgets"]["CZK"]["sum"] == "5000" and s["counts"]["duplicates_merged"] == 1


# ----------------------------------------------------------------------------- deadlines
@pytest.mark.parametrize("text,date,basis", [
    ("нужно до 25.10", "2026-10-25", "year_inferred_from_message_date"),
    ("срок до 15.10.2026", "2026-10-15", "explicit_date"),
    ("надо к 20 октября", "2026-10-20", "year_inferred_from_message_date"),
    ("ready by Oct 30", "2026-10-30", "year_inferred_from_message_date"),
    ("deadline tomorrow", "2026-10-12", "relative_to_message_date"),
    ("сделайте до пятницы", "2026-10-16", "relative_to_message_date"),
    ("нужно через 3 дня", "2026-10-14", "relative_to_message_date"),
    ("within a week", "2026-10-18", "relative_to_message_date"),
    ("нужно до 1 января", "2027-01-01", "year_inferred_from_message_date")])
def test_deadline_resolution_is_explicit_about_its_basis(text, date, basis):
    r = ex.extract_deadline(text, T0)
    assert (r["date"], r["basis"]) == (date, basis) and not [f for f in r["flags"] if f != "urgent"]


@pytest.mark.parametrize("text", ["need dubbing by 04/05", "нужно до 31.02"])
def test_ambiguous_or_impossible_deadline_is_flagged_without_a_date(text):
    r = ex.extract_deadline(text, T0)
    assert r["date"] == "" and "deadline_ambiguous" in r["flags"] and r["raw"]


def test_duration_is_not_a_deadline():
    assert ex.extract_deadline("ролик до 1.5 минуты, 2000 Kč", T0)["raw"] == ""


def test_urgent_is_a_flag_not_an_invented_date():
    e = one("Срочно нужен монтаж видео")
    assert e.deadline_date == "" and e.deadline_basis == "urgent" and "urgent" in e.flags


def test_relative_deadline_without_message_date_is_not_resolved():
    r = ex.extract_deadline("нужно завтра", None)
    assert r["raw"] == "завтра" and r["date"] == "" and r["basis"] == "no_message_date"


# ----------------------------------------------------------------------------- service, name, e-mail, language
@pytest.mark.parametrize("text,service", [
    ("Нужен монтаж рилса", "video_editing"), ("need video editing for youtube", "video_editing"),
    ("нужна замена лица в ролике", "face_swap"), ("Need dubbing for a video", "voice_dubbing"),
    ("нужна озвучка", "voice_dubbing"), ("нужен логотип", "design"), ("Need a landing page", "website"),
    ("Нужен бот для телеграма", "bot_dev"), ("translation EN->CZ", "translation"),
    ("нужен ролик для рекламы", "video_editing")])
def test_service_taxonomy_russian_and_english(text, service):
    assert one(text).service == service


def test_two_requested_services_are_ambiguous_not_first_wins():
    e = one("Сделайте логотип и сайт для кофейни")
    assert e.service == "" and set(e.service_candidates) == {"design", "website"}
    assert "service_ambiguous" in e.flags


def test_message_without_any_service_is_flagged_missing():
    e = one("Добрый день, вопрос по цене")
    assert e.service == "" and "service_missing" in e.flags and e.is_request


def test_custom_taxonomy_file(tmp_path):
    p = tmp_path / "svc.json"
    p.write_text(json.dumps({"tattoo": ["тату", "tattoo"], "piercing": {"strong": ["пирсинг"], "weak": []}},
                            ensure_ascii=False), encoding="utf-8")
    tax = ex.Taxonomy.from_file(p)
    inb = ex.Inbound(src_id="t:1", source="telegram_json", text="хочу тату на плече", ts_utc=T0, ts_ref=T0)
    assert ex.extract(inb, tax).service == "tattoo"


@pytest.mark.parametrize("text,name", [
    ("Здравствуйте! Меня зовут Анна Новак. Нужен монтаж", "Анна Новак"), ("Hi, I'm John Miller. Need a logo", "John Miller"),
    ("Need voice clone. Name: Peter Novak. +420 775 100 200", "Peter Novak"), ("Это Анна, нужен сайт", "Анна"),
    ("Нужен сайт. С уважением, Иван", "Иван")])
def test_name_is_taken_only_from_explicit_markers(text, name):
    assert ex.extract_name(text) == name


@pytest.mark.parametrize("text", ["Нужен сайт Алексей", "I'm looking for a logo", "I am Interested in a logo",
                                  "Хочу заказать озвучку, Алексей"])
def test_name_is_not_invented_from_bare_words(text):
    assert ex.extract_name(text) == ""


def test_email_valid_invalid_and_gmail_key():
    assert one("пишите на Anna.K@Example.COM").email == "anna.k@example.com"
    bad = one("Нужен перевод, пишите ivan@mail")
    assert bad.email == "" and bad.email_raw == "ivan@mail" and "email_invalid" in bad.flags
    assert dd.email_key("Tom.Black+shop@Gmail.com") == dd.email_key("tomblack@gmail.com") == "tomblack@gmail.com"
    assert dd.email_key("a.b@corp.example") != dd.email_key("ab@corp.example")      # only gmail ignores dots


@pytest.mark.parametrize("text,lang", [
    ("Нужен монтаж видео для рекламы", "ru"), ("Need a logo for my shop, please", "en"),
    ("Hello! Нужен dubbing для ролика", "mixed"), ("???", "und"),
    ("Potřebuji překlad dokumentu, děkuji", "cs"), ("Потрібен монтаж відео, їхній сайт", "uk")])
def test_language_detection(text, lang):
    assert one(text).language == lang


# ----------------------------------------------------------------------------- duplicates: merge rules
def test_same_phone_in_different_spelling_is_merged():
    a = item("Нужен монтаж видео, +420 777 123 456", 0, "S1")
    b = item("Монтаж видео, пожалуйста! 00420777123456", 30, "S2")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 1 and [l.rule for l in res.links] == ["A_phone"]
    assert res.links[0].dup.src_id == b.src_id and res.clusters[0].first_src_id == a.src_id


def test_two_different_people_with_the_same_service_are_not_merged():
    a = item("Нужен логотип, бюджет 5000 Kč, +420 721 000 111", 0, "S1")
    b = item("Нужен логотип, бюджет 5000 Kč, +420 721 000 222", 5, "S2")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 2 and res.links == []
    assert all(not c.possible for c in res.clusters)


def test_same_person_asking_for_a_different_service_is_a_new_request():
    a = item("Нужен логотип, +420 777 123 456", 0, "S1")
    b = item("Ещё нужен сайт, +420 777 123 456", 20, "S1")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 2 and res.links == []


def test_request_outside_the_window_is_new_and_the_boundary_is_inclusive():
    def pair(delta):
        return dd.dedupe([item("Нужен монтаж, +420 777 123 456", 0, "S1"),
                          item("Нужен монтаж, +420 777 123 456", delta, "S1")], window_min=1440)
    assert len(pair(1440).clusters) == 1
    assert len(pair(1441).clusters) == 2 and pair(1441).links == []


def test_conflicting_amount_blocks_the_merge_and_both_rows_are_flagged():
    a = item("Нужен монтаж, 5000 Kč, +420 777 123 456", 0, "S1")
    b = item("Нужен монтаж, 6000 Kč, +420 777 123 456", 10, "S1")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 2 and res.links == []
    rows = table.build_rows(res)
    assert all(r["needs_review"] == "yes" and "possible_duplicate" in r["review_reasons"] for r in rows)
    assert rows[0]["possible_duplicate_of"] == rows[1]["row_id"]


def test_same_sender_and_service_merges_with_medium_confidence_unless_high_is_required():
    a = item("Нужен монтаж видео", 0, "S1")
    b = item("Хочу монтаж ролика, срочно", 10, "S1")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 1 and res.links[0].rule == "C_same_sender_service" \
        and res.links[0].confidence == "medium"
    strict = dd.dedupe([a, b], min_confidence="high")
    assert len(strict.clusters) == 2 and all("possible_duplicate" in c.row.flags for c in strict.clusters)


def test_name_match_is_transliteration_tolerant_for_leads_without_sender():
    a = lead(0, name="Анна Новак", service="монтаж")
    b = lead(60, name="Anna Novak", service="video editing")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 1 and res.links[0].rule == "B_name_service"
    assert dd.name_similarity("Петров Иван", "Ivan Petrov") == 1.0


def test_single_word_name_is_not_enough_to_merge_but_is_flagged():
    a = item("Меня зовут Иван. Нужен сайт", 0, "S1")
    b = item("Меня зовут Иван. Нужен сайт, +420 733 100 200", 10, "S2")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 2 and res.links == []
    assert all("possible_duplicate" in c.row.flags for c in res.clusters)


def test_different_telegram_senders_with_the_same_full_name_are_only_flagged():
    a = item("Я Анна Новак, нужен монтаж видео", 0, "S1")
    b = item("Это Анна Новак. Нужен монтаж видео", 10, "S2")
    res = dd.dedupe([a, b])
    assert len(res.clusters) == 2 and all("possible_duplicate" in c.row.flags for c in res.clusters)


def test_gmail_dots_and_plus_tags_do_not_defeat_the_email_match():
    a = lead(0, email="Tom.Black+shop@Gmail.com", service="website")
    b = lead(15, email="tomblack@gmail.com", service="website")
    assert [l.rule for l in dd.dedupe([a, b]).links] == ["A_email"]


# ----------------------------------------------------------------------------- duplicates: keep first, link the rest
def test_first_is_kept_and_only_empty_fields_are_filled_from_the_duplicate():
    a = item("Меня зовут Анна. Нужен монтаж видео", 0, "S1")
    b = item("Я Анна Новак, монтаж видео, мой номер +420 777 123 456", 10, "S1")
    c = dd.dedupe([a, b]).clusters[0]
    assert c.row.name == "Анна"                       # never overwritten
    assert c.row.phone == "+420777123456" and c.filled == [f"phone<-{b.src_id}"]
    assert "phone_missing" not in c.row.flags
    assert [m.src_id for m in c.members] == [a.src_id, b.src_id]


def test_ambiguous_phone_in_kept_row_is_not_replaced_by_the_duplicates_value():
    a = item("Нужен монтаж, номер 777 123 456", 0, "S1")
    b = item("Монтаж, +420 777 123 456", 10, "S1")
    c = dd.dedupe([a, b]).clusters[0]
    assert c.row.phone == "" and "phone_ambiguous" in c.row.flags and c.filled == []
    assert any(i.startswith("phone(not filled") for i in c.links[0].new_info)


def test_unsorted_input_still_keeps_the_chronologically_first_request():
    late = lead(120, phone="+420 777 123 456", service="сайт", text="again")
    early = lead(0, phone="+420 777 123 456", service="сайт", text="first")
    res = dd.dedupe([late, early])
    assert res.clusters[0].first_src_id == early.src_id and res.links[0].dup.src_id == late.src_id


def test_items_without_timestamp_merge_only_on_identity_and_say_so():
    a = lead(0, phone="+420 777 123 456", service="сайт")
    b = item("", 0, source="csv", fields={"phone": "00420777123456", "service": "сайт"}, structured=True, ts=False)
    res = dd.dedupe([a, b])
    assert len(res.links) == 1 and res.links[0].window_verified is False
    c = item("Нужен сайт", 0, "S1", ts=False)
    d = item("Нужен сайт", 0, "S1", ts=False)
    assert len(dd.dedupe([c, d]).clusters) == 2       # no window evidence: fuzzy rules never fire


def test_answered_status_follows_a_reply_to_any_member_of_the_cluster():
    a = item("Нужен монтаж, +420 777 123 456", 0, "S1")
    b = item("Нужен монтаж, +420 777 123 456", 5, "S1")
    b.answered, b.answered_basis = True, "reply_link:tg:9"
    a.answered, a.answered_basis = False, "no_reply_link_found"
    row = dd.dedupe([a, b]).clusters[0].row
    assert row.answered is True and row.answered_basis == "reply_link:tg:9"


# ----------------------------------------------------------------------------- end to end on the synthetic chat
def test_demo_counts_and_reconciliation(demo):
    c = demo["info"]["counts"]
    assert (c["input_items"], c["unparsed"], c["skipped"], c["requests_found"]) == (81, 0, 12, 69)
    assert (c["unique_requests"], c["duplicates_merged"], c["needs_review"], c["possible_duplicates"]) == (57, 12, 16, 2)
    assert all(demo["info"]["reconciliation"].values())
    assert c["input_items"] == c["unparsed"] + c["skipped"] + c["requests_found"]
    assert c["requests_found"] == c["unique_requests"] + c["duplicates_merged"]


def test_demo_every_input_message_is_accounted_for_exactly_once(demo):
    raw = json.loads(TG_JSON.read_text(encoding="utf-8"))
    sources = [f"tg:{m['id']}" for m in raw["messages"] if m["type"] == "message"]
    ids = demo["info"]["ids"]
    placed = ids["unique_requests"] + ids["duplicates_merged"] + ids["skipped"] + ids["unparsed"]
    assert sorted(placed) == sorted(sources) and len(set(placed)) == len(placed)


def test_demo_report_numbers_trace_to_id_lists(demo):
    info = demo["info"]
    for key in ("input_items", "unparsed", "skipped", "requests_found", "unique_requests", "duplicates_merged",
                "needs_review", "possible_duplicates"):
        assert len(info["ids"][key]) == info["counts"][key], key
    md = (demo["out"] / "daily_report.md").read_text(encoding="utf-8")
    for ref, key in (("A5", "unique_requests"), ("A6", "duplicates_merged"), ("A7", "needs_review")):
        m = re.search(rf"\*\*\[{ref}\] .* - (\d+)\*\*: (.*)", md)
        assert m and int(m.group(1)) == info["counts"][key] == len(m.group(2).split(", "))


def test_demo_negative_controls_survive_the_whole_pipeline(demo):
    uniq = set(demo["info"]["ids"]["unique_requests"])
    dups = set(demo["info"]["ids"]["duplicates_merged"])
    assert {"tg:1038", "tg:1039"} <= uniq                  # two people, same logo request, different phones
    assert {"tg:1010", "tg:1013"} <= uniq                  # same phone, different service
    assert {"tg:1001", "tg:1040"} <= uniq                  # same phone and service, 28.5 h apart
    assert {"tg:1019", "tg:1017", "tg:1025"} <= dups      # re-spelled phone, exact resend, e-mail case


def test_demo_rows_flagged_for_review_and_what_they_do_not_contain(demo):
    r = demo["by_src"]
    assert r["tg:1024"]["amount"] == "" and r["tg:1024"]["amount_raw"] == "2,500 EUR"
    assert r["tg:1030"]["phone"] == "" and r["tg:1030"]["phone_raw"] == "777 654 321"
    assert r["tg:1044"]["phone"] == "" and "phone_ambiguous" in r["tg:1044"]["review_reasons"]
    assert r["tg:1046"]["amount"] == "" and r["tg:1046"]["amount_candidates"] == "3000 CZK; 5000 CZK; 150 EUR"
    assert r["tg:1047"]["amount"] == "5000" and r["tg:1047"]["currency"] == ""
    assert r["tg:1050"]["deadline_date"] == "" and "deadline_ambiguous" in r["tg:1050"]["review_reasons"]
    assert r["tg:1045"]["service"] == "" and "service_missing" in r["tg:1045"]["review_reasons"]
    assert r["tg:1060"]["service"] == "" and r["tg:1060"]["service_candidates"] == "website; translation"
    assert r["tg:1041"]["possible_duplicate_of"] == r["tg:1042"]["row_id"]
    assert sum(1 for x in demo["rows"] if x["needs_review"] == "yes") == 16


def test_demo_clean_rows_are_not_sent_to_review(demo):
    r = demo["by_src"]
    for src in ("tg:1002", "tg:1010", "tg:1034", "tg:1065", "tg:1076"):
        assert r[src]["needs_review"] == "no", src
    assert r["tg:1002"]["name"] == "Анна Новак" and r["tg:1002"]["phone"] == "+420777123456"
    assert (r["tg:1010"]["amount"], r["tg:1010"]["currency"]) == ("1200", "USDT")
    assert (r["tg:1034"]["deadline_date"], r["tg:1034"]["merged_src_ids"]) == ("2026-10-16", "tg:1035")
    assert r["tg:1076"]["phone"] == "+491701234567"


def test_demo_budget_totals_are_per_currency(demo):
    s = json.loads((demo["out"] / "run.json").read_text(encoding="utf-8"))
    md = (demo["out"] / "daily_report.md").read_text(encoding="utf-8")
    got = {}
    for line in md.splitlines():
        m = re.match(r"\| (CZK|EUR|RUB|USD|USDT) \| (\d+) \| (\d+) \|", line)
        if m:
            got[m.group(1)] = (int(m.group(2)), int(m.group(3)))
    assert got == {"CZK": (13, 127000), "EUR": (3, 1960), "RUB": (6, 54500), "USD": (6, 4025), "USDT": (3, 2100)}
    assert s["counts"]["unique_requests"] == 57


def test_demo_staff_replies_are_not_requests_and_mark_requests_answered(demo):
    ids = demo["info"]["ids"]
    assert {f"tg:200{n}" for n in range(1, 9)} <= set(ids["skipped"])
    r = demo["by_src"]
    assert r["tg:1002"]["answered"] == "yes" and r["tg:1018"]["answered"] == "yes"   # reply to kept row
    assert r["tg:1024"]["answered"] == "no"


def test_demo_sla_oldest_unanswered_and_breaches(demo):
    md = (demo["out"] / "daily_report.md").read_text(encoding="utf-8")
    assert "R0001 / tg:1001 - 2026-10-10 05:30:00 UTC - age, h: 38.67" in md
    assert "Older than the SLA threshold (threshold, h: 24): **2**" in md


def test_without_staff_flag_staff_replies_are_read_as_requests_and_a_warning_is_issued(tmp_path):
    info = cli.run(TG_JSON, tmp_path)
    assert info["counts"]["skipped"] < 12 and any(w["code"] == "staff_not_given" for w in info["warnings"])


def test_no_field_value_is_invented_every_value_is_found_in_the_source_text(demo):
    for r in demo["rows"]:
        text, digits = r["text"], re.sub(r"\D", "", r["text"])
        if r["phone"]:
            assert re.sub(r"\D", "", r["phone"])[-9:] in digits, r["row_id"]
        if r["email"]:
            assert r["email"] in text.lower(), r["row_id"]
        if r["handle"]:
            assert r["handle"].lower() in text.lower(), r["row_id"]
        if r["amount_raw"] and r["source"] == "telegram_json":
            assert r["amount_raw"].split("; ")[0] in text, r["row_id"]
        if r["name"]:
            assert r["name"].split()[0].lower() in text.lower(), r["row_id"]


# ----------------------------------------------------------------------------- loaders
def test_csv_leads_fixture(tmp_path):
    info = cli.run(FIX / "leads_synthetic.csv", tmp_path)
    c = info["counts"]
    assert (c["input_items"], c["unparsed"], c["unique_requests"], c["duplicates_merged"]) == (13, 1, 9, 3)
    assert info["unparsed"] == [{"ref": "csv:row12", "reason": "more cells than header columns (unquoted delimiter?)"}]
    rows = {r["first_src_id"]: r for r in read_csv(tmp_path / "table.csv")}
    assert rows["csv:row1"]["email"] == "anna@example.com"          # filled from the re-spelled duplicate
    assert rows["csv:row1"]["filled_from"] == "email<-csv:row2"
    assert rows["csv:row3"]["answered"] == "yes"                    # status column
    assert rows["csv:row5"]["needs_review"] == "no" and rows["csv:row6"]["needs_review"] == "no"   # Maria x2, other phones
    assert rows["csv:row7"]["review_reasons"].startswith("empty_request")
    assert rows["csv:row10"]["phone"] == "" and rows["csv:row10"]["phone_raw"] == "12345"
    assert "timestamp_unparsed" in rows["csv:row11"]["flags"] and rows["csv:row11"]["ts_utc"] == ""
    assert rows["csv:row14"]["amount"] == "" and "amount_ambiguous" in rows["csv:row14"]["review_reasons"]


def test_jsonl_invalid_line_is_counted_not_dropped(tmp_path):
    info = cli.run(FIX / "leads_synthetic.jsonl", tmp_path)
    assert info["unparsed"] == [{"ref": "jsonl:line5", "reason": "invalid JSON (Expecting value)"}]
    c = info["counts"]
    assert (c["input_items"], c["unparsed"], c["requests_found"], c["unique_requests"]) == (7, 1, 6, 4)
    assert info["ids"]["input_items"].count("jsonl:line5") == 1
    assert {w["code"] for w in info["warnings"]} == {"answer_unknown"}


def test_html_export_reply_link_and_unparsed_block(tmp_path):
    info = cli.run(FIX / "messages_synthetic.html", tmp_path)
    c = info["counts"]
    assert (c["unparsed"], c["unique_requests"], c["duplicates_merged"], c["skipped"]) == (1, 2, 1, 2)
    rows = {r["first_src_id"]: r for r in read_csv(tmp_path / "table.csv")}
    assert rows["tg:5001"]["answered"] == "yes" and rows["tg:5004"]["answered"] == "no"


def test_telegram_message_without_date_is_unparsed_not_fatal(tmp_path):
    good = {"id": 1, "type": "message", "date": "2026-10-11T09:00:00", "date_unixtime": "1760166000",
            "from": "A", "from_id": "user1", "text": "Нужен сайт, +420 777 123 456"}
    bad = {"id": 2, "type": "message", "from": "B", "from_id": "user2", "text": "Нужен монтаж"}
    p = tmp_path / "result.json"
    p.write_text(json.dumps({"messages": [good, bad]}), encoding="utf-8")
    load = ex.detect_and_load(p)
    assert [i.src_id for i in load.items] == ["tg:1"] and load.unparsed[0]["ref"] == "tg:2"
    assert load.total_items == 2


def test_whole_account_export_is_refused_with_a_clear_error(tmp_path):
    p = tmp_path / "result.json"
    p.write_text(json.dumps({"chats": {"list": []}}), encoding="utf-8")
    with pytest.raises(ValueError, match="no 'messages' list"):
        ex.detect_and_load(p)
    assert cli.main(["run", "--input", str(p), "--out", str(tmp_path / "o")]) == 2


def test_unknown_file_type_and_bad_require_are_refused(tmp_path):
    p = tmp_path / "x.pdf"
    p.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported input type"):
        ex.detect_and_load(p)
    with pytest.raises(ValueError, match="unknown --require"):
        table.build_rows(dd.dedupe([]), ("bogus",))


def test_require_phone_sends_rows_without_phone_to_review(tmp_path):
    base = cli.run(TG_JSON, tmp_path / "a", staff=["user900"])
    strict = cli.run(TG_JSON, tmp_path / "b", staff=["user900"], require=("service", "contact", "phone"))
    assert strict["counts"]["needs_review"] > base["counts"]["needs_review"]
    assert strict["manual_review"]["by_reason"]["phone_missing"] > 0


# ----------------------------------------------------------------------------- outputs
def test_csv_is_formula_safe_and_xlsx_text_is_an_explicit_string(demo):
    row = demo["by_src"]["tg:1055"]
    assert row["text"].startswith("'=HYPERLINK(")                      # CSV guard
    assert table.safe_cell("=1+1") == "'=1+1" and table.safe_cell("+420777123456") == "+420777123456"
    assert table.safe_cell("-evil") == "'-evil" and table.safe_cell("@SUM(A1)") == "'@SUM(A1)"
    openpyxl = pytest.importorskip("openpyxl")
    ws = openpyxl.load_workbook(demo["out"] / "table.xlsx")["requests"]
    col = [c.value for c in ws[1]].index("text") + 1
    cell = next(ws.cell(row=r, column=col) for r in range(2, ws.max_row + 1)
                if str(ws.cell(row=r, column=col).value).startswith("=HYPERLINK"))
    assert cell.data_type == "s"


def test_xlsx_has_requests_duplicates_needs_review_and_skipped_sheets(demo):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.load_workbook(demo["out"] / "table.xlsx")
    assert wb.sheetnames == ["requests", "duplicates", "needs_review", "skipped"]
    assert wb["requests"].max_row == 58 and wb["duplicates"].max_row == 13 and wb["needs_review"].max_row == 17
    header = [c.value for c in wb["requests"][1]]
    assert "needs_review" in header and header == table.COLUMNS


def test_without_openpyxl_the_run_succeeds_and_says_xlsx_was_not_written(tmp_path, monkeypatch):
    def boom():
        raise ImportError("no openpyxl here")
    monkeypatch.setattr(table, "_load_openpyxl", boom)
    info = cli.run(TG_JSON, tmp_path, staff=["user900"])
    assert info["xlsx"]["written"] is False and "openpyxl" in info["xlsx"]["reason"]
    assert not (tmp_path / "table.xlsx").exists() and (tmp_path / "table.csv").exists()
    assert (tmp_path / "duplicates.csv").exists() and (tmp_path / "needs_review.csv").exists()


def test_no_xlsx_flag(tmp_path):
    info = cli.run(TG_JSON, tmp_path, staff=["user900"], xlsx=False)
    assert info["xlsx"]["written"] is False and not (tmp_path / "table.xlsx").exists()


def test_run_json_records_hash_timings_and_output_hashes(demo):
    info = json.loads((demo["out"] / "run.json").read_text(encoding="utf-8"))
    assert info["input"]["sha256"] == hashlib.sha256(TG_JSON.read_bytes()).hexdigest()
    assert set(info["timings_s"]) == {"load", "extract", "dedupe", "tables_and_summary", "write_outputs", "total"}
    assert info["timings_s"]["total"] > 0 and info["no_network_no_model_no_send"] is True
    for name in ("table.csv", "duplicates.csv", "needs_review.csv", "daily_report.md", "notifications_draft.md"):
        data = (demo["out"] / name).read_bytes()
        assert info["outputs"][name]["sha256"] == hashlib.sha256(data).hexdigest()
    assert info["manual_review"]["rows"] == 16 and info["manual_review"]["of_unique_requests"] == 57


def test_outputs_are_deterministic(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    cli.run(TG_JSON, a, staff=["user900"])
    cli.run(TG_JSON, b, staff=["user900"])
    for name in ("table.csv", "duplicates.csv", "needs_review.csv", "skipped.csv", "daily_report.md",
                 "notifications_draft.md"):
        assert (a / name).read_bytes() == (b / name).read_bytes(), name


@pytest.mark.parametrize("lang,head", [("ru", "Ежедневный отчёт"), ("en", "Daily intake report")])
def test_report_in_both_languages_with_the_same_numbers(tmp_path, lang, head):
    info = cli.run(TG_JSON, tmp_path, staff=["user900"], lang=lang)
    md = (tmp_path / "daily_report.md").read_text(encoding="utf-8")
    assert md.startswith("# " + head) and "**57**" in md and "**12**" in md and "**16**" in md
    assert info["counts"]["unique_requests"] == 57


def test_notifications_are_drafts_only_one_per_unique_request(demo):
    md = (demo["out"] / "notifications_draft.md").read_text(encoding="utf-8")
    drafts = re.findall(r"^## \d+\. R\d{4} .* STATUS: DRAFT, NOT SENT$", md, re.M)
    assert len(drafts) == 57 and "Nothing was sent" in md
    # a Russian message gets a Russian draft even in an English report; the ambiguous phone is asked about, not guessed
    assert "Уточните, пожалуйста, номер телефона целиком" in md
    assert "Could you confirm the budget amount and currency (you wrote: 2,500 EUR)" in md
    assert "1200 USDT" in md                                        # restates a clean extracted field only


def test_draft_asks_do_not_promise_prices_or_dates(demo):
    md = (demo["out"] / "notifications_draft.md").read_text(encoding="utf-8")
    quotes = [ln for ln in md.splitlines() if ln.startswith("> ") and "received your request" in ln]
    assert quotes and not any(re.search(r"(?i)price is|will cost|delivered by", q) for q in quotes)


def test_package_has_no_network_sending_or_model_imports():
    banned = {"socket", "urllib", "http", "requests", "smtplib", "ftplib", "telnetlib", "subprocess", "openai",
              "anthropic", "aiohttp", "httpx", "ssl", "imaplib", "poplib"}
    for path in (TOOLS / "ops_intake").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                     else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
            for n in names:
                assert n.split(".")[0] not in banned, f"{path.name} imports {n}"


def test_module_entry_point_runs_with_cwd_tools(tmp_path):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "ops_intake.cli", "run", "--input", str(TG_JSON), "--out", str(tmp_path),
         "--staff", "user900", "--window-min", "1440"],
        cwd=TOOLS, capture_output=True, text=True, encoding="utf-8", env=env, timeout=120)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["unique_requests"] == 57 and out["duplicates_merged"] == 12 and out["needs_review"] == 16
    assert (tmp_path / "run.json").exists()


def test_window_option_changes_the_result_in_the_expected_direction(tmp_path):
    narrow = cli.run(TG_JSON, tmp_path / "n", staff=["user900"], window_min=3)
    wide = cli.run(TG_JSON, tmp_path / "w", staff=["user900"], window_min=4000)
    assert narrow["counts"]["duplicates_merged"] < 12 < wide["counts"]["duplicates_merged"] + 1
    assert wide["counts"]["duplicates_merged"] >= 13     # tg:1040 now links to tg:1001 (28.5 h apart)


# ----------------------------------------------------------------------------- more loaders, scale
def test_json_array_of_leads_and_a_non_object_element(tmp_path):
    p = tmp_path / "leads.json"
    p.write_text(json.dumps([
        {"name": "Lena Frost", "phone": "+420 602 000 001", "service": "logo", "budget": "300 USD"},
        {"name": "Lena Frost", "phone": "00420602000001", "service": "logo"}, "junk"], ensure_ascii=False),
        encoding="utf-8")
    info = cli.run(p, tmp_path / "o")
    c = info["counts"]
    assert info["input"]["type"] == "json_array"
    assert (c["input_items"], c["unparsed"], c["unique_requests"], c["duplicates_merged"]) == (3, 1, 1, 1)
    assert info["unparsed"][0]["ref"] == "json:item3"


def test_messages_without_any_sender_never_share_an_anonymous_sender(tmp_path):
    msgs = [{"id": i, "type": "message", "date": f"2026-10-11T09:0{i}:00", "date_unixtime": str(1760166000 + 60 * i),
             "text": "Нужен монтаж видео"} for i in (1, 2)]
    p = tmp_path / "result.json"
    p.write_text(json.dumps({"messages": msgs}), encoding="utf-8")
    info = cli.run(p, tmp_path / "o")
    assert info["counts"]["unique_requests"] == 2 and info["counts"]["duplicates_merged"] == 0


def test_non_utf8_file_is_refused_not_mangled(tmp_path):
    p = tmp_path / "leads.csv"
    p.write_bytes("Имя;Телефон;Услуга\nАнна;+420 777 123 456;монтаж\n".encode("cp1251"))
    with pytest.raises(ValueError, match="not valid UTF-8"):
        ex.detect_and_load(p)
    assert cli.main(["run", "--input", str(p), "--out", str(tmp_path / "o")]) == 2


@pytest.mark.parametrize("sep", [",", ";", "\t"])
def test_csv_delimiter_is_detected_from_the_header(tmp_path, sep):
    p = tmp_path / "leads.csv"
    rows = [["name", "phone", "service", "message"], ["Anna", "+420 777 123 456", "website", "need a site, please"]]
    with open(p, "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, delimiter=sep).writerows(rows)
    load = ex.detect_and_load(p)
    assert len(load.items) == 1 and load.items[0].fields["phone"] == "+420 777 123 456"
    assert load.items[0].text == "need a site, please"


def test_thousands_of_messages_finish_quickly_and_text_cannot_stall_the_regexes(tmp_path):
    base = dt.datetime(2026, 10, 11, 0, 0)
    msgs = []
    for i in range(2000):
        t = base + dt.timedelta(seconds=10 * i)
        msgs.append({"id": i + 1, "type": "message", "date": t.strftime("%Y-%m-%dT%H:%M:%S"),
                     "date_unixtime": str(int((t - dt.timedelta(hours=2)).replace(tzinfo=dt.timezone.utc).timestamp())),
                     "from": f"C{i}", "from_id": f"user{i}",
                     "text": f"Нужен монтаж видео, бюджет {1000 + i} Kč, +420 {600000000 + i}"})
    p = tmp_path / "result.json"
    p.write_text(json.dumps({"messages": msgs}, ensure_ascii=False), encoding="utf-8")
    info = cli.run(p, tmp_path / "o", xlsx=False)
    assert info["counts"]["unique_requests"] == 2000 and info["timings_s"]["total"] < 30
    hostile = ("1 " * 2000) + ("5 000 " * 500) + "(" * 500 + "+" * 500 + "@" * 300
    assert one(hostile + " нужен сайт").service == "website"


def test_range_without_currency_keeps_both_bounds_and_is_flagged():
    r = ex.extract_amounts("бюджет 5000-7000")
    assert r["amount"] is None and "amount_multiple" in r["flags"] and r["candidates"] == ["5000", "7000"]
    assert ex.extract_amounts("видео 3-5 дней")["candidates"] == []        # a duration is not a budget
