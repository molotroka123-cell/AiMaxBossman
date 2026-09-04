"""Что считается доказательством, а что — только похоже на него.

Маркер `[w1]` в ответе печатает модель. Напечатать его можно про что угодно,
включая страницу, которой не открывали, и цитату, которой на странице нет.
Подтверждением считается ровно одно — успешный `web.cite`: он ищет цитату в
теле страницы ДОСЛОВНО и записывает наблюдение. Всё остальное — «веб
использован, ссылка не подтверждена», и владельцу это обязано быть видно.

Сети здесь нет: реестр и хук работают с файлом прогона, а не с провайдером.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bcc.features.web_research import config, gate, ledger

PAGE = "https://ru.wikipedia.org/wiki/Небо"


class Bus:
    """Шина, которая помнит, что через неё прошло: событие — это и есть то,
    чем неподтверждённый ответ отличается от подтверждённого."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    async def emit(self, kind: str, **data) -> None:
        self.events.append((kind, data))

    def kinds(self) -> list[str]:
        return [k for k, _ in self.events]

    def data(self, kind: str) -> dict:
        return next(d for k, d in self.events if k == kind)


@pytest.fixture
def stand(tmp_path, monkeypatch):
    monkeypatch.setenv(config.FLAG, "1")
    monkeypatch.setenv("BOSSMAN_OSIRIS_ENABLED", "1")
    svc = SimpleNamespace(settings=SimpleNamespace(data_dir=tmp_path / "data"), bus=Bus())
    return svc


def opened_ledger(svc, run_id: int = 11) -> tuple[ledger.Ledger, str]:
    """Прогон, в котором страницу открыли, но ещё не цитировали."""
    book = ledger.Ledger.load(svc, run_id)
    token = book.mint(PAGE, kind="search", subject="небо", origin="wikipedia")
    entry = book.resolve(token)
    entry.opened_at = "2026-09-04T00:00:00Z"
    entry.status = "ok"
    book.save()
    return book, token


async def verdict(svc, run_id: int, answer: str) -> dict:
    return await gate.make_gate(svc)({"id": 1}, run_id, answer)


# ------------------------------------------------------- подтверждённая цитата

async def test_a_verified_cite_passes_without_a_second_attempt(stand):
    """web.cite прошёл — ответ со ссылкой проходит сразу и молча."""
    book, token = opened_ledger(stand)
    assert book.note_cite(token) is True

    out = await verdict(stand, 11, f"Небо синее из-за рассеяния [{token}].")
    assert out["verdict"] == "PASS"
    assert "reasons" not in out, out
    assert stand.bus.kinds() == [], "подтверждённый ответ не нуждается в оговорках"


async def test_the_cite_mark_survives_a_reload(stand):
    """Отметка о проверенной цитате durable: она переживает пробуждение в
    другом процессе, иначе после парковки на одобрение доказательство
    исчезало бы вместе с памятью."""
    book, token = opened_ledger(stand)
    book.note_cite(token)
    again = ledger.Ledger.load(stand, 11)
    assert [e.ref for e in again.cited_refs()] == [token]


# ------------------------------------------------------- маркер без цитаты

async def test_a_bare_marker_is_not_evidence(stand):
    """Маркер напечатан, цитаты нет: одна попытка исправиться — и не PASS."""
    book, token = opened_ledger(stand)

    out = await verdict(stand, 11, f"Небо синее [{token}].")
    assert out["verdict"] == "FAIL", "напечатанный маркер зачли за доказательство"
    assert out["reasons"] == "web_research/unverified"
    assert "web.cite" in out["feedback"], out["feedback"]


async def test_an_invented_marker_is_not_evidence(stand):
    """Номера w9 в реестре нет вовсе — это выдумка, а не ссылка."""
    opened_ledger(stand)
    out = await verdict(stand, 11, "Небо синее [w9].")
    assert out["verdict"] == "FAIL"
    assert out["reasons"] == "web_research/unverified"


