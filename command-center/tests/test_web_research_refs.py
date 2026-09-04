"""web_research: реестр ссылок и эффекты инструментов — что стоит за токеном.

Реестр отвечает на единственный вопрос «куда ведёт `w1`», и на этом ответе
держится вся политика доступа фичи. Поэтому здесь проверяется не «реестр
работает», а вред, который случается тихо и виден только через месяц.

  * **чужой прогон.** Токен `w1` есть в каждом втором прогоне. Если он
    резолвится не в своём реестре, одобрение, выданное владельцем вчера в одной
    задаче, открывает адрес в другой — и никто этого не заметит, потому что
    внешне всё «работает»;
  * **перезапуск процесса.** Ветка `ask` паркует прогон и освобождает воркер;
    пробуждение приходит в другой процесс. Реестр в памяти теряется ровно в тот
    момент, ради которого `ask` и существует, — и одобренная ссылка перестаёт
    резолвиться сразу ПОСЛЕ одобрения;
  * **слепое одобрение (A2).** `normalize_args` по сигнатуре чистая: ни `svc`,
    ни `run_id`, ни доступа к реестру. Непрозрачный `l7` означал бы, что
    владелец одобряет строку, не зная назначения, а `approval_digest` фиксирует
    номер вместо адреса. Здесь предпросмотр собирается ровно так же, как его
    собирает движок, и в нём обязаны быть ХОСТ И ПУТЬ;
  * **подмена записи между одобрением и исполнением.** Одобрен один адрес,
    исполнен другой — и паспорт наблюдения назовёт чужую страницу источником;
  * **заражение (B1).** Посылка «по `ref` байтов модели в адресе ноль» ложна
    после первого же чтения страницы: `query` и `site` — аргументы модели, и
    инъекция со страницы вторым прыжком через поиск получает чтение своего
    хоста. Проверяется на живом конвейере: первый поиск даёт `w`, поиск ПОСЛЕ
    открытия страницы — `l`, то есть вопрос владельцу;
  * **A1: предпросмотр прячет ровно тот секрет, ради которого спрашивают.**
    Движок прогоняет предпросмотр через редактор секретов, а исполняет сырой
    аргумент. Значит адрес, который редактор изменил бы, нельзя показать честно
    — и спрашивать про него тоже нельзя, только отказ;
  * **битый JSON локальной модели.** Ключ `_raw` кладёт разбор аргументов, когда
    JSON от раннера не разбирается вовсе. Это сегодня отказ каждого инструмента
    системы, и здесь он не имеет права стать исключением наружу.

Сети в файле нет: транспорт OSIRIS подменяется стендом со свойством
`live = False`, и каждый тест, утверждающий «наружу не ходили», считает его
обращения, а не полагается на удачу.

Чего этот файл НЕ проверяет (у этого есть свои файлы): выключенный флаг и
нулевая настройка, извлечение текста, рендер результата, происхождение
наблюдений и хук завершения.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from bcc.features import osiris
from bcc.features.web_research import config, ledger, net, tools
from bcc.plugin_security import redact as psec_redact
from bcc.plugin_security import redact_text
from bcc.tools import REGISTRY, ToolContext, approval_digest, execute_tool, normalized_args

from .conftest import make_settings, start_app

# Канарейка репозитория: строка, которую редактор секретов ОБЯЗАН вычистить.
# Настоящего секрета в тесте быть не может, а проверять поведение защиты от
# секретов на строке, которую защита не считает секретом, — значит проверять
# ничего.
CANARY = "BOSSMAN_TEST_SECRET_9F31A7"

WIKI_HOST = "ru.wikipedia.org"
WIKI_API = f"https://{WIKI_HOST}/w/api.php"
WIKI_ROBOTS = f"https://{WIKI_HOST}/robots.txt"
WIKI_PAGE = f"https://{WIKI_HOST}/wiki/Sky"
WIKI_PAGE_2 = f"https://{WIKI_HOST}/wiki/Grass"

DOCS_HOST = "docs.example.org"
DOCS_ROBOTS = f"https://{DOCS_HOST}/robots.txt"
DOCS_PAGE = f"https://{DOCS_HOST}/guide/api"

ROBOTS_ALLOW = "User-agent: *\nAllow: /\n"

# Страницы стенда — обычный текст без единой скрытой конструкции: этот файл
# проверяет реестр и эффекты, а не защиту от инъекций, и «подозрительный» текст
# здесь только запутал бы причину падения.
PAGE_HTML = (
    "<html><head><title>Небо</title></head><body>"
    "<p>Небо кажется синим потому, что молекулы воздуха рассеивают короткие "
    "волны сильнее длинных. Это рэлеевское рассеяние, и оно объясняет и цвет "
    "неба днём, и красный цвет солнца у горизонта вечером.</p>"
    "<p>На закате свет проходит сквозь более толстый слой атмосферы, короткие "
    "волны рассеиваются в стороны, и до наблюдателя доходят длинные.</p>"
    "</body></html>")


# --------------------------------------------------------------------- стенд


class StubAdapter:
    """Транспорт без сети, считающий каждое обращение.

    `live = False` — не косметика: право пометить источник проверенным есть
    только у настоящей сети, и тест, который утверждает «наружу не ходили»,
    обязан ставить именно стенд.

    Реализован осирисовский `fetch` и НЕ реализован `fetch_bytes`: конвейер
    страницы умеет работать с таким стендом, а `robots.txt` тянется только
    через `fetch` — стенд без него упёрся бы в fail-closed «robots недоступен»,
    и все тесты чтения падали бы по причине, не имеющей отношения к делу.
    """

    live = False

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, str, dict[str, str]]] = {}
        self.calls: list[str] = []

    def route(self, marker: str, body: str, *, status: int = 200,
              content_type: str = "text/html; charset=utf-8") -> None:
        """Ответ на адрес, содержащий `marker`.

        Вхождение, а не начало строки: текст запроса уходит в адрес API
        percent-кодированным (`Source.url_for`), и различать два поиска можно
        только по этой части. Порядок объявления значим — первый подошедший
        маршрут и отвечает, поэтому частные объявляются раньше общих.
        """
        self.routes[marker] = (status, body, {"content-type": content_type})

    async def fetch(self, url: str, *, headers=None, timeout: float = 15.0):
        self.calls.append(url)
        for marker, (status, body, head) in self.routes.items():
            if marker in url:
                return osiris.FetchResult(status=status, body=body, url=url, headers=head)
        return osiris.FetchResult(status=404, body="", url=url,
                                  headers={"content-type": "text/plain"})

    def page_calls(self) -> list[str]:
        """Обращения за страницами: `robots.txt` — это не чтение страницы, а
        выяснение, есть ли разрешение читать, и в счёт «ходили наружу за
        содержимым» он не идёт."""
        return [u for u in self.calls if not u.endswith("/robots.txt")]


class Machine:
    """Другой процесс над тем же каталогом данных.

    Реестр связан с процессом ровно одним — путём к `settings.data_dir`.
    Поэтому «перезапуск» здесь честно моделируется новым объектом сервисов над
    тем же каталогом, а не повторной загрузкой того же самого объекта: второе
    доказывало бы только то, что метод `load` умеет читать свою же память.
    """

    def __init__(self, data_dir: Path) -> None:
        self.settings = SimpleNamespace(data_dir=Path(data_dir))


class Env:
    def __init__(self, svc, settings, adapter: StubAdapter) -> None:
        self.svc = svc
        self.settings = settings
        self.adapter = adapter

    @property
    def data_dir(self) -> Path:
        return Path(self.settings.data_dir)

    def ctx(self, *, run_id: int = 7, step: int = 1) -> ToolContext:
        return ToolContext(svc=self.svc, task={"id": 1, "meta": {}}, run_id=run_id,
                           agent={"id": 1, "name": "аналитик"}, step=step)

    def ledger(self, run_id: int = 7) -> ledger.Ledger:
        return ledger.Ledger.load(self.svc, run_id)

    def run_file(self, run_id: int = 7) -> Path:
        return ledger.Ledger.path_for(self.svc, run_id)


@pytest.fixture(autouse=True)
def _isolate_process_tables():
    """Реестр инструментов, таблица парсеров и память вердиктов robots —
    глобальные на процесс. Не убрав их за собой, этот файл ломал бы соседние
    (или, хуже, проходил бы за их счёт)."""
    tools_before = set(REGISTRY.names())
    parsers_before = dict(osiris.PARSERS)
    net.robots_cache_clear()
    yield
    for name in set(REGISTRY.names()) - tools_before:
        REGISTRY.unregister(name)
    osiris.PARSERS.clear()
    osiris.PARSERS.update(parsers_before)
    net.robots_cache_clear()


@asynccontextmanager
async def _stand(tmp_path, monkeypatch):
    """Оба флага включены, транспорт подменён, общий веб-поиск не настроен.

    Флаги ставятся ДО старта: `setup()` зовётся из `Services.start()`. Адаптер
    подменяется ПОСЛЕ старта, потому что `setup()` сам ставит боевой
    `WebFetchAdapter` — и без подмены первый же тест пошёл бы в настоящую сеть.
    """
    assert config.env_errors() == (), (
        "в окружении разработчика испорчены переменные BOSSMAN_WEB_*: "
        "стенд обязан стоять на настройке по умолчанию")
    monkeypatch.setenv(config.FLAG, "1")
    monkeypatch.setenv(config.OSIRIS_FLAG, "1")
    # Свой SearXNG превращает поиск в общий веб-поиск (и меняет эффект `site`).
    # Если он задан у разработчика, стенд перестаёт быть тем, что описано.
    monkeypatch.setattr(config, "SEARXNG_URL", "")
    settings = make_settings(tmp_path)
    _app, svc = await start_app(settings, start_workers=False)
    adapter = StubAdapter()
    adapter.route(WIKI_ROBOTS, ROBOTS_ALLOW, content_type="text/plain")
    adapter.route(DOCS_ROBOTS, ROBOTS_ALLOW, content_type="text/plain")
    osiris.store(svc).adapter = adapter
    try:
        yield Env(svc, settings, adapter)
    finally:
        await svc.stop()


@pytest.fixture
async def env(tmp_path, monkeypatch):
    async with _stand(tmp_path, monkeypatch) as stand:
        yield stand


# ------------------------------------------------------------- помощники


def spec_of(name: str):
    spec = REGISTRY.get(name)
    assert spec is not None, f"{name} не зарегистрирован: фича не установилась"
    return spec


def serp(*urls: str) -> str:
    """Тело выдачи OpenSearch: [запрос, заголовки, описания, адреса]."""
    return json.dumps(["запрос", [f"Заголовок {i}" for i, _ in enumerate(urls, 1)],
                       ["описание"] * len(urls), list(urls)], ensure_ascii=False)


def asked(query: str) -> str:
    """Примета адреса, по которой стенд узнаёт конкретный поисковый запрос."""
    return f"search={quote(query, safe='')}"


def engine_preview(spec, args: dict) -> str:
    """Предпросмотр одобрения так, как его собирает движок: КАНОНИЧЕСКИЕ
    аргументы, прогнанные через редактор секретов. Своей формы здесь нет
    намеренно — проверять надо ту строку, которую увидит владелец."""
    shown = normalized_args(spec, args)
    return redact_text("аргументы: " + json.dumps(psec_redact(shown), ensure_ascii=False,
                                                  indent=1))


def mint_link(led: ledger.Ledger, url: str) -> str:
    """Токен ссылки со страницы — через настоящую чеканку, а не строкой руками.
    Токен, собранный в тесте вручную, проверял бы регулярку, а не поведение."""
    token = led.mint(url, kind="link", subject="почему небо синее", origin="w1",
                     origin_host=WIKI_HOST)
    assert token, f"чеканка отказала на {url}"
    return token


async def open_tool(env: Env, args: dict, *, run_id: int = 7):
    return await execute_tool(spec_of("web.open"), args, env.ctx(run_id=run_id))


async def search_tool(env: Env, args: dict, *, run_id: int = 7, step: int = 1):
    return await execute_tool(spec_of("web.search"), args, env.ctx(run_id=run_id, step=step))


# -------------------------------------------------- реестр: чужой прогон


async def test_ref_chuzhogo_progona_ne_rezolvitsya(env):
    """Вред: `w1` есть почти в каждом прогоне. Резолвись он в чужом реестре —
    и адрес, одобренный владельцем во вчерашней задаче, открывался бы сегодня в
    другой, причём под видом обычного «w» без единого вопроса.

    Проверяется с обеих сторон: сам реестр не отдаёт запись, и инструмент по
    этому токену не идёт наружу вовсе.
    """
    own = env.ledger(7)
    token = own.mint(WIKI_PAGE, kind="search", subject="почему небо синее",
                     origin="wikipedia-opensearch-ru", origin_host=WIKI_HOST,
                     trusted_hosts=(WIKI_HOST,))
    assert token == "w1"
    assert own.resolve("w1") is not None

    stranger = env.ledger(8)
    assert stranger.resolve("w1") is None, "токен чужого прогона не имеет права резолвиться"
    assert stranger.resolve_with_reason("w1")[1] == "unknown"

    result = await open_tool(env, {"ref": "w1"}, run_id=8)
    assert result.error is True
    assert "ref_unknown" in result.content
    assert env.adapter.page_calls() == [], "по чужому токену наружу ходить нельзя"
    # Файл чужого прогона от неудачной попытки создаваться не должен: иначе
    # каждый промах модели оставлял бы на диске владельца пустой реестр.
    assert not env.run_file(8).exists()


async def test_reestr_perezhivaet_perezapusk_processa(env):
    """Вред самый дорогой: ветка `ask` паркует прогон и освобождает воркер, а
    пробуждение после одобрения приходит в ДРУГОЙ процесс. Реестр, живущий в
    памяти, теряется ровно в этот момент — и токен, который владелец только что
    одобрил, перестаёт резолвиться сразу после одобрения.

    «Другой процесс» здесь честный: новый объект сервисов над тем же каталогом
    данных. Вместе со ссылкой обязаны пережить перезапуск счётчики бюджета и
    отметка заражения — иначе перезапуск сбрасывал бы лимиты и раздавал бы
    бесплатные `w` после первого же чтения страницы.
    """
    first = env.ledger(11)
    token = first.mint(WIKI_PAGE, kind="search", subject="почему небо синее",
                       origin="wikipedia-opensearch-ru", origin_host=WIKI_HOST,
                       trusted_hosts=(WIKI_HOST,))
    assert first.spend("search") is True
    assert first.spend("search") is True
    first.mark_tainted()

    revived = ledger.Ledger.load(Machine(env.data_dir), 11)
    entry = revived.resolve(token)
    assert entry is not None, "после перезапуска одобренная ссылка обязана резолвиться"
    assert entry.url == WIKI_PAGE
    assert entry.host == WIKI_HOST
    assert revived.left()["search"]["used"] == 2, "перезапуск не возвращает потраченный бюджет"
    assert revived.tainted is True, "заражение обязано переживать перезапуск"

    # И обратная сторона того же свойства: реестр не появляется на диске сам.
    # Прогон, который ничего не чеканил, не оставляет файла вовсе.
    assert ledger.Ledger.load(Machine(env.data_dir), 12).refs() == []
    assert not env.run_file(12).exists()


# ------------------------------------------- A2: самоописывающий токен


async def test_l_token_nesyot_host_i_put_v_predprosmotre_odobreniya(env):
    """Вред (A2): владелец одобряет строку `l7`, не зная, куда она ведёт.

    `normalize_args` по сигнатуре чистая — ни `svc`, ни `run_id`, ни доступа к
    реестру, — поэтому резолвить `l7 → адрес` для предпросмотра ей нечем.
    Единственный способ показать назначение владельцу — держать его в САМОМ
    аргументе. Предпросмотр здесь собирается ровно так же, как его собирает
    движок: канонические аргументы через редактор секретов.
    """
    led = env.ledger()
    token = mint_link(led, "https://docs.example.org/guide/api")
    assert token.startswith("l"), "ссылка из тела страницы обязана быть l-токеном"
    assert token != "l1", "непрозрачный номер сделал бы одобрение слепым"

    preview = engine_preview(spec_of("web.open"), {"ref": token})
    assert DOCS_HOST in preview, "владелец обязан видеть ХОСТ назначения"
    assert "/guide/api" in preview, "владелец обязан видеть ПУТЬ назначения"
    # Редактор секретов не имеет права съесть назначение по дороге: показанное
    # владельцу и исполненное обязаны совпасть до знака.
    assert redact_text(preview) == preview

    # Тот же токен, дошедший до хука, требует одобрения и называет причину.
    effect = tools.open_effect({"ref": token})
    assert effect is not None and effect[0] == "ask"


async def test_approval_digest_menyaetsya_vmeste_s_naznacheniem_tokena(env):
    """Вред: `approval_digest` фиксирует строку аргумента. Будь токен
    непрозрачным номером, digest у `l1 → docs.example.org` и у
    `l1 → evil.example` совпал бы — то есть одобрение одного адреса подошло бы
    к походу по другому, и F-013 проверял бы номер вместо назначения.

    Здесь же проверяется и обратное свойство: косметическая описка модели
    (ведущий ноль) НЕ меняет digest, иначе identity-гард при пробуждении
    срабатывал бы на знаке, которого владелец не видел.
    """
    spec = spec_of("web.open")
    same_number_other_host = "l1@evil.example/guide/api"
    ours = "l1@docs.example.org/guide/api"
    assert config.parse_ref(ours) == ("l", DOCS_HOST, "/guide/api")

    digest_ours = approval_digest(spec, {"ref": ours})
    digest_theirs = approval_digest(spec, {"ref": same_number_other_host})
    assert digest_ours != digest_theirs, "digest обязан различать разные назначения"
    assert digest_ours == approval_digest(spec, {"ref": ours})
    assert digest_ours == approval_digest(spec, {"ref": "l01@docs.example.org/guide/api"})
    # Путь тоже часть назначения: `/guide/api` и `/guide/apikeys` — разные
    # страницы, и одобрение первой не является одобрением второй.
    assert digest_ours != approval_digest(spec, {"ref": "l1@docs.example.org/guide/apikeys"})


async def test_podmena_zapisi_reestra_posle_odobreniya_obnaruzhivaetsya(env):
    """Вред: между одобрением и исполнением запись реестра меняют, и владелец,
    одобривший `docs.example.org`, отправляет прогон на чужой хост. Паспорт
    наблюдения при этом назовёт источником страницу, которую никто не одобрял.

    Самоописание токена только тогда и является проверкой, когда расхождение
    токена с записью — ОТКАЗ. Проверяются обе половины назначения: хост и путь.
    """
    led = env.ledger()
    token = mint_link(led, "https://docs.example.org/guide/api")
    assert env.ledger().resolve(token) is not None, "контроль: нетронутая запись резолвится"

    # Подмена с другой стороны: запись цела, а номер в вызове снабдили чужим
    # хостом. Одобрен `l1@docs…`, исполняется `l1@evil…` — сверка обязана
    # поймать и это, иначе номер снова становится главным, а хвост украшением.
    spoofed = token.replace(DOCS_HOST, "evil.example")
    assert env.ledger().resolve_with_reason(spoofed) == (None, "mismatch")

    def rewrite(url: str) -> None:
        doc = json.loads(env.run_file().read_text(encoding="utf-8"))
        for row in doc["refs"]:
            if row["ref"] == token:
                row["url"] = url
                row["host"] = url.split("/")[2]
        env.run_file().write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    rewrite("https://evil.example/guide/api")
    entry, reason = env.ledger().resolve_with_reason(token)
    assert entry is None and reason == "mismatch", "подмена хоста обязана быть замечена"

    result = await open_tool(env, {"ref": token})
    assert result.error is True
    assert "ref_mismatch" in result.content
    assert env.adapter.page_calls() == [], "по расходящемуся токену наружу не ходят"

    rewrite("https://docs.example.org/guide/apikeys")
    entry, reason = env.ledger().resolve_with_reason(token)
    assert entry is None and reason == "mismatch", "подмена пути обязана быть замечена"


# ------------------------------------------------- B1: модель заражения


async def test_pervyi_poisk_daet_w_i_otkryvaetsya_bez_odobreniya(env):
    """Контрольная половина модели заражения: пока страница ещё не читалась,
    адрес из выдачи доверенного хоста выбирает backend, а не модель, и вопрос
    владельцу не добавил бы ему ни одного бита.

    Без этого теста следующий (про `l` после чтения) прошёл бы и на реестре,
    который вообще никогда не выдаёт `w`, — то есть не доказывал бы ничего.
    """
    env.adapter.route(WIKI_API, serp(WIKI_PAGE))
    result = await search_tool(env, {"query": "почему небо синее"})

    assert result.error is False, result.content
    assert result.data["refs"] == ["w1"]
    assert result.data["backend"] == "wikipedia-opensearch-ru"
    assert tools.open_effect({"ref": "w1"}) is None, "w-токен открывается без одобрения"
    assert env.ledger().tainted is False, "до первого чтения реестр не заражён"


async def test_poisk_posle_otkrytiya_stranicy_daet_l_i_trebuet_odobreniya(env):
    """Вред (B1) — второй прыжок через поиск. Посылка «по `ref` байтов модели в
    адресе ноль» ложна: `query` и `site` это аргументы модели, а выдача от них
    детерминирована. Инъекция со страницы говорит «поищи такое-то слово», и
    получает чтение выбранного ею хоста БЕЗ одобрения — с паспортом «пришли из
    Википедии».

    Лечение — заражение: первое же чтение страницы взводит `tainted`, и дальше
    даже выдача поиска даёт `l`, то есть вопрос владельцу с показом адреса.
    Заражение проверяется на живом конвейере, а не вызовом `mark_tainted`: иначе
    тест доказывал бы, что флаг умеет ставиться, а не что его ставит чтение.
    """
    # Второй поиск отвечает ДРУГИМ адресом того же доверенного хоста: повторный
    # адрес вернул бы уже отчеканенный `w1`, и тест доказывал бы не заражение, а
    # то, что токен не перечеканивается.
    env.adapter.route(asked("почему трава зелёная"), serp(WIKI_PAGE_2))
    env.adapter.route(WIKI_API, serp(WIKI_PAGE))
    env.adapter.route(WIKI_PAGE, PAGE_HTML)

    first = await search_tool(env, {"query": "почему небо синее"})
    assert first.data["refs"] == ["w1"]

    opened = await open_tool(env, {"ref": "w1"})
    assert opened.error is False, opened.content
    assert WIKI_PAGE in env.adapter.page_calls()
    assert env.ledger().tainted is True, "чтение страницы обязано заражать реестр"

    second = await search_tool(env, {"query": "почему трава зелёная"}, step=2)
    assert second.error is False, second.content
    refs = second.data["refs"]
    assert refs and all(ref.startswith("l") for ref in refs), (
        "после чтения страницы даже выдача поиска обязана требовать одобрения: "
        f"получено {refs}")
    for ref in refs:
        effect = tools.open_effect({"ref": ref})
        assert effect is not None and effect[0] == "ask", ref

    # Заражение необратимо и переживает перезапуск: иначе достаточно было бы
    # дождаться пробуждения в другом процессе, чтобы снова получать `w`.
    assert ledger.Ledger.load(Machine(env.data_dir), 7).tainted is True


# ------------------------------------------------- эффекты web.open


async def test_open_effect_url_ot_modeli_sprashivaet_a_ne_puskaet(env):
    """Контроль для двух следующих тестов: годный адрес не отвергается, но и в
    `auto` не едет. Без него «всё запрещено» выглядело бы как работающая
    политика, хотя это отказ от возможности, а не защита.
    """
    effect = tools.open_effect({"url": f"https://{DOCS_HOST}/guide/api"})
    assert effect is not None
    assert effect[0] == "ask", "адрес выбрала модель — владелец обязан его увидеть"


async def test_open_effect_deny_na_query_stroku(env):
    """Вред (A1): движок оборачивает предпросмотр в редактор секретов, а
    исполняет СЫРОЙ аргумент. Значит `?token=<секрет>` владельцу показан не
    будет, он одобрит очищенный адрес, а наружу уйдёт полный — защита
    анти-коррелирована с опасностью.

    Поэтому непустая строка параметров — отказ БЕЗ порога длины, и отказ обязан
    случиться в самом хуке: спросить про такой адрес честно нельзя.
    """
    leaky = f"https://{DOCS_HOST}/guide?token={CANARY}"
    # Показать, почему `ask` тут был бы ложью: предпросмотр вычистит ровно ту
    # часть, ради которой и спрашивают.
    assert redact_text(leaky) != leaky

    effect = tools.open_effect({"url": leaky})
    assert effect is not None and effect[0] == "deny", "адрес с параметрами не одобряют"
    # И безобидный параметр тоже: порога тут нет, потому что порог пришлось бы
    # объяснять владельцу, а «у документа нет нужды в ?» объяснять не надо.
    plain = tools.open_effect({"url": f"https://{DOCS_HOST}/guide?print=1"})
    assert plain is not None and plain[0] == "deny"


async def test_open_effect_deny_na_adres_kotoryi_izmenil_by_redact(env):
    """Тот же вред (A1) с другой стороны: секрет умеет жить и в ПУТИ, где
    строки параметров нет вовсе. Проверка «есть ли `?`» такой адрес пропустит,
    а предпросмотр всё равно покажет его владельцу без опасной части.

    Правило поэтому сформулировано не про синтаксис адреса, а про честность
    показа: изменил бы редактор секретов строку — значит показать её честно
    нельзя, значит и спрашивать нельзя.
    """
    in_path = f"https://{DOCS_HOST}/{CANARY}"
    assert "?" not in in_path and "#" not in in_path
    assert redact_text(in_path) != in_path

    effect = tools.open_effect({"url": in_path})
    assert effect is not None and effect[0] == "deny"

    # И то же самое на исполнении: правило владельца в `tool_rules` применяется
    # ПОСЛЕ хука и может вернуть эффект в `auto`, поэтому handler обязан быть
    # второй дверью, а не полагаться на первую.
    result = await open_tool(env, {"url": in_path})
    assert result.error is True
    assert env.adapter.page_calls() == [], "по такому адресу наружу не ходят вовсе"


# ------------------------------------- терпимость к битому вводу модели


async def test_bityi_json_ot_lokalnoi_modeli_ne_lomaet_vyzov(env):
    """Вред: раннер локальной модели отдаёт аргументы строкой, которая не
    разбирается как JSON, и разбор кладёт её под ключ `_raw`. Сегодня это отказ
    КАЖДОГО инструмента системы. Инструмент, который на этом падает исключением,
    роняет шаг прогона вместо того, чтобы объяснить модели, что не так.

    Проверяются три формы, в которых `_raw` реально приходит: сама фраза
    запроса, токен вместо объекта и обрывок JSON. Первые две обязаны РАБОТАТЬ,
    третья — честно отказать, но остаться данными, а не исключением.
    """
    env.adapter.route(WIKI_API, serp(WIKI_PAGE))
    env.adapter.route(WIKI_PAGE, PAGE_HTML)

    # (1) Модель напечатала фразу вместо объекта — поиск обязан состояться.
    phrase = await search_tool(env, {"_raw": "почему небо синее"})
    assert phrase.error is False, phrase.content
    assert phrase.data["refs"] == ["w1"]

    # (2) Модель напечатала один токен — это может быть только ссылка.
    assert tools.coerce_args({"_raw": "w1"}) == {"ref": "w1"}
    by_ref = await open_tool(env, {"_raw": "w1"})
    assert by_ref.error is False, by_ref.content
    assert by_ref.data["ref"] == "w1"

    # (3) Обрывок JSON: отказ данными, а не исключением, и наружу не ушло.
    before = len(env.adapter.calls)
    broken = await search_tool(env, {"_raw": '{"query": "цена rtx 4090'}, step=3)
    assert broken.error is True
    assert "query_refused" in broken.content
    assert env.adapter.calls[before:] == [], "отвергнутый запрос наружу не уходит"


async def test_normalize_args_i_handler_dayut_odnu_kanonicheskuyu_formu(env):
    """Вред: движок показывает владельцу `normalize_args(args)`, а исполняет
    handler с СЫРЫМИ аргументами. Разойдись эти две формы — и владелец одобряет
    одно, а исполняется другое; при пробуждении после одобрения расхождение
    вдобавок ловится identity-гардом и превращается в отказ на ровном месте.

    Проверяется по обеим веткам web.open: по токену (описка модели в номере) и
    по адресу (регистр хоста и завершающая точка). Совпадение доказывается не
    сравнением функции с самой собой, а тем, что наружу ушёл ИМЕННО тот адрес,
    который был бы напечатан в предпросмотре.
    """
    spec = spec_of("web.open")
    env.adapter.route(WIKI_API, serp(WIKI_PAGE))
    env.adapter.route(WIKI_PAGE, PAGE_HTML)
    env.adapter.route(DOCS_PAGE, PAGE_HTML)

    # Ветка токена: синоним ключа и ведущий ноль приводятся к одной форме.
    await search_tool(env, {"query": "почему небо синее"})
    assert normalized_args(spec, {"id": "w01"}) == {"ref": "w1"}
    assert approval_digest(spec, {"id": "w01"}) == approval_digest(spec, {"ref": "w1"})
    opened = await open_tool(env, {"id": "w01"})
    assert opened.error is False, opened.content
    assert opened.data["ref"] == "w1"

    # Ветка адреса: канонизация обязана быть ОДНА на предпросмотр и исполнение.
    raw_url = "https://DOCS.Example.ORG./guide/api"
    shown = normalized_args(spec, {"address": raw_url})
    assert shown["url"] == DOCS_PAGE, "предпросмотр показывает канонический адрес"

    result = await open_tool(env, {"address": raw_url})
    assert result.error is False, result.content
    assert DOCS_PAGE in env.adapter.page_calls(), (
        "наружу обязан уйти ровно тот адрес, который показан владельцу")
    entry = env.ledger().resolve(result.data["ref"])
    assert entry is not None and entry.url == shown["url"]
