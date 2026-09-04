"""web_research: реестр ссылок прогона, бюджеты и суточный счётчик — НА ДИСКЕ.

Реестр отвечает на один вопрос: «что стоит за токеном `w3`, который модель
только что назвала». Из этого ответа растёт вся политика доступа фичи, поэтому
файл делает ровно три вещи и ни одной больше.

  * **чеканит токен и запоминает адрес за ним.** Префикс токена — не косметика,
    а политика: `w` означает «адрес выбран не страницей и не моделью», `l` —
    «адрес пришёл из тела уже прочитанной, то есть потенциально враждебной,
    страницы». Именно это делает правило «ссылка со страницы требует одобрения»
    выразимым в ЧИСТОМ `effect_hook`, у которого по сигнатуре нет ни `run_id`,
    ни `svc`, ни доступа сюда;
  * **считает бюджет прогона.** В движке бюджета вызовов нет: `max_steps`
    ограничивает обращения к модели, а список вызовов внутри шага исполняется
    целиком, и двести обращений по разным адресам для governor'а — «прогресс»;
  * **держит суточный счётчик обращений машины владельца.**

Почему обязательно диск, а не память процесса. Ветка `ask` паркует прогон и
освобождает воркер; пробуждение после одобрения может прийти в другой процесс и
через сутки. Реестр в памяти теряется ровно в тот момент, ради которого `ask` и
существует, — и токен `w1`, который владелец только что одобрил, перестал бы
резолвиться сразу после одобрения.

Чего этот файл НЕ делает и делать не должен:

  * **не хранит НИ ОДНОГО байта содержимого страниц.** Только адреса, счётчики
    и метаданные чеканки. Иначе реестр становится параллельным хранилищем
    доказательств рядом с OSIRIS, и обещание «удалил эпизод — удалил всё»
    перестаёт быть правдой. Единственный текст внешнего происхождения здесь —
    заголовок и выжимка ИЗ ВЫДАЧИ ПОИСКА (их отдаёт backend, а не тело
    прочитанной страницы), оба обрезаны и оба нужны, чтобы владелец в
    `GET /web/ledger/{run_id}` видел, что именно было предложено модели;
  * **не ходит в сеть и не решает, пускать ли на адрес.** Это дело `net.py` и
    чистых `effect_hook` в `tools.py`. Здесь только учёт;
  * **не создаёт ни одного файла при импорте.** Каталог появляется в момент
    первой записи, а записи случаются только из включённой фичи. Выключенный
    флаг обязан означать «на диске ничего не появилось»;
  * **не заводит своей канонизации адреса.** `html_text.canon_url` — одна
    функция на весь пакет; вторая однажды разойдётся с первой, и тогда
    одобренный адрес перестанет совпадать с исполненным.

Модель заражения (почему префикс — это не «откуда пришло», а «можно ли верить»):

  посылка «по `ref` байтов модели в адресе ноль» ЛОЖНА для backend'а общего
  поиска. `query` и `site` — аргументы модели, выдача от них детерминирована, и
  инъекция со страницы вида «поищи `ledger-alpha7` на `docs-mirror.example`»
  получила бы чтение своего хоста без одобрения. Поэтому:

  * `tainted` взводится при первом же открытии страницы и НЕ сбрасывается;
  * `w` выдаётся, только если `kind in ("search", "owner")` И реестр не заражён;
  * и даже до заражения `w` получает только адрес, чей хост совпадает с хостом
    самого backend'а или объявлен источником доверенным хостом выдачи. URL
    внутри тела выдачи PyPI, HN, StackExchange или SearXNG выбирает ТРЕТЬЕ ЛИЦО,
    а не backend, и паспорт «пришли из pypi» не делает такой адрес безопасным.

Одновременная запись, испорченный файл и переполнение разобраны у методов:
`_transaction`, `load` и `mint` соответственно.
"""
from __future__ import annotations

import contextlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence
from urllib.parse import urlsplit

from ... import html_text
from ...db import utcnow
from . import config

LEDGER_VERSION = 1

# Потолок числа записей. Худший ЗАКОННЫЙ случай считается по бюджетам:
# 5 поисков × 10 результатов + затравка владельца + 12 страниц × 12 ссылок
# ≈ 210 записей. 400 оставляет запас и при этом останавливает зациклившийся
# прогон задолго до того, как файл реестра станет тяжёлым. Переполнение — это
# отказ чеканить (данными, пустой строкой), а не исключение: прогон обязан
# доехать до ответа по тому, что уже собрано.
MAX_REFS_TOTAL = 400
# Формат токена (`config.REF_W_RE`) физически вмещает три цифры. Номер сверх
# этого нельзя ни выдать, ни разобрать обратно.
MAX_REF_NUMBER = 999

# Сколько знаков пути с параметрами помещается в самоописывающий `l`-токен.
# Ровно столько разрешает `config.REF_L_RE`, и это не совпадение: владелец в
# предпросмотре одобрения видит АРГУМЕНТ, то есть сам токен, и ничего кроме.
PATH_IN_TOKEN_MAX = 120