async def test_a_marker_for_another_page_is_not_evidence(stand):
    """Цитата проверена для одной страницы, маркер поставлен на другую."""
    book, first = opened_ledger(stand)
    second = book.mint("https://ru.wikipedia.org/wiki/Море", kind="search",
                       subject="небо", origin="wikipedia")
    entry = book.resolve(second)
    entry.opened_at = "2026-09-04T00:00:00Z"
    book.save()
    book.note_cite(first)

    out = await verdict(stand, 11, f"Море солёное [{second}].")
    assert out["verdict"] == "FAIL", "чужая проверенная цитата зачлась этой ссылке"


# --------------------------------------------- вторая попытка: годен, но помечен

async def test_the_second_uncited_answer_passes_but_is_visibly_unverified(stand):
    """Вторая попытка не зацикливается и не выдаёт себя за доказанную.

    Ответ остаётся годным — владельцу он нужен, — но и прогон, и событие
    говорят прямо: веб использован, ссылка не подтверждена.
    """
    book, token = opened_ledger(stand)
    answer = f"Небо синее [{token}]."

    first = await verdict(stand, 11, answer)
    assert first["verdict"] == "FAIL"

    second = await verdict(stand, 11, answer)
    assert second["verdict"] == "PASS", "вторая попытка обязана быть годной"
    assert second["reasons"] == "web_research/unverified"
    assert "feedback" not in second, "повторная просьба = петля, её быть не должно"

    assert "web.citation_unverified" in stand.bus.kinds()
    said = stand.bus.data("web.citation_unverified")
    assert said["verified"] is False
    assert said["label"] == "веб использован, ссылка не подтверждена"
    assert said["markers"] == [token]

    third = await verdict(stand, 11, answer)
    assert third["verdict"] == "PASS" and "feedback" not in third


async def test_an_answer_without_any_marker_is_a_different_complaint(stand):
    """Забыл сослаться и напечатал пустой маркер — разные беды, разные тексты."""
    opened_ledger(stand)
    out = await verdict(stand, 11, "Небо синее, я читал.")
    assert out["verdict"] == "FAIL"
    assert out["reasons"] == "web_research/uncited"

    again = await verdict(stand, 11, "Небо синее, я читал.")
    assert again["verdict"] == "PASS"
    assert "web.uncited_answer" in stand.bus.kinds()


async def test_a_run_without_web_is_not_touched(stand):
    """Чужой прогон гейт не трогает вовсе."""
    out = await verdict(stand, 99, "Ответ без всякого веба.")
    assert out["verdict"] == "NOT_APPLICABLE"


# ------------------------------------------------------- честная готовность

def test_general_web_is_not_claimed_ready_without_a_routable_backend(stand, monkeypatch):
    """Brave объявлен, но ключа ему взять неоткуда — значит он НЕ работает.

    Показывать «общий веб доступен», пока ни один общий источник не опрашивается,
    значит обещать то, чего нет: модель пойдёт искать и вернётся ни с чем.
    """
    from bcc.features.web_research import sources

    monkeypatch.delenv("BOSSMAN_WEB_SEARXNG_URL", raising=False)
    ready = sources.readiness(stand)
    assert ready["code"] != "ready_general", ready
    assert not ready["general_web"], ready

    brave = next(b for b in ready["backends"] if b["id"] == "brave-search")
    assert brave["ready"] is False, "Brave показан работающим, а ключа взять неоткуда"
    assert brave["keyless"] is False
    assert "ключ" in brave["reason"].lower(), brave["reason"]


async def test_the_repair_budget_is_one_attempt_for_the_whole_citation_problem(stand):
    """Сначала без маркера, потом с пустым — это всё ещё ОДНА просьба.

    Иначе модель, исправившая одну жалобу другой, получала бы вторую просьбу,
    третью и так до max_steps: ровно та петля, ради отсутствия которой потолок
    и заведён.
    """
    book, token = opened_ledger(stand)

    first = await verdict(stand, 11, "Небо синее, я читал.")
    assert first["verdict"] == "FAIL" and first["reasons"] == "web_research/uncited"

    second = await verdict(stand, 11, f"Небо синее [{token}].")
    assert second["verdict"] == "PASS", "вторая просьба = петля"
    assert second["reasons"] == "web_research/unverified"
    assert "feedback" not in second
    assert "web.citation_unverified" in stand.bus.kinds()
