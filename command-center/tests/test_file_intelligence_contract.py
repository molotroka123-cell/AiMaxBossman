"""§24 уровень A — контракт File Intelligence на детерминированном сайдкаре.

Проходит в CI без моделей и без настоящего бинаря. Проверяется КАЖДАЯ граница
протокола: JSON, статусы, ошибки, эффект. Настоящий бинарь — отдельный уровень
(§32), и эти тесты его не заменяют и не выдают за него.

Инварианты, которые здесь держатся:

    PROPOSAL != AUTHORIZATION
    APPROVAL != POST_STATE
    PROCESS_EXIT_0 != VERIFIED_EFFECT
    MODEL_TEXT != PROOF
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bcc import file_intelligence as fi
from bcc.file_intelligence import protocol, verify
from bcc.file_intelligence.models import (BackendMode, Denied, JobState, Operation,
                                          Refusal, UpstreamStatus)
from bcc.file_intelligence.scope import ScopePolicy
from bcc.file_intelligence.service import FileIntelligenceService

from . import fileintel_fake as fake


# ------------------------------------------------------------------ фикстуры

@pytest.fixture
def workspace(tmp_path):
    """Разрешённый корень с тремя файлами и сайдкаром, который их разложит."""
    root = tmp_path / "Downloads"
    files = fake.make_corpus(root, 3)
    state = tmp_path / "state"
    policy = ScopePolicy(authorized_roots=[root],
                         protected_paths=[state],
                         repository_root=tmp_path / "repo")
    executable = fake.make_executable(tmp_path)
    plan = fake.review_plan(
        [fake.plan_entry(str(f), category="Documents") for f in files],
        paths=[str(root)])
    sidecar = fake.FakeSidecar(plan=plan, effect=fake.move_effect)
    service = FileIntelligenceService(
        state_dir=state, policy=policy,
        discovery=fake.fake_discovery(executable),
        config_path=fake.local_config(tmp_path),
        runner=sidecar)
    return type("WS", (), {"root": root, "files": files, "service": service,
                           "sidecar": sidecar, "policy": policy,
                           "tmp": tmp_path, "plan": plan})


async def _approved(ws, selected=None):
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.REVIEW_REQUIRED.value, job.detail
    chosen = selected if selected is not None else [str(f) for f in ws.files]
    return await ws.service.apply(job.job_id, chosen)


# ------------------------------------------------------------------ §31 флаг

def test_feature_is_off_by_default():
    """Способность двигать файлы не появляется у системы сама."""
    assert fi.enabled() is False


def test_unknown_task_class_is_refused_not_guessed():
    with pytest.raises(Denied) as denied:
        fi.operation_for("file.delete_everything")
    assert denied.value.refusal is Refusal.UNSUPPORTED_ARGUMENT


def test_every_task_class_maps_to_the_same_governed_route():
    """§7 — восемь имён в интерфейсе, один путь исполнения.

    Отдельный маршрут на каждую кнопку означал бы отдельную дыру на каждую
    кнопку, поэтому проверяется, что все они разворачиваются в перечисление
    операций, а не в собственный вызов.
    """
    assert set(fi.TASK_CLASSES) == {
        "file.rename_smart", "file.categorize", "file.categorize_and_rename",
        "folder.organize", "downloads.clean", "photos.organize",
        "documents.organize", "media.organize"}
    for name in fi.TASK_CLASSES:
        assert isinstance(fi.operation_for(name), Operation)


# ------------------------------------------------------------------ §6 auto-apply

def test_analyze_argv_is_always_review_only():
    """`--review-only` не параметр: переданное значение можно вычислить из
    недоверенного входа, а отсутствие параметра — нельзя."""
    argv = protocol.analyze_argv("/bin/aifilesorter", operation=Operation.CATEGORIZE,
                                 path=Path("/tmp/x"), review_file=Path("/tmp/r.json"),
                                 status_file=Path("/tmp/s.json"), job_id="abc")
    assert "--review-only" in argv
    assert not any(f in argv for f in protocol.FORBIDDEN_FLAGS)


@pytest.mark.parametrize("flag", sorted(protocol.FORBIDDEN_FLAGS))
def test_every_upstream_auto_apply_spelling_is_unreachable(flag):
    """§6 — upstream принимает ЧЕТЫРЕ написания (HeadlessAnalysisCommand.cpp:699).

    Закрыть одно и объявить проблему решённой означало бы оставить три калитки.
    Проверяется и `--flag`, и `--flag=value`.
    """
    from bcc.file_intelligence.protocol import _reject_forbidden
    for token in (flag, f"{flag}=true"):
        with pytest.raises(Denied) as denied:
            _reject_forbidden(["/bin/aifilesorter", token])
        assert denied.value.refusal is Refusal.AUTO_APPLY_FORBIDDEN


async def test_model_arguments_cannot_turn_review_only_into_auto_apply(workspace):
    """Враждебный случай §6: попытка протащить флаг через данные.

    Класс задачи, цели и имена файлов — всё, что приходит снаружи. Ни одно из
    этого не становится флагом: argv собирается из перечислений и путей.
    """
    ws = workspace
    hostile = ws.root / "--auto-apply"
    hostile.write_text("not a flag, a filename\n", encoding="utf-8")

    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.REVIEW_REQUIRED.value

    for argv in ws.sidecar.calls:
        assert not any(token.split("=")[0] in protocol.FORBIDDEN_FLAGS
                       for token in argv), argv
        assert "--review-only" in argv or "--headless-apply" in argv
    # имя файла доехало как ЗНАЧЕНИЕ, а не как флаг
    assert any("--path" in argv for argv in ws.sidecar.calls)


def test_apply_argv_accepts_no_path_and_no_operation():
    """Применение исполняет утверждённый план и только его.

    Возможность передать сюда путь означала бы, что «применить утверждённое» и
    «сделать что-то ещё» — один вызов.
    """
    argv = protocol.apply_argv("/bin/aifilesorter", review_file=Path("/tmp/r.json"),
                               status_file=Path("/tmp/s.json"), job_id="abc")
    assert "--path" not in argv and "--operation" not in argv
    assert "--headless-apply" in argv


def test_argv_is_a_list_so_a_filename_is_never_a_command(workspace):
    """Shell-строки нет, поэтому экранировать нечего.

    Имя `; rm -rf ~` остаётся именем: интерпретатора, который мог бы его
    разобрать, в этом пути не существует.
    """
    argv = protocol.analyze_argv(
        "/bin/aifilesorter", operation=Operation.CATEGORIZE,
        path=Path("/tmp/; rm -rf ~/$(whoami)`id`"), review_file=Path("/tmp/r.json"),
        status_file=Path("/tmp/s.json"), job_id="abc")
    assert isinstance(argv, list) and all(isinstance(t, str) for t in argv)
    assert "/tmp/; rm -rf ~/$(whoami)`id`" in argv          # одно целое значение


def test_job_id_cannot_smuggle_a_flag():
    for bad in ("--auto-apply", "-x", "a b", "a;b", "", "x" * 200):
        with pytest.raises(Denied):
            protocol.analyze_argv("/bin/x", operation=Operation.CATEGORIZE,
                                  path=Path("/tmp"), review_file=Path("/tmp/r"),
                                  status_file=Path("/tmp/s"), job_id=bad)


# ------------------------------------------------------------------ §4 протокол

def test_unknown_status_is_failed_closed_not_reinterpreted():
    """Новое состояние upstream приедет как UNKNOWN и будет ВИДНО.

    Подогнать незнакомое под ближайшее знакомое — это способ превратить будущее
    `partially_applied` в сегодняшнее `completed`.
    """
    assert UpstreamStatus.parse("some_future_state") is UpstreamStatus.UNKNOWN
    assert UpstreamStatus.parse(None) is UpstreamStatus.UNKNOWN
    assert protocol.is_terminal(UpstreamStatus.UNKNOWN) is True
    assert protocol.is_terminal(UpstreamStatus.RUNNING) is False


@pytest.mark.parametrize("raw,refusal", [
    ("this is not json", Refusal.PROTOCOL_FAILED),
    ("", Refusal.PROTOCOL_FAILED),
    (None, Refusal.PROTOCOL_FAILED),
    ('["a","list"]', Refusal.PROTOCOL_FAILED),
    ('{"schemaVersion": 99, "status": "completed"}', Refusal.PROTOCOL_FAILED),
])
def test_malformed_status_is_a_protocol_failure(raw, refusal):
    with pytest.raises(Denied) as denied:
        protocol.parse_status(raw)
    assert denied.value.refusal is refusal


@pytest.mark.parametrize("document,refusal", [
    ({"kind": "something.else", "schemaVersion": 1, "entries": []},
     Refusal.REVIEW_PLAN_MALFORMED),
    ({"kind": fake.PLAN_KIND, "schemaVersion": 42, "entries": []},
     Refusal.REVIEW_PLAN_SCHEMA_UNSUPPORTED),
    ({"kind": fake.PLAN_KIND, "schemaVersion": 1},
     Refusal.REVIEW_PLAN_MALFORMED),
])
def test_a_file_that_is_not_a_review_plan_is_not_parsed_as_far_as_it_goes(
        document, refusal):
    with pytest.raises(Denied) as denied:
        protocol.parse_review_plan(json.dumps(document))
    assert denied.value.refusal is refusal


async def test_non_json_stdout_fails_the_job_without_touching_files(workspace):
    ws = workspace
    ws.sidecar.raw_status = "Segmentation fault (core dumped)"
    before = {f: f.read_text() for f in ws.files}
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.FAILED.value
    assert job.refusal == Refusal.PROTOCOL_FAILED.value
    assert all(f.read_text() == before[f] for f in ws.files)


async def test_sidecar_crash_is_a_named_outcome_not_a_silent_pass(workspace):
    ws = workspace
    ws.sidecar.raise_on_run = OSError("sidecar died")
    with pytest.raises(OSError):
        await ws.service.analyze([str(ws.root)])
    assert all(f.exists() for f in ws.files)


async def test_analysis_that_completes_instead_of_requiring_review_is_refused(
        workspace):
    """Мы просили ревью. `completed` в ответ — не удача, а нарушение контракта:
    сайдкар мог что-то применить."""
    ws = workspace
    ws.sidecar.analyze_status = "completed"
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.FAILED.value
    assert job.refusal == Refusal.PROTOCOL_FAILED.value


# ------------------------------------------------------------------ §5 поток

async def test_review_produces_a_plan_and_mutates_nothing(workspace):
    """PROPOSAL != AUTHORIZATION. Анализ ничего не двигает."""
    ws = workspace
    before = {str(f): f.read_text() for f in ws.files}
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.REVIEW_REQUIRED.value
    assert len(job.envelope["entries"]) == 3
    assert all(Path(p).exists() and Path(p).read_text() == text
               for p, text in before.items()), "анализ изменил файлы"
    assert not any(e["selected"] for e in job.envelope["entries"])


async def test_apply_moves_only_the_selected_subset(workspace):
    """Владелец утвердил подмножество — двигается ровно оно."""
    ws = workspace
    chosen = str(ws.files[0])
    job = await _approved(ws, selected=[chosen])
    assert job.state == JobState.VERIFIED.value, job.detail
    assert not Path(chosen).exists(), "выбранный файл не переехал"
    assert ws.files[1].exists() and ws.files[2].exists(), "тронуты невыбранные файлы"
    assert job.receipt["effect_count"] == 1


async def test_receipt_is_produced_only_after_independent_observation(workspace):
    """PROCESS_EXIT_0 != VERIFIED_EFFECT.

    Сайдкар отчитался `completed`, но НИЧЕГО не сделал. Квитанция обязана это
    заметить, потому что смотрит на файловую систему, а не в отчёт.
    """
    ws = workspace
    ws.sidecar.effect = None                     # процесс «успешен», эффекта нет
    job = await _approved(ws)
    assert job.state == JobState.VERIFICATION_FAILED.value
    assert job.refusal == Refusal.POST_STATE_MISMATCH.value
    assert job.receipt["effect_count"] == 0
    assert all(f.exists() for f in ws.files)


async def test_content_is_verified_not_just_the_name(workspace):
    """Файл на месте с правильным именем и ЧУЖИМ содержимым — не успех."""
    ws = workspace

    def wrong_content(flags):
        fake.move_effect(flags)
        for moved in (ws.root / "Documents").glob("*.pdf"):
            moved.write_text("substituted after the move\n", encoding="utf-8")

    ws.sidecar.effect = wrong_content
    job = await _approved(ws)
    assert job.state == JobState.VERIFICATION_FAILED.value
    assert all(not e["content_sha256_matches"] for e in job.receipt["applied"])


async def test_unexpected_mutations_inside_scope_fail_verification(workspace):
    """Применение, которое «заодно» тронуло соседний файл, не является успехом."""
    ws = workspace

    def greedy(flags):
        fake.move_effect(flags)
        (ws.root / "bystander.txt").write_text("I was not in the plan\n",
                                               encoding="utf-8")

    ws.sidecar.effect = greedy
    job = await _approved(ws)
    assert job.state == JobState.VERIFICATION_FAILED.value
    assert any("bystander" in m for m in job.receipt["unexpected_mutations"])


# ------------------------------------------------------------------ §12 stale

@pytest.mark.parametrize("mutate,label", [
    (lambda p: p.write_text("completely different contents\n"), "contents changed"),
    (lambda p: p.unlink(), "source deleted"),
])
async def test_changed_source_refuses_the_whole_apply(workspace, mutate, label):
    ws = workspace
    job = await ws.service.analyze([str(ws.root)])
    mutate(ws.files[0])
    with pytest.raises(Denied) as denied:
        await ws.service.apply(job.job_id, [str(f) for f in ws.files])
    assert denied.value.refusal is Refusal.STALE_REVIEW_PLAN, label
    # отказ на ВСЮ единицу: не переехал ни один файл, включая невиновные
    assert ws.files[1].exists() and ws.files[2].exists()


async def test_same_size_different_contents_with_restored_mtime_is_still_stale(
        workspace):
    """Ровно тот случай, который поверхностная проверка пропускает.

    Размер тот же, mtime восстановлен — отличить можно только содержимым,
    поэтому §12 пересчитывает sha256, а не сравнивает метаданные.
    """
    import os
    ws = workspace
    victim = ws.files[0]
    original = victim.read_text()
    stat = victim.stat()

    job = await ws.service.analyze([str(ws.root)])

    replacement = "X" * len(original)
    assert len(replacement) == len(original)
    victim.write_text(replacement, encoding="utf-8")
    os.utime(victim, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert victim.stat().st_size == stat.st_size
    assert victim.stat().st_mtime_ns == stat.st_mtime_ns

    with pytest.raises(Denied) as denied:
        await ws.service.apply(job.job_id, [str(f) for f in ws.files])
    assert denied.value.refusal is Refusal.STALE_REVIEW_PLAN
    assert ws.files[1].exists()


async def test_source_replaced_by_a_symlink_pointing_inside_is_stale(workspace):
    """§12 — источник стал ссылкой, но осталась внутри разрешённого корня.

    Проверка области здесь пропускает (цель законна), поэтому поймать подмену
    обязан именно stale-контроль: файл, который утверждал владелец, больше не
    тот файл.
    """
    ws = workspace
    job = await ws.service.analyze([str(ws.root)])
    inside = ws.root / "decoy.txt"
    inside.write_text("still inside the root\n", encoding="utf-8")
    victim = ws.files[0]
    victim.unlink()
    victim.symlink_to(inside)
    with pytest.raises(Denied) as denied:
        await ws.service.apply(job.job_id, [str(f) for f in ws.files])
    assert denied.value.refusal is Refusal.STALE_REVIEW_PLAN
    assert ws.files[1].exists() and ws.files[2].exists()


async def test_source_replaced_by_a_symlink_pointing_outside_is_contained(workspace):
    """Тот же приём, но ссылка ведёт НАРУЖУ. Ловится раньше и строже —
    это побег из области, а не просто устаревший план."""
    ws = workspace
    job = await ws.service.analyze([str(ws.root)])
    outside = ws.tmp / "outside.txt"
    outside.write_text("elsewhere\n", encoding="utf-8")
    victim = ws.files[0]
    victim.unlink()
    victim.symlink_to(outside)
    with pytest.raises(Denied) as denied:
        await ws.service.apply(job.job_id, [str(f) for f in ws.files])
    assert denied.value.refusal in (Refusal.STALE_REVIEW_PLAN,
                                    Refusal.SYMLINK_ESCAPE)
    assert outside.read_text() == "elsewhere\n", "цель ссылки не должна быть тронута"
    assert ws.files[1].exists() and ws.files[2].exists()


async def test_review_file_edited_after_approval_is_refused_by_digest(workspace):
    """§11 — дайджест привязывает решение владельца к содержимому файла плана."""
    ws = workspace
    job = await ws.service.analyze([str(ws.root)])
    review = ws.service.jobs_dir / job.job_id / "review.json"
    tampered = json.loads(review.read_text())
    tampered["entries"][0]["suggestedName"] = "owned.pdf"
    review.write_text(json.dumps(tampered), encoding="utf-8")

    with pytest.raises(Denied) as denied:
        await ws.service.apply(job.job_id, [str(ws.files[0])])
    assert denied.value.refusal is Refusal.REVIEW_PLAN_DIGEST_MISMATCH
    assert ws.files[0].exists()


# ------------------------------------------------------------------ §13 назначение

async def test_destination_collision_is_refused_not_judged_safe(workspace):
    ws = workspace
    job = await ws.service.analyze([str(ws.root)])
    existing = ws.root / "Documents"
    existing.mkdir(parents=True, exist_ok=True)
    (existing / ws.files[0].name).write_text("already here\n", encoding="utf-8")
    with pytest.raises(Denied) as denied:
        await ws.service.apply(job.job_id, [str(ws.files[0])])
    assert denied.value.refusal is Refusal.DESTINATION_COLLISION


def test_duplicate_planned_destinations_are_refused(tmp_path):
    from bcc.file_intelligence.models import PlanEntry
    root = tmp_path / "root"
    root.mkdir()
    policy = ScopePolicy(authorized_roots=[root])
    entries = [
        PlanEntry(file_path=str(root / "a.pdf"), file_name="a.pdf",
                  entry_type="file", destination=str(root / "out" / "same.pdf"),
                  selected=True),
        PlanEntry(file_path=str(root / "b.pdf"), file_name="b.pdf",
                  entry_type="file", destination=str(root / "out" / "same.pdf"),
                  selected=True),
    ]
    with pytest.raises(Denied) as denied:
        verify.check_destinations(entries, policy)
    assert denied.value.refusal is Refusal.DUPLICATE_PLANNED_DESTINATION


def test_case_folding_collision_is_caught_on_every_platform(tmp_path):
    """На Windows и macOS `Report.pdf` и `report.pdf` — один файл.

    Ловится всегда, а не только там, где ФС нечувствительна к регистру: иначе
    план, зелёный в CI на Linux, тихо перезаписал бы файл у владельца.
    """
    from bcc.file_intelligence.models import PlanEntry
    root = tmp_path / "root"
    root.mkdir()
    policy = ScopePolicy(authorized_roots=[root])
    entries = [
        PlanEntry(file_path=str(root / "a.pdf"), file_name="a.pdf",
                  entry_type="file", destination=str(root / "Report.pdf"),
                  selected=True),
        PlanEntry(file_path=str(root / "b.pdf"), file_name="b.pdf",
                  entry_type="file", destination=str(root / "report.pdf"),
                  selected=True),
    ]
    with pytest.raises(Denied) as denied:
        verify.check_destinations(entries, policy)
    assert denied.value.refusal is Refusal.DUPLICATE_PLANNED_DESTINATION


@pytest.mark.parametrize("name,refusal", [
    ("CON.txt", Refusal.RESERVED_FILENAME),
    ("com1.pdf", Refusal.RESERVED_FILENAME),
    ("", Refusal.INVALID_FILENAME),
    ("..", Refusal.INVALID_FILENAME),
    ("a/b.pdf", Refusal.INVALID_FILENAME),
    ("trailing .", Refusal.INVALID_FILENAME),
    ("x" * 300 + ".pdf", Refusal.PATH_TOO_LONG),
])
def test_unsafe_planned_names_are_refused(name, refusal):
    from bcc.file_intelligence.verify import _check_name
    with pytest.raises(Denied) as denied:
        _check_name(name)
    assert denied.value.refusal is refusal


def test_legitimate_names_are_not_refused():
    """Негативный контроль к предыдущему: строгость не должна ломать обычное.

    Unicode, пробелы и точки в имени — нормальные файлы владельца, и отказ им
    был бы такой же ошибкой, как пропуск `CON.txt`.
    """
    from bcc.file_intelligence.verify import _check_name
    for name in ("отчёт за сентябрь.pdf", "vacation photo 2026.jpg",
                 "archive.tar.gz", "конспект — копия.docx", "CONtract.pdf",
                 "file[1].png", "résumé.pdf"):
        _check_name(name)                          # не поднимает


# ------------------------------------------------------------------ §16 приватность

@pytest.mark.parametrize("value,expected", [
    ("Local_7b_Gemma", BackendMode.LOCAL),
    ("Local_3b", BackendMode.LOCAL),
    ("Remote_OpenAI", BackendMode.REMOTE),
    ("Remote_Custom", BackendMode.REMOTE),
    ("Custom", BackendMode.UNKNOWN),
    ("Unset", BackendMode.UNKNOWN),
    ("", BackendMode.UNKNOWN),
    ("Local_from_the_future", BackendMode.UNKNOWN),
])
def test_backend_classification_is_bossmans_own(value, expected):
    """`Custom` — ловушка, ради которой классификация своя.

    Upstream считает локальным всё, что не в `is_remote_choice()`
    (Types.hpp:18-21), а `Custom` туда не входит — то есть произвольный
    OpenAI-совместимый эндпоинт проходит у него как локальный. Здесь он
    UNKNOWN, потому что «мы не знаем, куда это ходит» — не «это локально».
    """
    from bcc.file_intelligence.privacy import classify
    assert classify(value) is expected


@pytest.mark.parametrize("mode,approved,ok", [
    (BackendMode.LOCAL, False, True),
    (BackendMode.REMOTE, True, True),
    (BackendMode.REMOTE, False, False),
    (BackendMode.UNKNOWN, False, False),
    (BackendMode.UNKNOWN, True, False),
])
def test_processing_is_fail_closed(mode, approved, ok):
    """UNKNOWN не проходит даже с одобрением: одобрять нечего, пока неизвестно,
    куда уйдут данные."""
    from bcc.file_intelligence.privacy import assert_processing_permitted
    if ok:
        assert_processing_permitted(mode, remote_approved=approved)
    else:
        with pytest.raises(Denied):
            assert_processing_permitted(mode, remote_approved=approved)


async def test_remote_backend_without_approval_denies_before_any_process(workspace):
    ws = workspace
    ws.service._config_path = fake.local_config(ws.tmp, "Remote_OpenAI")
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.DENIED.value
    assert job.refusal == Refusal.REMOTE_BACKEND_NOT_APPROVED.value
    assert ws.sidecar.calls == [], "процесс не должен был запускаться"


async def test_missing_config_is_unknown_not_assumed_local(workspace):
    """Локальность НЕ выводится из того, что запустился локальный бинарь."""
    ws = workspace
    ws.service._config_path = ws.tmp / "does-not-exist.ini"
    job = await ws.service.analyze([str(ws.root)])
    assert job.state == JobState.DENIED.value
    assert job.refusal == Refusal.BACKEND_UNKNOWN.value
    assert ws.sidecar.calls == []


# ------------------------------------------------------------------ §18 доктор

def test_missing_binary_is_named_not_installed(monkeypatch, tmp_path):
    from bcc.file_intelligence import discovery
    monkeypatch.setenv(discovery.EXECUTABLE_ENV, str(tmp_path / "nope"))
    monkeypatch.setattr(discovery.shutil, "which", lambda _n: None)
    found = discovery.discover(probe=False)
    assert found.status.value == "NOT_INSTALLED"
    assert found.pinned_upstream_sha_expected == discovery.pinned_sha()


def test_a_found_binary_is_not_called_pinned(tmp_path):
    """§18 — манифест пиннирован, сборка — отдельный вопрос.

    Назвать бинарь пиннированным потому, что пиннирован документ рядом с ним,
    значит подменить проверку ссылкой на самих себя.
    """
    found = fake.fake_discovery(fake.make_executable(tmp_path))
    assert found.version_verified is False
    assert found.binary_version == fi.VERSION_UNVERIFIED
    assert found.pinned_upstream_sha_expected == "4dc374df69b5e63d5354e121097d92e25bbd32da"


def test_manifest_is_found_where_a_wheel_puts_it_before_the_checkout(monkeypatch, tmp_path):
    """§18/§19 — установленный продукт обязан называть тот же пин.

    Единственный прежний путь вёл от модуля к `parents[3]/integrations/...`. В
    чекауте это корень репозитория, в site-packages — каталог интерпретатора, и
    манифест там не лежит: `manifest()` мягко возвращал `{}`, а установленный
    Bossman сообщал ПУСТОЙ pinned_upstream_sha. Теперь сначала смотрится копия,
    которую кладёт сборка колеса.
    """
    from bcc.file_intelligence import discovery

    packaged = tmp_path / "_integrations" / "ai-file-sorter" / "integration.json"
    packaged.parent.mkdir(parents=True)
    packaged.write_text(json.dumps({"pinned_sha": "packaged-copy-wins"}), encoding="utf-8")
    checkout = tmp_path / "checkout.json"
    checkout.write_text(json.dumps({"pinned_sha": "checkout-copy"}), encoding="utf-8")

    monkeypatch.setattr(discovery, "_PACKAGED_MANIFEST", packaged)
    monkeypatch.setattr(discovery, "_CHECKOUT_MANIFEST", checkout)
    assert discovery.manifest_path() == packaged

    # Колесо без своей копии всё ещё читает чекаут — редактируемая установка.
    monkeypatch.setattr(discovery, "_PACKAGED_MANIFEST", tmp_path / "absent.json")
    assert discovery.manifest_path() == checkout

    # И отсутствие обоих не выдаётся за найденный манифест.
    monkeypatch.setattr(discovery, "_CHECKOUT_MANIFEST", tmp_path / "also-absent.json")
    assert discovery.manifest_path() is None


def test_manifest_records_the_verified_upstream_pin():
    from bcc.file_intelligence.discovery import manifest
    document = manifest()
    assert document["pinned_sha"] == "4dc374df69b5e63d5354e121097d92e25bbd32da"
    assert document["license"] == "AGPL-3.0-or-later"
    assert document["integration_mode"] == "external_sidecar"
    assert document["local_only_default"] is True
    assert document["auto_apply_allowed"] is False
    assert document["protocol"]["headless_undo"] == "UNAVAILABLE"


# ------------------------------------------------------------------ §15 undo

def test_undo_is_reported_unavailable_rather_than_faked():
    """У upstream на закреплённом SHA нет headless-контракта отмены: в
    HeadlessAnalysisCommand.cpp `undo_dir` только передаётся в apply-опции,
    флага `--undo` не существует. Заявить undo в API значило бы пообещать
    операцию, которой нет."""
    assert fi.UNDO_HEADLESS == "UNAVAILABLE"
    assert not any(hasattr(FileIntelligenceService, name)
                   for name in ("undo", "revert", "rollback"))


# ------------------------------------------------------------------ §9 предел

async def test_unrelated_folders_are_split_not_silently_widened(tmp_path):
    """§9 — headless берёт одну папку или файлы одного родителя.

    Разнородный выбор не превращается в широкий обход диска: это подменило бы
    «разложи вот эти два файла» на «пройди по всему, что найдёшь».
    """
    first, second = tmp_path / "Downloads", tmp_path / "Photos"
    a = fake.make_corpus(first, 1)[0]
    b = fake.make_corpus(second, 1)[0]
    policy = ScopePolicy(authorized_roots=[first, second])
    with pytest.raises(Denied) as denied:
        policy.single_parent([str(a), str(b)])
    assert denied.value.refusal is Refusal.MULTIPLE_PARENT_FOLDERS

    # законный случай: файлы одного родителя
    more = fake.make_corpus(first, 2)
    root, resolved = policy.single_parent([str(p) for p in more])
    assert root == first.resolve() and len(resolved) == 2