MAX_SEED_URLS = 20               # затравка из задачи — список владельца, а не поток
TITLE_MAX = 200
SNIPPET_MAX = 200
URL_MAX = 2_000
SUBJECT_MAX = 200                # = osiris.MAX_SUBJECT: длиннее субъект не примет
GATE_RULE_MAX_CHARS = 32
GATE_RULES_MAX = 8               # словарь счётчиков хука обязан быть ограничен

# Замок на файл. Критическая секция — три файловые операции, доли миллисекунды;
# замок, проживший дольше, оставлен упавшим процессом.
LOCK_TIMEOUT_S = 5.0
LOCK_STALE_S = 15.0
LOCK_POLL_S = 0.01
LOCK_BREAK_LIMIT = 3

DAILY_FILE = "_daily.json"

_RUN_ID_RE = re.compile(r"^[0-9]{1,18}$")
_REF_NUM_RE = re.compile(r"^([wl])([0-9]{1,3})")
# Хост в токене обязан состоять из тех же знаков, что разрешает config.REF_L_RE:
# иначе собранный токен не пройдёт собственную же проверку формы.
_TOKEN_HOST_RE = re.compile(r"^[a-z0-9\-._]{1,253}$")

BUDGET_LIMITS: dict[str, float] = {
    "search": float(config.MAX_SEARCHES_PER_RUN),
    "open": float(config.MAX_OPENS_PER_RUN),
    "bytes": float(config.MAX_RUN_BYTES),
    "seconds": float(config.MAX_RUN_NET_SECONDS),
}

# Резерв ДО действия против учёта ПОСЛЕ него. Разница не стилистическая:
# «поиск» и «открытие» можно не начинать, а принятые байты и потраченные
# секунды уже потрачены, и делать вид, что их можно «не списать», значит врать
# счётчику. Поэтому первые два отказывают и не тратят, вторые два тратят всегда
# и отвечают False, когда бюджет после списания исчерпан.
RESERVE_KINDS = ("search", "open")
ACCOUNT_KINDS = ("bytes", "seconds")


def _now() -> str:
    """Единый формат времени системы (`db.utcnow` — наивный UTC). Своего второго
    формата здесь нет: реестр читает и владелец, и тесты, и соседние модули."""
    return utcnow().isoformat(timespec="seconds")


def _clip(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit]


# --------------------------------------------------------------------- замок


@contextlib.contextmanager
def _file_lock(path: Path) -> Iterator[None]:
    """Межпроцессный замок на `os.O_CREAT | os.O_EXCL` — единственная форма,
    которая работает и на Windows владельца, и на Linux, и не требует новой
    зависимости.

    Замок не взят (нет прав, файловая система без `O_EXCL`, чужой процесс
    висит дольше `LOCK_TIMEOUT_S`) — работа ВСЁ РАВНО продолжается. Отказ писать
    реестр означал бы, что бюджет не списан и ссылка не отчеканена, то есть
    отказ замка превращался бы в обход бюджета. Гонку в этом вырожденном случае
    гасит слияние с диском в `_transaction`, а не замок.
    """
    lock = path.with_suffix(path.suffix + ".lock")
    fd: int | None = None
    deadline = time.monotonic() + LOCK_TIMEOUT_S
    broken = 0
    while True:
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            if _lock_is_dead(lock) or time.monotonic() >= deadline:
                if broken >= LOCK_BREAK_LIMIT:
                    break
                broken += 1
                with contextlib.suppress(OSError):
                    lock.unlink()
                continue
            time.sleep(LOCK_POLL_S)
        except OSError:
            # Права, файловая система без O_EXCL, гонка на mkdir — всё это не
            # причина потерять запись реестра.
            break
    try:
        yield
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
            with contextlib.suppress(OSError):
                lock.unlink()


def _lock_is_dead(lock: Path) -> bool:
    try:
        return (time.time() - lock.stat().st_mtime) > LOCK_STALE_S
    except OSError:
        return True


# ------------------------------------------------------------------- запись


