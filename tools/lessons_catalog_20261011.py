"""Build docs/owner/bossman_lessons_20261011.json — the lessons of 11.10 for Bossman memory (owner: «обучай
Bossman всему, чему научились; упор на его контекст и память»). Same schema and import path as 10.10:
  python tools/lessons_catalog_20261011.py
  BOSSMAN_SKILL_RECORDING=1 python tools/import_operational_lessons.py --catalog docs/owner/bossman_lessons_20261011.json --data-dir <...>/learning/apprentice
Every entry is CANDIDATE until an external verifier exists; evidence sha256 is computed here (CRLF->LF).
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "owner" / "bossman_lessons_20261011.json"
L: list[dict] = []


def dig(rel: str) -> str:
    return hashlib.sha256((ROOT / rel).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def ev(*paths: str) -> list[dict]:
    return [{"path": p, "sha256": dig(p), "kind": "doc" if p.endswith(".md") else "code"} for p in paths]


def cand(reason: str) -> dict:
    return {"state": "CANDIDATE", "reason": reason}


def add(id_, cls, title, sev, status, symptom, ex, rx, kw, det, cause, fix, prev, evidence, ver):
    L.append({"id": id_, "class": cls, "title": title, "severity": sev, "status": status, "symptom": symptom,
              "symptom_examples": ex, "symptom_regex": rx, "keywords": kw, "detection": det, "root_cause": cause,
              "fix": fix, "prevention": prev, "evidence": evidence, "verification": ver})


NIGHT = "tools/night_autonomy.py"
NIGHT_T = "tests/test_night_autonomy.py"
REMOTE = "command-center/bcc/telegram_companion/remote_access.py"
REMOTE_T = "command-center/tests/test_companion_remote_access.py"
AUDIT = "docs/owner/PRODUCTION_READINESS_AUDIT_RU.md"
SEC = "tools/security_audit.ps1"
KEEPER = "tools/claude_remote_service.ps1"
POKER_UI = "command-center/ui/pages/poker_vision.js"

add("PULT-OLD-BUILD-REJECTS-NEW-CONFIG-KEYS", "config-compat", "Установленный пульт старой сборки падает на новых ключах config.json",
    "HIGH", "FIXED",
    "После добавления ключа в telegram-companion/config.json установленный пульт и owner_report падают при загрузке настроек.",
    ["TypeError: Settings.__init__() got an unexpected keyword argument 'remote_control_name'"],
    [r"Settings\.__init__\(\) got an unexpected keyword argument"],
    ["config.json", "companion", "пульт", "Settings", "unexpected keyword", "совместимость сборок"],
    "Загрузить config.json кодом установленной сборки (owner_report --file x) до и после правки.",
    "Settings(**data) строгий; один config.json читают и установленная сборка, и исходники новее неё.",
    "Не добавлять ключи в общий config.json; новые параметры — константы модуля или существующие поля (claude_permission_mode). Откат из config.json.bak-*.",
    "Перед правкой общего конфига: проверка загрузки установленной сборкой; новые ключи — только вместе с установкой новой сборки.",
    ev(REMOTE, REMOTE_T), cand("наблюдение 11.10, откат из бэкапа; внешний верификатор не оформлен"))

add("TEST-PATCHED-OS-NAME-WINDOWSPATH", "ci-portability", "Подмена os.name в тесте ломает pathlib на Linux CI",
    "HIGH", "FIXED",
    "Тесты зелёные на Windows, на Linux-раннерах падают: pathlib создаёт WindowsPath.",
    ["NotImplementedError: cannot instantiate 'WindowsPath' on your system", "RuntimeError: Could not determine home directory."],
    [r"cannot instantiate 'WindowsPath' on your system", r"pathlib\.UnsupportedOperation: cannot instantiate"],
    ["os.name", "monkeypatch", "WindowsPath", "pathlib", "linux ci", "кроссплатформенность"],
    "grep monkeypatch.setattr(.*os, \"name\" в tests/.",
    "pathlib.Path выбирает класс по os.name; подмена os.name на 'nt' в Linux-процессе даёт WindowsPath.",
    "Модульный флаг IS_WINDOWS = os.name == 'nt'; тесты подменяют флаг, а не os.name.",
    "Никогда не подменять os.name/sys.platform в тестах; платформенные ветки через флаг модуля.",
    ev(REMOTE, REMOTE_T), cand("исправлено в 4c29b274, Command Center CI зелёный на py3.11/3.12/3.14"))

add("VERIFY-ARCHIVE-AGAINST-ITS-OWN-SHA", "release", "Архив проверялся против HEAD, а не против SHA, из которого собран",
    "HIGH", "FIXED",
    "Верификатор архива падает, как только в ветке появляется любой новый коммит после сборки.",
    ["verify_windows_bundle --expected-sha <HEAD> на архиве другого SHA"],
    [r"expected-sha", r"source_sha .* differs"],
    ["verify_windows_bundle", "expected-sha", "MANIFEST", "source_sha", "архив", "сборка"],
    "Сравнить MANIFEST.json/source_sha архива с HEAD до проверки.",
    "Ожидаемый SHA брался из git rev-parse HEAD; HEAD уходит вперёд от коммита сборки.",
    "Ожидаемый SHA = source_sha из MANIFEST.json архива; при изменении кода после сборки — пересборка из чистого worktree.",
    "Кэш результатов проверки — по ключу SHA256 архива + source SHA + installed SHA.",
    ev(NIGHT, NIGHT_T), cand("найдено и исправлено до первого ночного прогона; 34 теста"))

add("WINDOWS-TIMEOUT-HANGS-ON-GRANDCHILDREN", "process", "subprocess.run(timeout) зависает на Windows из-за внуков-процессов",
    "HIGH", "FIXED",
    "Шаг с таймаутом не возвращается: убит только прямой потомок, бэкенд/браузер-внуки держат pipe.",
    ["процесс не вернулся после timeout", "communicate() висит после kill"],
    [r"TimeoutExpired", r"(?i)hang.*after.*timeout"],
    ["subprocess", "timeout", "taskkill", "process tree", "pipe", "windows", "дерево процессов"],
    "Запустить дочерний процесс, который сам стартует sleep 60, с timeout=3: возврат дольше 45 с — дефект.",
    "capture_output использует pipe; после kill родителя внуки держат его открытым, communicate ждёт EOF.",
    "Вывод в файл, CREATE_NEW_PROCESS_GROUP, по таймауту taskkill /T /F (POSIX: killpg), ограниченное ожидание.",
    "Для любых долгих шагов, которые стартуют сервер или браузер, — только run() с убийством дерева.",
    ev(NIGHT, NIGHT_T), cand("реальный тест с внуком проходит на Windows; найдено независимым ревью"))

add("AGENT-ANSWER-IS-A-CLAIM-NOT-A-PROOF", "autonomy", "Ответ агента «исправил» — заявка, а не доказательство",
    "HIGH", "FIXED",
    "Ночной цикл засчитывал успешный ответ Claude как исправление без пересборки и замера.",
    ["Claude: done", "result is_error=false без нового коммита"],
    [r"(?i)claim[_ ]only|fix[_ ]proven"],
    ["claim", "proof", "fix_proven", "claude", "агент", "доказательство", "north star"],
    "Проверить: был ли новый коммит с кодом, пересобран ли архив, пройден ли замер на нём.",
    "Успешный код возврата агента не связан с состоянием продукта.",
    "Статусы CLAUDE_ANSWERED / UNAVAILABLE / FAILED отдельно от FIX_PROVEN; FIX_PROVEN только на пересобранном архиве с меньшим числом ошибок UX и не хуже по verify/evening.",
    "Любая метрика самоулучшения считает только FIX_PROVEN; CLAIM_ONLY и NOT_PROVEN — отдельно.",
    ev(NIGHT, NIGHT_T), cand("логика и тесты есть; первый реальный FIX_PROVEN ещё не получен"))

add("PS51-NEEDS-UTF8-BOM-FOR-CYRILLIC", "powershell", "PowerShell 5.1 ломает разбор скрипта с кириллицей без BOM",
    "MEDIUM", "FIXED",
    "Скрипт .ps1 с русскими строками падает ParserError в Windows PowerShell 5.1, хотя в pwsh 7 работает.",
    ["Missing closing '}' in statement block", "Unexpected token 'РЅРµ'"],
    [r"Missing closing '\}' in statement block", r"Unexpected token '.*Р[Ѓ-я]"],
    ["powershell 5.1", "utf-8", "bom", "кириллица", "ParserError", "ps1"],
    "Кракозябры вида РЅРµ в тексте ошибки разбора.",
    "PS 5.1 читает UTF-8 без BOM как ANSI-кодировку системы.",
    "Сохранять .ps1 с кириллицей в UTF-8 с BOM.",
    "Проверка .ps1 в CI: файлы с не-ASCII символами обязаны начинаться с BOM.",
    ev(SEC), cand("наблюдение 11.10, аудит безопасности после BOM отработал"))

add("PS-STRING-VAR-FOLLOWED-BY-COLON", "powershell", "\"$var:\" в строке PowerShell — ошибка разбора",
    "LOW", "FIXED",
    "Скрипт не запускается: Variable reference is not valid. ':' was not followed by a valid variable name character.",
    ["Log \"attempt $attempt: starting\""],
    [r"Variable reference is not valid\. ':' was not followed"],
    ["powershell", "variable", "colon", "drive", "строка"],
    "Найти в .ps1 шаблон \\$\\w+: внутри двойных кавычек.",
    "PowerShell трактует $name: как переменную с указанием диска.",
    "Писать ${attempt}: .",
    "Перед запуском .ps1 — разбор Parser::ParseFile без ошибок.",
    ev(KEEPER), cand("наблюдение 11.10"))

add("REMOTE-CONTROL-ONE-PER-FOLDER", "remote-access", "Claude Remote Control: один экземпляр на папку",
    "MEDIUM", "ENV",
    "Второй claude remote-control в той же папке завершается сразу с кодом 1.",
    ["Error: This folder is already served by a terminal `claude remote-control` on this device. Stop it first."],
    [r"already served by a terminal `claude remote-control`"],
    ["remote-control", "claude", "remote", "удалённый доступ", "дубликат"],
    "Перед запуском — процессы с remote-control и --name в командной строке.",
    "CLI сам запрещает второй сервер на ту же папку.",
    "Keeper проверяет живой процесс по имени сессии и не запускает второй; пульт /remote отдаёт PID и ссылку.",
    "Не запускать Remote Control поверх действующего; /remote_status до /remote.",
    ev(KEEPER, REMOTE), cand("реальный прогон keeper'а 11.10 15:33"))

add("PUSH-CANCELS-RUNNING-CI", "ci", "Push в ветку отменяет идущий Command Center CI предыдущего SHA",
    "MEDIUM", "ENV",
    "Command Center CI на SHA помечен cancelled; зелёного CC CI нет ни на одном SHA.",
    ["Command Center CI cancelled"],
    [r"(?i)command center ci.*cancelled"],
    ["ci", "concurrency", "cancelled", "push", "github actions"],
    "gh run list: conclusion=cancelled у Command Center CI сразу после нового push.",
    "workflow с concurrency cancel-in-progress на ветку.",
    "Не пушить, пока идёт CC CI нужного SHA; копить локальные коммиты и пушить после завершения.",
    "Монитор CI точного SHA до push; документы аудита коммитить пачкой.",
    ev(AUDIT), cand("наблюдение 11.10 на 9520a45a и 4f4e7183"))

add("TREE-PUBLISH-NEEDS-COMMITTED-REGISTRY-ABS-PATH", "tree", "Публикация дерева не видит реестр: git-снимок и относительный путь",
    "MEDIUM", "FIXED",
    "Сайт дерева опубликован, но CI-улик на узлах нет; в выводе REGISTRY=UNREADABLE.",
    ["REGISTRY=UNREADABLE", "источник git-snapshot"],
    [r"REGISTRY=UNREADABLE"],
    ["tree", "дерево", "registry", "tree_publish_site", "git-snapshot", "github pages"],
    "Проверить, что на сайте у листа есть GREEN_ON_SHA и SHA; Last-Modified Pages после сборки.",
    "Публикатор берёт git-снимок HEAD (незакоммиченный реестр не виден) и резолвит --registry от папки сайта.",
    "Сначала коммит реестра, потом tree_publish_site.py --registry <абсолютный путь> --push; дождаться pages build=built.",
    "После публикации — проверка живого JSON узла по сети, а не только вывода публикатора.",
    ev(AUDIT), cand("11.10: 471 лист GREEN_ON_SHA виден на живом сайте после второй публикации"))

add("INSTALLED-UI-SWEEP-SLOWER-THAN-CAP", "ux", "Обход интерфейса установленной сборки на ПК владельца в 3 раза дольше лимита",
    "MEDIUM", "ENV",
    "Вечерний тест сборки: installed_ui_sweep TIMEOUT 900 с → OWNER_EVENING_RESULT=FAIL, хотя на CI-раннере укладывается.",
    ["OWNER_EVENING_REASON=ui-sweep_timeout [fail]: installed_ui_sweep.py did not finish within 900 s"],
    [r"installed_ui_sweep\.py did not finish within \d+ s"],
    ["ui sweep", "evening test", "timeout", "обход интерфейса", "приёмка"],
    "Запустить installed_ui_sweep.py отдельно без лимита и замерить время (11.10: 2693 с, 630 кликов).",
    "Обход кликает 630 элементов с ожиданиями; ПК владельца загружен и медленнее CI-раннера.",
    "Ночной раннер запускает обход отдельно с лимитом 75 мин; результат — counts и review-пункты, а не таймаут.",
    "Не считать таймаут обхода ни PASS, ни регрессией: перезапуск без лимита.",
    ev(NIGHT, AUDIT), cand("замер 11.10; CI One-download с тем же обходом PASS"))

add("DISABLED-BUTTON-MUST-SAY-WHY", "ux", "Кнопка, которая сейчас не может сработать, должна быть выключена с причиной",
    "MEDIUM", "FIXED",
    "Обход UI: кнопки шлют запросы, получающие 403/400 по замыслу, или выключены без объяснения (disabled_silent).",
    ["403 /api/poker-vision/desk/sandbox/cover", "400 /api/poker-vision/calibrate", "disabled_silent"],
    [r"REVIEW (error|disabled_silent)"],
    ["ui", "disabled", "title", "403", "400", "disabled_silent", "кнопка", "подсказка"],
    "Классы ui_acceptance_sweep: error с 4xx по замыслу и disabled_silent.",
    "Предусловие проверялось только сервером; страница не знала о нём.",
    "Страница выключает кнопку и пишет причину в title, пока предусловие не выполнено; серверная проверка остаётся.",
    "Каждое предусловие действия — в состоянии кнопки на странице (класс disabled_reason).",
    ev(POKER_UI), cand("исправлено 71ee3c5d/eb90062e; доказательство — повторный обход ночью"))

add("FREE-MODEL-EMPTY-CONTENT", "model-routing", "Бесплатная/облачная модель вернула пустой content",
    "LOW", "ENV",
    "Ревью от GLM 5.3 Flash пришло пустым; у Mistral Large 4 текст лежал в thinking-блоках.",
    ["content: None", "[{'type': 'thinking', ...}]"],
    [r"'content':\s*None", r"'type':\s*'thinking'"],
    ["openrouter", "glm", "mistral", "reasoning", "empty content", "пустой ответ"],
    "Пустой content при ненулевых токенах ответа.",
    "Рассуждающие модели кладут текст в reasoning/thinking; параметр reasoning у части провайдеров даёт 400.",
    "Читать content, затем reasoning/thinking; при пустом — следующая модель в маршруте.",
    "Маршрутизатор считает пустой content отказом модели, а не ответом.",
    ev(AUDIT), cand("наблюдение 11.10 на ревью diff"))

add("WINDOWS-LOCAL-E2E-NOT-A-REGRESSION", "ci-portability", "Локальный провал e2e на Windows — сравнить с базовым коммитом до вывода о регрессии",
    "LOW", "OPEN",
    "test_replay_source_shows_live_stream… падает локально на Windows (hero_cards UNKNOWN), а на Linux CI проходит.",
    ["assert '6d' in 'hero_cards[0]\tUNKNOWN"],
    [r"hero_cards\[0\]\s+UNKNOWN"],
    ["poker-vision", "e2e", "windows", "regression", "регрессия", "базовый коммит"],
    "Прогнать тот же тест на предыдущем зелёном SHA в отдельном worktree.",
    "Распознавание по образцам чувствительно к окружению; падение воспроизводится и на 4c29b274.",
    "Записать как окружение Windows, не откатывать чужой коммит; разбор — отдельной задачей Poker.",
    "Перед обвинением коммита — тот же тест на базовом SHA.",
    ev(POKER_UI), cand("11.10: падает на 4c29b274 и 21fadc22 локально, CI Linux зелёный"))


def main() -> int:
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    cat = {"schema": "bossman.critical_errors.v1",
           "title": "Уроки дня 2026-10-11: удалённый доступ, ночная автономность, сборка, CI, дерево, UX",
           "captured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "observed_head_sha": head,
           "environment": "owner-machine Windows 11 Pro 10.0.26200, Ryzen AI MAX+ 395; GitHub Actions ubuntu/windows",
           "recorded_by": {"principal_id": "agent:claude-coordinator", "model": "claude-opus-5-5", "agent": "coordinator",
                           "run_id": "lessons-20261011"},
           "privacy_class": "internal-owner-ops", "scope": "owner-machine/windows-11 + github-actions",
           "notes": ["Каждый урок CANDIDATE, пока нет внешнего верификатора; доказательства — файлы репозитория с sha256."],
           "entries": L}
    OUT.write_bytes((json.dumps(cat, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
    print("entries", len(L), "->", OUT.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
