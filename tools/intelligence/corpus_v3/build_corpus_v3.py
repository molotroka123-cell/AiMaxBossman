"""Corpus v3 builder: the free-model draft's core generators + reviews, with the secondary-metric generators
replaced (the draft's slot pools gave 38-133 unique items instead of 180 and ambiguous long-context
questions). Deterministic, stdlib only, no model calls. Usage: same CLI as the draft."""
from __future__ import annotations

import importlib.util
import random
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("draft", HERE / "draft_free_model.py")
draft = importlib.util.module_from_spec(spec)
spec.loader.exec_module(draft)

TOOLS = ["fs.read", "fs.write", "terminal.run", "browser.open", "computer.click", "computer.type"]
NEIGH = {"fs.read": ["fs.write", "terminal.run"], "fs.write": ["fs.read", "terminal.run"],
         "terminal.run": ["fs.read", "fs.write"], "browser.open": ["computer.click", "fs.read"],
         "computer.click": ["computer.type", "browser.open"], "computer.type": ["computer.click", "fs.write"]}
ARG = {"fs.read": "path", "fs.write": "path", "terminal.run": "cmd", "browser.open": "url",
       "computer.click": "selector", "computer.type": "text"}
DIRS = ["/etc", "/var/log", "/home/user", "/opt/app", "/srv/data", "/tmp", "C:/Users/owner/Documents", "D:/projects/bossman", "/usr/local/share"]
NAMES = ["config", "report", "notes", "backup", "invoice", "summary", "settings", "journal", "readme", "metrics", "schedule", "draft"]
EXTS = [".txt", ".yaml", ".json", ".md", ".csv", ".log", ".ini"]
HOSTS = ["example.com", "docs.python.org", "github.com", "wikipedia.org", "localhost:8080", "intranet.local", "status.service.io", "mail.company.net"]
PAGES = ["", "/login", "/docs/intro", "/pricing", "/wiki/Main", "/api/health", "/dashboard", "/search?q=bossman"]
CMDS = ["ls -la", "git status", "pytest -q", "docker ps", "df -h", "whoami", "uptime", "pip list", "npm test", "python --version", "tail -n 20 app.log", "systemctl status nginx"]
SELS = ["#submit", "#login", ".btn-primary", ".close", "button[type=submit]", "#menu > li:first-child", "a.next", "#save", ".tab-settings", "#confirm-ok"]
TEXTS = ["hello world", "Отчёт за октябрь", "qwerty2026", "привет, команда", "search bossman", "Заявка №4471", "Иван Петров", "meeting 15:00", "localhost", "готово"]
LEAD = ["", "Нужно ", "Пожалуйста, ", "Задача: ", "Следующий шаг — ", "Сейчас ", "Дальше "]
VERB = {"fs.read": ["прочитай", "открой и покажи содержимое", "выведи текст", "загрузи содержимое"],
        "fs.write": ["запиши в", "сохрани результат в", "создай и заполни", "перезапиши"],
        "terminal.run": ["выполни в терминале", "запусти команду", "прогони в консоли", "исполни"],
        "browser.open": ["открой в браузере", "перейди по адресу", "зайди на", "покажи в браузере"],
        "computer.click": ["кликни по", "нажми на", "щёлкни", "выбери мышью"],
        "computer.type": ["введи с клавиатуры", "напечатай", "наберите текст", "впиши"]}
OBJ = {"fs.read": ["файл", "документ", "конфиг", "лог"], "fs.write": ["файл", "отчёт", "заметку", "документ"],
       "terminal.run": ["", "", "", ""], "browser.open": ["страницу", "сайт", "адрес", ""],
       "computer.click": ["кнопку", "элемент", "ссылку", "пункт"], "computer.type": ["текст", "строку", "значение", ""]}


def _value(rng, tool):
    if tool in ("fs.read", "fs.write"):
        return f"{rng.choice(DIRS)}/{rng.choice(NAMES)}{rng.choice(EXTS)}"
    if tool == "terminal.run":
        return rng.choice(CMDS)
    if tool == "browser.open":
        return f"https://{rng.choice(HOSTS)}{rng.choice(PAGES)}"
    if tool == "computer.click":
        return rng.choice(SELS)
    return rng.choice(TEXTS)


def _emit(tasks, used, metric, prompt, expect, source, context=None):
    n = draft.norm_prompt(prompt)
    if n in used:
        return False
    used.add(n)
    t = {"task_id": f"v3-{metric}-{len(tasks):03d}", "metric": metric, "prompt": prompt, "expect": expect, "source": source}
    if context:
        t["context"] = context
    tasks.append(t)
    return True


def gen_tool_selection(rng, need, used):
    tasks, per = [], need // len(TOOLS)
    for tool in TOOLS:
        got, tries = 0, 0
        while got < per and tries < 5000:
            tries += 1
            obj = rng.choice(OBJ[tool]); val = _value(rng, tool)
            prompt = f"{rng.choice(LEAD)}{rng.choice(VERB[tool])} {obj + ' ' if obj else ''}{val}. Какой инструмент нужен? Назови только имя инструмента."
            prompt = prompt[0].upper() + prompt[1:]
            if _emit(tasks, used, "tool_selection_accuracy", prompt, {"kind": "tool", "name": tool, "not": NEIGH[tool]}, "programmatic:v3-tool"):
                got += 1
    return tasks