@dataclass
class RefEntry:
    """Одна отчеканенная ссылка. Содержимого страницы здесь нет и быть не может.

    `ref` — ПОЛНЫЙ токен, включая самоописывающий хвост `@host/path` у `l`-веток:
    в предпросмотре одобрения владелец видит аргумент вызова и ничего кроме,
    поэтому назначение обязано жить в самом аргументе, а не в реестре, до
    которого чистая `normalize_args` не дотягивается.
    """
    ref: str
    url: str
    host: str
    kind: str                      # search | owner | link
    origin: str = ""               # id backend'а или родительский ref
    subject: str = ""              # субъект эпизода OSIRIS
    title: str = ""
    snippet: str = ""
    minted_at: str = ""
    minted_step: int = 0
    opened_at: str = ""
    # Время УСПЕШНОЙ проверки цитаты через web.cite. Пустая строка — цитаты не
    # было. Это единственный признак «источник подтверждён»: сам по себе
    # маркер [w1] в ответе не значит ничего, его модель печатает от руки.
    cited_at: str = ""
    raw_digest: str = ""
    body_sha256: str = ""
    status: str = ""
    chars: int = 0
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref, "url": self.url, "host": self.host, "kind": self.kind,
            "origin": self.origin, "subject": self.subject, "title": self.title,
            "snippet": self.snippet, "minted_at": self.minted_at,
            "minted_step": self.minted_step, "opened_at": self.opened_at,
            "cited_at": self.cited_at, "raw_digest": self.raw_digest, "body_sha256": self.body_sha256,
            "status": self.status, "chars": self.chars, "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RefEntry | None":
        """None вместо исключения: испорченная запись — это данные с диска, а не
        ошибка программы, и одна кривая строка не имеет права уронить прогон."""
        if not isinstance(raw, dict):
            return None
        ref = _clip(raw.get("ref"), config.REF_MAX_CHARS)
        url = _clip(raw.get("url"), URL_MAX)
        if not ref or not url or not _REF_NUM_RE.match(ref):
            return None
        try:
            chars = int(raw.get("chars") or 0)
            step = int(raw.get("minted_step") or 0)
        except (TypeError, ValueError):
            chars, step = 0, 0
        return cls(
            ref=ref, url=url,
            host=_clip(raw.get("host"), 253).lower(),
            kind=_clip(raw.get("kind"), 16) or "link",
            origin=_clip(raw.get("origin"), 200),
            subject=_clip(raw.get("subject"), SUBJECT_MAX),
            title=_clip(raw.get("title"), TITLE_MAX),
            snippet=_clip(raw.get("snippet"), SNIPPET_MAX),
            minted_at=_clip(raw.get("minted_at"), 32),
            minted_step=max(0, step),
            opened_at=_clip(raw.get("opened_at"), 32),
            cited_at=_clip(raw.get("cited_at"), 32),
            raw_digest=_clip(raw.get("raw_digest"), 128),
            body_sha256=_clip(raw.get("body_sha256"), 128),
            status=_clip(raw.get("status"), 64),
            chars=max(0, chars),
            truncated=bool(raw.get("truncated")),
        )


# ------------------------------------------------------------------- реестр


class Ledger:
    """Курсор над файлом `<data_dir>/osiris/web_runs/run-<run_id>.json`.

    Объект — не кэш: КАЖДЫЙ изменяющий метод сам берёт замок, перечитывает файл,
    сливает его с собой, применяет изменение и пишет обратно через
    `os.replace`. Поэтому расхождения между объектом и файлом не бывает, а
    ссылка, отчеканенная в одном процессе, резолвится в другом — том, который
    проснётся после одобрения владельца.
    """

    def __init__(self, svc: Any, run_id: str, path: Path) -> None:
        self._svc = svc
        self.run_id = run_id
        self.path = path
        self.created_at = _now()
        self.updated_at = ""
        self.tainted = False
        self.damaged = False
        self._spent: dict[str, float] = {kind: 0.0 for kind in config.BUDGET_KINDS}
        self._gate: dict[str, int] = {}
        self._refs: dict[str, RefEntry] = {}      # токен → запись, в порядке чеканки

    # ------------------------------------------------------------ адресация

    @staticmethod
    def path_for(svc: Any, run_id: Any) -> Path:
        """`run_id` приходит и из `ToolContext` (int), и из пути HTTP-ручки
        (строка от владельца). Поэтому здесь не форматирование, а проверка:
        всё, кроме цифр, — отказ. Иначе `DELETE /web/ledger/../../secrets`
        стал бы удалением чужого файла руками моего кода."""
        token = str(run_id).strip()
        if not _RUN_ID_RE.match(token):
            raise ValueError(f"негодный run_id: {token[:40]!r}")
        return config.runs_dir(svc) / f"run-{token}.json"

    @classmethod
    def load(cls, svc: Any, run_id: Any) -> "Ledger":
        """Файла нет — пустой объект, и файл НЕ создаётся (выключенный или
        просто не дошедший до сети прогон не оставляет следов на диске).

        Файл есть, но не читается или не разбирается — начинаем чистый реестр,
        помечаем `damaged=True` и ВЗВОДИМ `tainted`. Второе важнее первого:
        потерянный реестр — это потерянные счётчики, вернуть их нельзя, но можно
        перестать раздавать бесплатные `w`-адреса. После порчи каждая новая
        ссылка требует одобрения владельца с показом полного адреса — прогон
        продолжается, но уже не молча. Испорченный файл при этом не
        затирается сразу: он отодвигается в `.broken-<время>`, потому что это
        улика, а не мусор.
        """
        path = cls.path_for(svc, run_id)
        led = cls(svc, str(run_id).strip(), path)
        exists = path.exists()
        doc = config.read_json(path) if exists else None
        if exists and not isinstance(doc, dict):
            led.damaged = True
            led.tainted = True
            with contextlib.suppress(OSError):
                path.replace(path.with_suffix(f".json.broken-{int(time.time())}"))
            return led
        led._absorb(doc)
        return led

    # ------------------------------------------------------- сериализация

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": LEDGER_VERSION,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at or _now(),
            "tainted": self.tainted,
            "damaged": self.damaged,
            "spent": {k: (round(v, 3) if k == "seconds" else int(v))
                      for k, v in self._spent.items()},
            "gate": dict(self._gate),
            "refs": [entry.as_dict() for entry in self._refs.values()],
        }

    def _absorb(self, doc: Any) -> None:
        """Слияние с состоянием на диске. Правила выбраны так, чтобы устаревший
        объект НИКОГДА не откатывал файл назад:

          * счётчики — максимум (израсходованное не возвращается);
          * `tainted` и `damaged` — логическое ИЛИ (заражение не сбрасывается);
          * ссылки — объединение по токену, причём при совпадении токена с
            РАЗНЫМ адресом побеждает диск: его номер уже мог быть показан
            модели и владельцу, и переписывать назначение под выданным токеном
            нельзя ни при каких обстоятельствах.
        """
        if not isinstance(doc, dict):
            return
        created = str(doc.get("created_at") or "")
        if created and (not self.created_at or created < self.created_at):
            self.created_at = created
        self.tainted = bool(self.tainted or doc.get("tainted"))
        self.damaged = bool(self.damaged or doc.get("damaged"))

        spent = doc.get("spent")
        if isinstance(spent, dict):
            for kind in config.BUDGET_KINDS:
                try:
                    disk = float(spent.get(kind) or 0.0)
                except (TypeError, ValueError):
                    disk = 0.0
                self._spent[kind] = max(self._spent.get(kind, 0.0), max(0.0, disk))

        gate = doc.get("gate")
        if isinstance(gate, dict):
            for rule, value in gate.items():
                key = _clip(rule, GATE_RULE_MAX_CHARS)
                if not key:
                    continue
                try:
                    disk_value = int(value or 0)
                except (TypeError, ValueError):
                    continue
                if key not in self._gate and len(self._gate) >= GATE_RULES_MAX:
                    continue
                self._gate[key] = max(self._gate.get(key, 0), max(0, disk_value))

        disk_refs = doc.get("refs")
        if not isinstance(disk_refs, list):
            return
        merged: dict[str, RefEntry] = {}
        for raw in disk_refs:
            entry = RefEntry.from_dict(raw)
            if entry is None or entry.ref in merged:
                continue
            ours = self._refs.get(entry.ref)
            # Поля (отметка о чтении, дайджест сырья) свежее у нас — их и
            # оставляем. Но АДРЕС под уже выданным токеном не наш, чтобы его
            # менять: расхождение здесь означало бы, что владелец одобрил один
            # адрес, а пойдём мы по другому.
            keep = ours if ours is not None and ours.url == entry.url else entry
            # Проверенная цитата — факт, а не поле «посвежее»: если её отметил
            # любой из двух процессов, она была, и терять её при слиянии нельзя.
            if ours is not None and ours.url == entry.url:
                keep.cited_at = keep.cited_at or entry.cited_at
            merged[entry.ref] = keep
        for token, entry in self._refs.items():
            if token not in merged and len(merged) < MAX_REFS_TOTAL:
                merged[token] = entry
        self._refs = merged

    def _write(self) -> None:
        self.updated_at = _now()
        config.atomic_write_json(self.path, self.as_dict())

    def _transaction(self, apply):
        """Замок → перечитать диск → слить → применить → записать атомарно.

        Гонка двух одновременных записей закрывается здесь дважды. Замок разводит
        обычный случай; слияние с диском разводит вырожденный, когда замок взять
        не удалось: счётчики берутся максимумом, ссылки объединяются, номер для
        новой ссылки выбирается уже ПОСЛЕ чтения файла — то есть два процесса не
        могут выдать один и тот же `w1` на два разных адреса.
        """
        with _file_lock(self.path):
            self._absorb(config.read_json(self.path))
            result = apply()
            self._write()
        return result

    def save(self) -> None:
        """Явная точка сохранения для вызывающего, который правил поля записи
        руками. Все изменяющие методы пишут сами, поэтому обычно save() —
        безобидное подтверждение уже сохранённого состояния."""
        self._transaction(lambda: None)

    # ------------------------------------------------------------- чеканка

    def _next_number(self, prefix: str) -> int:
        used = 0
        for token in self._refs:
            match = _REF_NUM_RE.match(token)
            if match and match.group(1) == prefix:
                used = max(used, int(match.group(2)))
        return used + 1

    @staticmethod
    def _token_tail(url: str) -> str | None:
        """Хвост самоописывающего токена: `/путь?параметры` от канонического
        адреса. None — «в токен не помещается».

        Обрезать хвост многоточием нельзя: владелец одобряет то, что видит, и
        `l4@docs.example/a…` — это обещание показать назначение, которого мы не
        выполнили. Поэтому слишком длинный адрес просто не чеканится как `l`, а
        значит и не предлагается модели: одной несделанной ссылкой мы платим за
        то, чтобы ни одно одобрение не было слепым.
        """
        split = urlsplit(url)
        tail = split.path or "/"
        if split.query:
            tail = f"{tail}?{split.query}"
        if len(tail) - 1 > PATH_IN_TOKEN_MAX:
            return None
        return tail

    def _prefix_for(self, kind: str, host: str, *, origin_host: str,
                    trusted_hosts: Sequence[str]) -> str:
        """`w` или `l` — решение о том, нужно ли одобрение владельца.

        Три условия, и каждое закрывает свой известный обход:

          * `kind in ("search", "owner")` — ссылка из тела страницы никогда не
            бывает `w`, это исходное правило;
          * `not tainted` — после первого же открытия страницы даже поиск
            перестаёт давать `w`: `query` и `site` это аргументы модели, а
            выдача от них детерминирована, значит инъекция со страницы умеет
            выбрать хост вторым прыжком через поиск;
          * хост совпадает с хостом backend'а или объявлен им доверенным —
            потому что URL внутри тела выдачи (PyPI, HN, StackExchange,
            SearXNG) выбирает третье лицо, а не backend, и «пришло из pypi» не
            делает адрес выбранным нами.

        Для `kind="owner"` хост не проверяется: адрес назвал сам владелец, и
        сверять его список с чьим-то ещё не с чем. Допущение названо вслух:
        затравка берётся из `task.meta`, и доверие к ней — это доверие к тому,
        что `meta` пишет владелец при постановке задачи, а не инструмент по
        ходу прогона.
        """
        if kind not in ("search", "owner") or self.tainted:
            return "l"
        if kind == "owner":
            return "w"
        allowed = {h for h in _same_site(origin_host) if h}
        for extra in trusted_hosts or ():
            allowed |= _same_site(str(extra))
        # Суффиксного сравнения здесь нет намеренно: правило
        # `host.endswith("." + declared)` пропускает `evil.example.com` по
        # объявлению `example.com`, то есть отдаёт `w` чужому сайту.
        return "w" if host in allowed else "l"

    def mint(self, url: str, *, kind: str, subject: str = "", title: str = "",
             snippet: str = "", origin: str = "", origin_host: str = "",
             trusted_hosts: Sequence[str] = (), step: int = 0) -> str:
        """Отчеканить токен для адреса. Пустая строка — отказ чеканить.

        Отказ — это ДАННЫЕ, а не исключение: адреса приходят из выдачи и из тел
        страниц, то есть из внешнего мира, и ни один из них не имеет права
        уронить прогон. Причин отказа четыре, все безопасные по умолчанию:
        адрес не канонизуется (`canon_url` отвергает userinfo, bidi, не-ASCII
        хост, чужую схему); хост не помещается в форму токена; путь длиннее
        того, что влезает в самоописывающий токен (см. `_token_tail`); реестр
        переполнен (`MAX_REFS_TOTAL`).

        Повторный адрес возвращает СУЩЕСТВУЮЩИЙ токен и не перечеканивает его:
        номер уже мог уйти модели и владельцу. Побочное свойство здесь тоже
        безопасное — адрес, однажды получивший `l`, после заражения не станет
        `w` задним числом.
        """
        if kind not in ("search", "owner", "link"):
            raise ValueError(f"неизвестный вид ссылки: {kind!r}")
        try:
            canonical = html_text.canon_url(url)
        except (ValueError, UnicodeError):
            return ""
        if len(canonical) > URL_MAX:
            return ""
        host = (urlsplit(canonical).hostname or "").strip().lower().rstrip(".")
        if not host:
            return ""

        def _apply() -> str:
            for token, entry in self._refs.items():
                if entry.url == canonical:
                    return token
            if len(self._refs) >= MAX_REFS_TOTAL:
                return ""
            prefix = self._prefix_for(kind, host, origin_host=origin_host,
                                      trusted_hosts=trusted_hosts)
            number = self._next_number(prefix)
            if number > MAX_REF_NUMBER:
                return ""
            if prefix == "w":
                token = f"w{number}"
            else:
                if not _TOKEN_HOST_RE.match(host):
                    return ""
                tail = self._token_tail(canonical)
                if tail is None:
                    return ""
                token = f"l{number}@{host}{tail}"
            # Собственная форма проверяется собственной же регуляркой: токен,
            # который не пройдёт `open_effect`, бесполезен и лучше бы его не
            # было вовсе, чем он висел в реестре и в выдаче модели.
            if not config.REF_RE.match(token) or len(token) > config.REF_MAX_CHARS:
                return ""
            self._refs[token] = RefEntry(
                ref=token, url=canonical, host=host, kind=kind,
                origin=_clip(origin, 200), subject=_clip(subject, SUBJECT_MAX),
                title=_clip(title, TITLE_MAX), snippet=_clip(snippet, SNIPPET_MAX),
                minted_at=_now(), minted_step=max(0, int(step or 0)),
            )
            return token

        return self._transaction(_apply)

    # -------------------------------------------------------------- резолв

    def resolve(self, ref: str) -> RefEntry | None:
        """Запись за токеном или None. Ref чужого прогона не резолвится по
        построению: файл выбран по `run_id`, и другого файла у объекта нет."""
        entry, _ = self.resolve_with_reason(ref)
        return entry

    def resolve_with_reason(self, ref: str) -> tuple[RefEntry | None, str]:
        """То же, но с причиной отказа. Причина нужна не для текста владельцу, а
        чтобы вызывающий отличил «модель назвала несуществующий номер» (обычная
        ошибка 7B) от «хост в токене не совпал с записью» — второе означает, что
        между одобрением и исполнением кто-то пытался подменить назначение, и
        это событие, а не опечатка.
        """
        token = (ref or "").strip()
        if not token:
            return None, "empty"
        if config.parse_ref(token) is None:
            return None, "bad_form"
        # Ведущий ноль («w03») — обычная описка маленькой модели, а не подмена:
        # номер приводится к одной форме ДО сверки, иначе опечатка выглядела бы
        # как попытка подставить чужой адрес и путала бы владельца.
        match = _REF_NUM_RE.match(token)
        token = f"{match.group(1)}{int(match.group(2))}{token[match.end():]}"
        entry = self._refs.get(token) or self._find_by_number(token)
        if entry is None:
            return None, "unknown"
        expected = self._expected_token(entry)
        if expected is None or not _tokens_equal(expected, token):
            # A2. Самоописание токена — не украшение, а проверка: `approval_digest`
            # фиксирует строку аргумента, поэтому запись, изменившаяся между
            # одобрением и исполнением, обязана давать отказ, а не тихий переход
            # по новому адресу.
            return None, "mismatch"
        return entry, ""

    def _find_by_number(self, token: str) -> RefEntry | None:
        match = _REF_NUM_RE.match(token)
        if not match:
            return None
        head = f"{match.group(1)}{int(match.group(2))}"
        for key, entry in self._refs.items():
            other = _REF_NUM_RE.match(key)
            if other and f"{other.group(1)}{int(other.group(2))}" == head:
                return entry
        return None

    def _expected_token(self, entry: RefEntry) -> str | None:
        """Токен, который ЭТА запись обязана иметь. Сверяется целиком, а не по
        префиксу пути: сравнение «путь токена — начало пути записи» пропустило бы
        `/a` на `/admin`."""
        match = _REF_NUM_RE.match(entry.ref)
        if not match:
            return None
        prefix, number = match.group(1), int(match.group(2))
        if prefix == "w":
            return f"w{number}"
        tail = self._token_tail(entry.url)
        if tail is None:
            return None
        return f"l{number}@{entry.host}{tail}"

    def refs(self) -> list[RefEntry]:
        return list(self._refs.values())

    def opened_refs(self) -> list[RefEntry]:
        return [entry for entry in self._refs.values() if entry.opened_at]

    # ---------------------------------------------------- отметка о чтении

    def mark_tainted(self) -> None:
        """Взводится при первом же ОБРАЩЕНИИ к странице, а не при успешном
        чтении: заражает не текст, а сам факт, что страница выбрала, чем
        ответить. Обратной операции нет намеренно.

        Короткого выхода «уже заражён — ничего не делаем» здесь нет: реестр,
        загруженный с испорченного файла, заражён только в памяти, и без записи
        на диск следующий процесс об этом не узнает."""
        self._transaction(lambda: setattr(self, "tainted", True))

    def mark_opened(self, ref: str, *, raw_digest: str = "", body_sha256: str = "",
                    chars: int = 0, truncated: bool = False, status: str = "") -> bool:
        """Проставить результат чтения. False — записи нет (тогда и писать
        некуда). Заражение взводится здесь же: если вызывающий забыл
        `mark_tainted()`, реестр всё равно не останется чистым."""
        def _apply() -> bool:
            entry = self._refs.get(ref) or self._find_by_number(ref)
            self.tainted = True
            if entry is None:
                return False
            entry.opened_at = _now()
            entry.raw_digest = _clip(raw_digest, 128)
            entry.body_sha256 = _clip(body_sha256, 128)
            entry.status = _clip(status, 64)
            entry.chars = max(0, int(chars or 0))
            entry.truncated = bool(truncated)
            return True

        return self._transaction(_apply)

    # -------------------------------------------------------------- бюджет

    def spend(self, kind: str, n: float = 1) -> bool:
        """Списать бюджет прогона. False — «дальше нельзя».

        Два вида списания, и разница не стилистическая:

          * `search` и `open` — РЕЗЕРВ ДО действия. Не помещается — ничего не
            списываем и отвечаем False; вызывающий печатает готовый текст
            `config.MSG_BUDGET_*` с `error=False`, потому что `error=True`
            заставляет маленькую модель повторить вызов, и защита от
            перерасхода сама становится перерасходом;
          * `bytes` и `seconds` — УЧЁТ ПОСЛЕ действия. Байты уже приняты и
            секунды уже потрачены; счётчик может перевалить за потолок, и это
            честнее, чем «не списалось, потому что не помещалось».
        """
        if kind not in config.BUDGET_KINDS:
            raise ValueError(f"неизвестный вид бюджета: {kind!r}")
        if kind not in RESERVE_KINDS and kind not in ACCOUNT_KINDS:
            # Список видов бюджета живёт в config, а способ списания — здесь.
            # Новый вид, добавленный там и не разнесённый сюда, обязан упасть
            # громко: молчаливое «спишем как байты» — это неучтённый расход.
            raise ValueError(f"вид бюджета {kind!r} не отнесён ни к резерву, ни к учёту")
        try:
            amount = max(0.0, float(n))
        except (TypeError, ValueError):
            raise ValueError(f"негодная величина списания: {n!r}") from None
        limit = BUDGET_LIMITS[kind]

        def _apply() -> bool:
            used = self._spent.get(kind, 0.0)
            if kind in RESERVE_KINDS:
                if used + amount > limit:
                    return False
                self._spent[kind] = used + amount
                return True
            self._spent[kind] = used + amount
            return self._spent[kind] < limit

        return self._transaction(_apply)

    def left(self) -> dict[str, Any]:
        """Остатки для шапки результата и для ручки владельца.

        Форма у каждого вида одна и та же — `used`, `limit`, `left`, — потому
        что шаблоны отказов в `config` подставляют `{used}` и `{limit}`, и
        считать их второй раз в рендере значило бы завести второй счётчик.
        """
        out: dict[str, Any] = {}
        for kind in config.BUDGET_KINDS:
            limit = BUDGET_LIMITS[kind]
            used = self._spent.get(kind, 0.0)
            if kind == "seconds":
                out[kind] = {"used": round(used, 1), "limit": round(limit, 1),
                             "left": round(max(0.0, limit - used), 1)}
            else:
                out[kind] = {"used": int(used), "limit": int(limit),
                             "left": int(max(0.0, limit - used))}
        out["daily"] = daily_state(self._svc)
        out["refs"] = {"used": len(self._refs), "limit": MAX_REFS_TOTAL,
                       "left": max(0, MAX_REFS_TOTAL - len(self._refs))}
        out["tainted"] = self.tainted
        out["damaged"] = self.damaged
        return out

    # --------------------------------------------- подтверждённые цитаты

    def note_cite(self, ref: str) -> bool:
        """Отметить, что для этой ссылки web.cite ДОСЛОВНО нашёл цитату.

        Зовётся только из успешной ветки web.cite — то есть после того, как
        текст цитаты найден в теле страницы и наблюдение записано. Никакой
        другой путь эту отметку не ставит: иначе «подтверждено» перестало бы
        что-либо значить.
        """
        token = (ref or "").strip()
        if not token:
            return False

        def _apply() -> bool:
            entry = self._refs.get(token) or self._find_by_number(token)
            if entry is None:
                return False
            entry.cited_at = entry.cited_at or _now()
            return True

        return bool(self._transaction(_apply))

    def cited_refs(self) -> list[RefEntry]:
        """Ссылки, для которых цитата действительно проверена."""
        return [e for e in self._refs.values() if e.cited_at]

    # ------------------------------------------------- счётчик попыток хука

    def gate_attempts(self, rule: str) -> int:
        return int(self._gate.get(_clip(rule, GATE_RULE_MAX_CHARS), 0))

    def bump_gate(self, rule: str) -> None:
        """Каждое правило `gate_completion` даёт РОВНО ОДНУ корректирующую
        попытку на прогон; дальше PASS плюс событие. Иначе слабая модель крутится
        до `max_steps` на процессоре владельца."""
        key = _clip(rule, GATE_RULE_MAX_CHARS)
        if not key:
            return

        def _apply() -> None:
            if key not in self._gate and len(self._gate) >= GATE_RULES_MAX:
                return
            self._gate[key] = self._gate.get(key, 0) + 1

        self._transaction(_apply)

    # ------------------------------------------------------------ затравка

    def seed_from_task(self, task: Any) -> list[str]:
        """Адреса из `task.meta.web_seed_urls` — как `kind="owner"`.

        Это единственный путь, которым адрес попадает в реестр до первого
        поиска. Негодные адреса молча пропускаются: список пишет человек, и
        одна опечатка в нём не повод отказать в поиске целиком.
        """
        meta = task.get("meta") if isinstance(task, dict) else None
        if not isinstance(meta, dict):
            return []
        seeds = meta.get("web_seed_urls")
        if isinstance(seeds, str):
            seeds = [seeds]
        if not isinstance(seeds, (list, tuple)):
            return []
        minted: list[str] = []
        for raw in list(seeds)[:MAX_SEED_URLS]:
            if not isinstance(raw, str):
                continue
            token = self.mint(raw, kind="owner", origin="owner:task")
            if token:
                minted.append(token)
        return minted

    # ---------------------------------------------------------- жизненный цикл

    @staticmethod
    def delete(svc: Any, run_id: Any) -> bool:
        """Идемпотентное удаление реестра прогона. True — файл был и удалён.

        Реестр удаляется ВМЕСТЕ с эпизодом (см. `subject_runs`), потому что иначе
        обещание «удалил эпизод — не осталось ничего» держалось бы на том, что
        адреса «не считаются данными». Они считаются.
        """
        try:
            path = Ledger.path_for(svc, run_id)
        except ValueError:
            return False
        removed = False
        for candidate in (path, path.with_suffix(path.suffix + ".lock")):
            try:
                candidate.unlink()
                removed = removed or candidate == path
            except OSError:
                continue
        for broken in path.parent.glob(f"{path.stem}.json.broken-*"):
            with contextlib.suppress(OSError):
                broken.unlink()
        return removed

    @staticmethod
    def subject_runs(svc: Any, subject: str) -> list[str]:
        """Прогоны, в реестрах которых встречается этот субъект OSIRIS.

        Нужно ровно для удаления эпизода: субъект — это эпизод, а реестры
        адресуются по прогону, и без такого перебора «удалить вместе с эпизодом»
        превратилось бы в «удалить, если владелец сам помнит номер прогона».
        Перебор дёшев: реестры живут сутки и их единицы.
        """
        wanted = (subject or "").strip()
        if not wanted:
            return []
        found: list[str] = []
        for path in _run_files(svc):
            doc = config.read_json(path)
            if not isinstance(doc, dict):
                continue
            refs = doc.get("refs")
            if not isinstance(refs, list):
                continue
            if any(isinstance(item, dict) and item.get("subject") == wanted
                   for item in refs):
                found.append(str(doc.get("run_id") or path.stem[4:]))
        return found

    @staticmethod
    def delete_for_subject(svc: Any, subject: str) -> list[str]:
        removed = []
        for run_id in Ledger.subject_runs(svc, subject):
            if Ledger.delete(svc, run_id):
                removed.append(run_id)
        return removed

    @staticmethod
    def gc(svc: Any, ttl_hours: float = config.LEDGER_TTL_HOURS) -> int:
        """Убрать реестры старше TTL. Возвращает число удалённых файлов.

        Возраст берётся по времени изменения файла, а не по полю внутри: у
        испорченного файла поля нет, а убирать его надо тем более. Суточный
        счётчик и файл преполёта не трогаются — они не `run-*.json`.
        """
        cutoff = time.time() - max(0.0, float(ttl_hours)) * 3600.0
        removed = 0
        for path in _run_files(svc, extra_globs=("run-*.json.broken-*", "run-*.json.lock",
                                                 "run-*.json.tmp*")):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
            except OSError:
                continue
            removed += 1
        return removed