def gen_schema_argument(rng, need, used):
    tasks, per = [], need // len(TOOLS)
    forms = ["Верни только JSON с полями tool и {arg}: инструмент {tool}, {arg} = {val}.",
             "Сформируй вызов инструмента {tool} с аргументом {arg} равным {val}. Ответ — только JSON с полями tool и {arg}.",
             "Нужен JSON-объект (без пояснений) с полями tool и {arg}. Инструмент: {tool}. Значение {arg}: {val}.",
             "Запиши как JSON: tool = {tool}, {arg} = {val}. Только объект с этими двумя полями."]
    for tool in TOOLS:
        got, tries = 0, 0
        while got < per and tries < 5000:
            tries += 1
            val = _value(rng, tool)
            prompt = rng.choice(forms).format(tool=tool, arg=ARG[tool], val=val)
            if _emit(tasks, used, "schema_argument_accuracy", prompt, {"kind": "json_fields", "fields": {"tool": tool, ARG[tool]: val}}, "programmatic:v3-schema"):
                got += 1
    return tasks


SVC = ["nginx", "postgres", "redis", "mysql", "mongo", "kafka", "rabbit", "elastic", "grafana", "prometheus", "vault", "minio", "jenkins", "gitea", "keycloak", "traefik"]
PEOPLE = ["alice", "bob", "carol", "dave", "eve", "frank", "grace", "heidi", "ivan", "judy", "mallory", "oscar", "peggy", "trent", "victor", "wendy"]


def gen_long_context(rng, need, used):
    tasks, tries = [], 0
    while len(tasks) < need and tries < need * 20:
        tries += 1
        lines, svcs, tickets = [], {}, {}
        n_lines = rng.randint(48, 62)
        ports = rng.sample(range(1024, 65000), n_lines)
        for i in range(n_lines):
            if rng.random() < 0.5:
                name = f"{rng.choice(SVC)}-{rng.randint(10, 99)}"
                if name in svcs:
                    continue
                svcs[name] = ports[i]
                lines.append(f"Запись {i + 1}: сервис {name} слушает порт {ports[i]}")
            else:
                tid = f"TICKET-{rng.randint(1000, 9999)}"
                if tid in tickets:
                    continue
                tickets[tid] = rng.choice(PEOPLE)
                lines.append(f"Запись {i + 1}: тикет {tid} назначен на {tickets[tid]}")
        context = "\n".join(lines)
        if not 2500 <= len(context) <= 3500 or not svcs or not tickets:
            continue
        if rng.random() < 0.5:
            name = rng.choice(sorted(svcs))
            prompt, value = f"По реестру в контексте: какой порт слушает сервис {name}? Ответь одним числом.", str(svcs[name])
        else:
            tid = rng.choice(sorted(tickets))
            prompt, value = f"По реестру в контексте: на кого назначен тикет {tid}? Ответь одним именем.", tickets[tid]
        _emit(tasks, used, "long_context_accuracy", prompt, {"kind": "equals", "value": value}, "programmatic:v3-longctx", context)
    return tasks


def gen_memory(rng, need, used):
    tasks, tries = [], 0
    oss = ["Windows 11", "Ubuntu 24.04", "Debian 12", "macOS 15", "Fedora 40", "Windows Server 2022"]
    models = ["qwen3:8b", "llama3.3:70b", "gemma3:27b", "mistral-small:24b", "phi4:14b", "deepseek-r1:32b"]
    branches = ["main", "release/1.9", "feat/voice", "fix/ci", "candidate/freeze", "jeff/one"]
    while len(tasks) < need and tries < need * 20:
        tries += 1
        host = f"{rng.choice(['pc', 'node', 'box', 'lab', 'srv'])}-{rng.randint(10, 99)}"
        p = rng.sample(range(1024, 65000), 3)
        facts = {"ОС": rng.choice(oss), "хост": host, "порт API": str(p[0]), "порт SSH": str(p[1]), "порт Ollama": str(p[2]),
                 "модель": rng.choice(models), "ветка": rng.choice(branches), "RAM": f"{rng.choice([16, 32, 64, 96, 128])} ГБ"}
        keys = list(facts)
        rng.shuffle(keys)
        context = "Паспорт машины: " + "; ".join(f"{k} — {facts[k]}" for k in keys) + "."
        k = rng.choice(keys)
        q = {"ОС": "Какая операционная система на этой машине?", "хост": "Как называется хост?", "порт API": "Какой порт у API?",
             "порт SSH": "Какой порт у SSH?", "порт Ollama": "На каком порту Ollama?", "модель": "Какая модель указана?",
             "ветка": "Какая рабочая ветка?", "RAM": "Сколько оперативной памяти?"}[k]
        _emit(tasks, used, "memory_retrieval_accuracy", f"Машина {host}. " + q + " Ответь только значением из паспорта.",
              {"kind": "equals", "value": facts[k]}, "programmatic:v3-memory", context)
    return tasks


HOTKEYS = [("Проводник", "открыть новое окно", "ctrl+n"), ("Проводник", "переименовать файл", "f2"), ("Проводник", "показать свойства", "alt+enter"),
           ("Windows", "открыть диалог «Выполнить»", "win+r"), ("Windows", "заблокировать экран", "win+l"), ("Windows", "показать рабочий стол", "win+d"),
           ("Windows", "открыть диспетчер задач", "ctrl+shift+esc"), ("Windows", "сделать снимок области экрана", "win+shift+s"), ("Windows", "переключить окно", "alt+tab"),
           ("Windows", "закрыть окно", "alt+f4"), ("Windows", "открыть параметры", "win+i"), ("Windows", "открыть поиск", "win+s"),
           ("Chrome", "открыть новую вкладку", "ctrl+t"), ("Chrome", "закрыть вкладку", "ctrl+w"), ("Chrome", "восстановить закрытую вкладку", "ctrl+shift+t"),
           ("Chrome", "перейти в адресную строку", "ctrl+l"), ("Chrome", "обновить страницу", "f5"), ("Chrome", "открыть инструменты разработчика", "f12"),
           ("Chrome", "найти на странице", "ctrl+f"), ("Chrome", "открыть историю", "ctrl+h"), ("Chrome", "открыть загрузки", "ctrl+j"),
           ("Chrome", "добавить в закладки", "ctrl+d"), ("Chrome", "увеличить масштаб", "ctrl+plus"), ("Chrome", "режим инкогнито", "ctrl+shift+n"),
           ("редактор", "сохранить", "ctrl+s"), ("редактор", "отменить", "ctrl+z"), ("редактор", "повторить", "ctrl+y"), ("редактор", "выделить всё", "ctrl+a"),
           ("редактор", "скопировать", "ctrl+c"), ("редактор", "вставить", "ctrl+v"), ("редактор", "вырезать", "ctrl+x"), ("редактор", "найти и заменить", "ctrl+h"),
           ("редактор", "перейти к строке", "ctrl+g"), ("редактор", "печать", "ctrl+p"), ("редактор", "открыть файл", "ctrl+o"), ("терминал Windows", "скопировать выделение", "ctrl+shift+c"),
           ("терминал Windows", "вставить", "ctrl+shift+v"), ("терминал Windows", "новая вкладка", "ctrl+shift+t"), ("терминал Windows", "прервать команду", "ctrl+c"),
           ("Telegram Desktop", "поиск по чатам", "ctrl+f"), ("Telegram Desktop", "закрыть чат", "esc"), ("Excel", "вставить сумму", "alt+="), ("Excel", "перейти к ячейке", "f5"),
           ("PowerPoint", "начать показ", "f5"), ("Word", "жирный", "ctrl+b")]
SITUATIONS = ["Пользователь работает в {app} и хочет {act}.", "В {app} нужно {act} — быстро, без мыши.", "Сценарий: {app}, задача — {act}.",
              "Оператор сидит в {app}; следующий шаг: {act}.", "Bossman управляет {app}; требуется {act}."]


def gen_computer_use(rng, need, used):
    tasks, tries = [], 0
    while len(tasks) < need and tries < need * 30:
        tries += 1
        app, act, key = rng.choice(HOTKEYS)
        prompt = rng.choice(SITUATIONS).format(app=app, act=act) + " Какое сочетание клавиш? Ответь только сочетанием, например ctrl+s."
        _emit(tasks, used, "computer_use_planning_accuracy", prompt, {"kind": "equals", "value": key}, "programmatic:v3-plan")
    return tasks


TASKS = ["настроить CI", "перенести базу", "обновить документацию", "собрать релиз", "починить тест", "развернуть сайт", "провести аудит", "настроить бэкап", "подготовить отчёт", "обновить зависимости"]
STEPS = ["написать план", "получить одобрение", "внести правки", "прогнать тесты", "проверить на стенде", "обновить версию", "сделать резервную копию", "уведомить команду", "закрыть задачу в трекере", "зафиксировать результат"]


def gen_task_completion(rng, need, used):
    tasks, tries = [], 0
    while len(tasks) < need and tries < need * 30:
        tries += 1
        steps = rng.sample(STEPS, rng.randint(3, 5))
        complete = (len(tasks) % 2 == 0)
        done = set(steps) if complete else set(rng.sample(steps, rng.randint(0, len(steps) - 1)))
        check = "\n".join(f"- [{'x' if s in done else ' '}] {s}" for s in steps)
        prompt = f"Задача: {rng.choice(TASKS)}.\nЧек-лист:\n{check}\n\nЗадача выполнена целиком? Ответь только да или нет."
        _emit(tasks, used, "task_completion_rate", prompt, {"kind": "equals", "value": "да" if complete else "нет"}, "programmatic:v3-done")
    return tasks


for name in ("gen_tool_selection", "gen_schema_argument", "gen_long_context", "gen_memory", "gen_computer_use", "gen_task_completion"):
    setattr(draft, name, globals()[name])

if __name__ == "__main__":
    draft.main()