def _run_files(svc: Any, *, extra_globs: Sequence[str] = ()) -> list[Path]:
    directory = config.runs_dir(svc)
    if not directory.is_dir():
        return []
    files: list[Path] = []
    for pattern in ("run-*.json", *extra_globs):
        with contextlib.suppress(OSError):
            files.extend(p for p in directory.glob(pattern) if p.is_file())
    return files


def _same_site(host: str) -> set[str]:
    """`{host, host без www., www.+host}` — тот же набор, что уходит в
    `allowed_hosts` транспорта. Поддомены сюда НЕ входят: «доверенный хост
    выдачи» это конкретное имя, а не дерево имён под ним."""
    clean = (host or "").strip().lower().rstrip(".")
    if not clean:
        return set()
    bare = clean[4:] if clean.startswith("www.") else clean
    return {clean, bare, f"www.{bare}"}


def _tokens_equal(expected: str, given: str) -> bool:
    """Сравнение токенов. Хост регистронезависим (DNS), путь — нет (сервер): в
    одном токене живут обе половины, поэтому и правил сравнения два."""
    if expected == given:
        return True
    e_head, e_sep, e_tail = expected.partition("/")
    g_head, g_sep, g_tail = given.partition("/")
    return e_sep == g_sep and e_head.lower() == g_head.lower() and e_tail == g_tail


# ------------------------------------------------------- суточный счётчик


def _daily_path(svc: Any) -> Path:
    return config.runs_dir(svc) / DAILY_FILE


def _today() -> str:
    return utcnow().date().isoformat()


def _daily_read(svc: Any) -> tuple[str, int]:
    """(день, израсходовано). Испорченный файл — это новый день с нулём: суточный
    счётчик не то место, где стоит уронить прогон, а его потеря ограничена одними
    сутками и не открывает доступа никуда, куда не было доступа раньше."""
    doc = config.read_json(_daily_path(svc))
    if not isinstance(doc, dict):
        return _today(), 0
    day = str(doc.get("day") or "")
    try:
        used = max(0, int(doc.get("used") or 0))
    except (TypeError, ValueError):
        used = 0
    if day != _today():
        return _today(), 0
    return day, used


def daily_state(svc: Any) -> dict[str, Any]:
    """Остаток суточного лимита. Только чтение: отсутствующий файл не создаётся,
    потому что показ состояния не имеет права оставлять следов на диске."""
    _, used = _daily_read(svc)
    limit = int(config.DAILY_FETCHES)
    return {"used": used, "limit": limit, "left": max(0, limit - used), "day": _today()}


def daily_take(svc: Any, n: int = 1) -> bool:
    """Забрать `n` обращений из суточного лимита машины владельца.

    False — лимит исчерпан, и НИЧЕГО не списано: это резерв до сети, а не учёт
    после неё. Лимит общий на все прогоны, поэтому файл один и правило то же,
    что у реестра: замок, перечитывание и атомарная замена.
    """
    take = max(0, int(n or 0))
    limit = int(config.DAILY_FETCHES)
    path = _daily_path(svc)
    with _file_lock(path):
        day, used = _daily_read(svc)
        if used + take > limit:
            return False
        config.atomic_write_json(path, {"day": day, "used": used + take,
                                        "updated_at": _now()})
    return True


def daily_rollover(svc: Any) -> bool:
    """Ролловер суток из `tick()`. True — счётчик обнулён.

    Отдельный вызов нужен не ради `daily_take` (тот и сам видит смену дня), а
    ради ручки владельца и файла на диске: иначе `_daily.json` неделями показывал
    бы вчерашнее число, и владелец читал бы его как сегодняшнее.
    """
    path = _daily_path(svc)
    if not path.exists():
        return False
    with _file_lock(path):
        doc = config.read_json(path)
        day = str(doc.get("day") or "") if isinstance(doc, dict) else ""
        if day == _today():
            return False
        config.atomic_write_json(path, {"day": _today(), "used": 0,
                                        "updated_at": _now()})
    return True
