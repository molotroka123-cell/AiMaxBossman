"""web_research: граница выключенного режима и честность нулевой настройки.

Здесь проверяется не «фича работает», а два обещания, которые дороже любой
функциональности и ломаются тише всего.

Первое: **выключенный флаг = приложение ведёт себя ровно как до модуля.**
Ловится вред, который иначе заметят через месяц: инструмент `web.*`, попавший
в реестр процесса и, значит, в схемы модели; хук `gate_completion`, встрявший
в ЧУЖИЕ прогоны; подменённый транспорт OSIRIS, из-за которого чужие сборы
пойдут другим путём; каталог `osiris/web_runs` на диске владельца, о котором
он не просил. Ни одно из этих последствий не выглядит как ошибка — они
выглядят как «всё работает», и поэтому каждое проверяется отдельно, а не
одним «фича выключена».

Второе: **нулевая настройка отвечает правдой, а не пустотой.** Владелец
включил оба флага и не настроил ничего. Вред здесь — тихая подмена: сказать
модели «источников нет» одним текстом, а владельцу в интерфейсе другим; выдать
энциклопедию за общий веб-поиск; напечатать хоть один адрес, которого никто не
получал. Поэтому текст готовности сверяется ДОСЛОВНО (владелец и модель
получают одну и ту же строку, а не две похожих), а ответ «искать негде»
проверяется на отсутствие ссылок и на прямой запрет выдумывать.

И поправка E2: `normalize_source` считает отсутствующий `cache_ttl_seconds`
нулём, а ноль означает `expires_at == fetched_at`, то есть `raw_is_fresh`
навсегда False и кэша нет вовсе. Пустой параметр `fresh` при этом продолжает
выглядеть работающим. Тест перебирает ВСЕ объявленные backend'ы и требует
ненулевой TTL — и показывает на живом `raw_is_fresh`, почему ноль недопустим.

Чего этот файл НЕ проверяет (у этого есть свои файлы): матрицу эффектов и
реестр ссылок, сетевой конвейер, происхождение наблюдений, защиту от инъекций.
Сети здесь нет ни в одном тесте: транспорт либо не подменяется вовсе
(выключенная фича), либо подменяется считающим вызовы стендом.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import pytest

from bcc.features import osiris
from bcc.features.web_research import FEATURE, config, net, render, sources
from bcc.tools import EXTERNAL_DATA_HEADER, REGISTRY, ToolContext, execute_tool
from .conftest import client_for, make_settings, start_app

# Ручки делятся не по глаголу HTTP, а по последствию: читающие обязаны
# отвечать «выключено» и не трогать диск, мутирующие — отказывать 409.
READ_ROUTES = (
    "/api/web",
    "/api/web/sources",
    "/api/web/episodes",
    "/api/web/trail?subject=web:проба",
    "/api/web/citations/20260101T000000-abcd",
    "/api/web/raw/" + "ab" * 32,
    "/api/web/ledger/7",
)
WRITE_ROUTES = (
    ("post", "/api/web/search", {"query": "цена rtx 4090"}),
    ("post", "/api/web/refs", {"run_id": "7", "url": "https://example.com/a"}),
    ("post", "/api/web/recheck", {"observation_id": "20260101T000000-abcd"}),
    ("post", "/api/web/preflight", {}),
    ("delete", "/api/web/ledger/7", None),
    ("delete", "/api/web/episodes/proba", None),
)

TOOL_NAMES = ("web.search", "web.open", "web.find", "web.cite")
API_NAMES = {"web.search": "web_search", "web.open": "web_open",
             "web.find": "web_find", "web.cite": "web_cite"}


# --------------------------------------------------------------- стенд


class CountingAdapter:
    """Транспорт, который считает обращения и не ходит в сеть.

    `live = False` не косметика: настоящий транспорт имеет право пометить
    источник рабочим, стенд — не имеет, и тест, доказывающий «наружу не
    ходили», обязан ставить именно стенд, а не полагаться на удачу.
    """

    live = False

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, url: str, *, headers=None, timeout: float = 15.0):
        self.calls.append(url)
        return osiris.FetchResult(status=200, body="{}", url=url)


class Env:
    def __init__(self, app, svc, client, settings) -> None:
        self.app = app
        self.svc = svc
        self.client = client
        self.settings = settings

    @property
    def data_dir(self) -> Path:
        return Path(self.settings.data_dir)

    @property
    def osiris_dir(self) -> Path:
        return self.data_dir / osiris.DIRNAME


@pytest.fixture(autouse=True)
def _isolate_process_tables():
    """Реестр инструментов и таблица парсеров — глобальные на процесс.

    Без уборки тест с включённым флагом оставил бы `web.*` в `REGISTRY`, и
    следующий тест с выключенным флагом увидел бы их и прошёл бы, ничего не
    доказав, — или, наоборот, упал бы в чужом файле. Снимок берётся до, разница
    снимается после.
    """
    tools_before = set(REGISTRY.names())
    parsers_before = dict(osiris.PARSERS)
    yield
    for name in set(REGISTRY.names()) - tools_before:
        REGISTRY.unregister(name)
    osiris.PARSERS.clear()
    osiris.PARSERS.update(parsers_before)


@asynccontextmanager
async def _stand(tmp_path, monkeypatch, *, web: bool, osiris_on: bool):
    """Приложение с заданными флагами. Флаги ставятся ДО старта: `setup()`
    зовётся из `Services.start()`, и переключить флаг после старта означало бы
    проверять не то состояние, которое собирались."""
    assert config.env_errors() == (), (
        "в окружении разработчика испорчены переменные BOSSMAN_WEB_*; "
        "нулевая настройка в тестах должна быть действительно нулевой")
    for name, want in ((config.FLAG, web), (config.OSIRIS_FLAG, osiris_on)):
        if want:
            monkeypatch.setenv(name, "1")
        else:
            monkeypatch.delenv(name, raising=False)
    # Свой SearXNG — единственный честный путь к общему веб-поиску. Если он
    # задан у разработчика, «нулевая настройка» перестаёт быть нулевой.
    monkeypatch.setattr(config, "SEARXNG_URL", "")
    settings = make_settings(tmp_path)
    app, svc = await start_app(settings, start_workers=False)
    try:
        async with client_for(app, svc) as client:
            yield Env(app, svc, client, settings)
    finally:
        await svc.stop()


@pytest.fixture
async def off(tmp_path, monkeypatch):
    """Оба флага сняты — состояние по умолчанию у каждого владельца."""
    async with _stand(tmp_path, monkeypatch, web=False, osiris_on=False) as env:
        yield env


@pytest.fixture
async def web_only(tmp_path, monkeypatch):
    """Владелец включил веб-поиск и забыл про слой происхождения."""
    async with _stand(tmp_path, monkeypatch, web=True, osiris_on=False) as env:
        yield env


@pytest.fixture
async def osiris_only(tmp_path, monkeypatch):
    """Слой происхождения включён, веб-поиск — нет. Обычное состояние владельца,
    который пользуется OSIRIS и о вебе не просил: именно оно отделяет проверку
    СВОЕГО флага от проверки чужого."""
    async with _stand(tmp_path, monkeypatch, web=False, osiris_on=True) as env:
        yield env


@pytest.fixture
async def both(tmp_path, monkeypatch):
    """Оба флага включены, не настроено НИЧЕГО: ни SearXNG, ни ключей."""
    async with _stand(tmp_path, monkeypatch, web=True, osiris_on=True) as env:
        yield env


def _web_tools() -> list[str]:
    return [name for name in REGISTRY.names() if name.startswith("web.")]


def _our_hooks(svc) -> list[str]:
    return [getattr(fn, "__module__", "") for fn in svc.engine.hooks["gate_completion"]
            if str(getattr(fn, "__module__", "")).startswith("bcc.features.web_research")]


def _tree(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*")}


# -------------------------------------------------- выключенный флаг


async def test_disabled_feature_registers_no_tools(off):
    """Вред: инструмент, попавший в реестр процесса, попадает и в схемы модели.

    Модель не может «не заметить» инструмент: она видит ровно то, что отдаёт
    `REGISTRY.resolve`, и один зарегистрированный `web.search` при выключенном
    флаге означает, что модель начнёт его звать, а владелец об этом не просил.
    """
    assert _web_tools() == []
    assert REGISTRY.resolve(["web.*"]) == []
    # Даже агент с правом на ВСЁ не должен получить ни одной схемы web.*:
    # «выключено» обязано быть свойством, а не следствием узкого списка.
    names = [s["function"]["name"] for s in REGISTRY.schemas_for(["*"])]
    assert [n for n in names if n.startswith("web_")] == []


async def test_disabled_feature_installs_no_hook_and_no_adapter(off):
    """Вред: хук `gate_completion` и подменённый транспорт бьют по ЧУЖИМ прогонам.

    Хук стоит в списке критичных: он вмешивается в завершение любой задачи, в
    том числе не имеющей отношения к вебу. Транспорт OSIRIS общий: подменив
    его, выключенная фича изменила бы сборы чужих источников. Ни того, ни
    другого при снятом флаге быть не должно.
    """
    assert _our_hooks(off.svc) == []
    store = osiris.store(off.svc)
    assert not isinstance(store.adapter, net.WebFetchAdapter)
    assert type(store.adapter) is osiris.HttpFetchAdapter
    # Чужая таблица парсеров тоже обязана остаться нетронутой: запись в ней
    # разрешает объявить наш источник, то есть открывает путь к сети.
    assert [k for k in osiris.PARSERS if k.startswith("web.")] == []


async def test_disabled_feature_creates_no_file_on_disk(off):
    """Вред: каталог на диске владельца, о котором он не просил.

    Проверяется не «нет каталога сразу после старта», а «его нет ПОСЛЕ того,
    как фичу подёргали за все ручки»: установка, фоновый тик, каждая читающая
    ручка и каждая мутирующая. Сравнивается набор путей, а не содержимое
    файлов: база данных приложения пишется своим кодом и к фиче отношения не
    имеет, а вот НОВЫЙ путь может появиться только от того, кого дёргали.
    """
    before = _tree(off.data_dir)
    await FEATURE.setup(off.svc)
    await FEATURE.tick(off.svc)
    for route in READ_ROUTES:
        assert (await off.client.get(route)).status_code == 200
    for method, route, body in WRITE_ROUTES:
        kw = {"json": body} if body is not None else {}
        await getattr(off.client, method)(route, **kw)
    after = _tree(off.data_dir)

    assert after == before, f"выключенная фича создала пути: {sorted(after - before)}"
    assert not off.osiris_dir.exists()
    assert [p for p in after if "web_run" in p or p.startswith("osiris")] == []


async def test_disabled_read_routes_answer_enabled_false(off):
    """Вред: ручка, отвечающая пустым списком вместо «выключено».

    Пустой список владелец читает как «ничего не найдено», то есть как факт о
    своих данных. Это ровно та подмена «нет настройки» на «нет результата»,
    которую фича обязана исключать, и начинается она с ответа ручки.
    """
    for route in READ_ROUTES:
        response = await off.client.get(route)
        assert response.status_code == 200, route
        body = response.json()
        assert body["enabled"] is False, route
        assert body["flags"]["web"] == config.FLAG, route

    state = (await off.client.get("/api/web")).json()
    assert state["readiness"]["code"] == "feature_disabled"
    assert config.FLAG in state["readiness"]["text"]
    # Счётчики и бюджеты не «нули», а `null`: ноль — это измерение, которого
    # не было, и выдавать его за измеренное нельзя.
    assert state["counts"] is None and state["budget"] is None


async def test_disabled_write_routes_refuse_with_409(off):
    """Вред: мутирующая ручка, тихо сделавшая работу при выключенном флаге.

    409 здесь обязателен вместе с ИМЕНЕМ недостающего флага: отказ без имени
    отправляет владельца читать исходники, а фича обещает обратное.
    """
    for method, route, body in WRITE_ROUTES:
        kw = {"json": body} if body is not None else {}
        response = await getattr(off.client, method)(route, **kw)
        assert response.status_code == 409, f"{method} {route}"
        detail = response.json()["error"]
        assert detail["code"] == "feature_disabled", route
        assert detail["flag"] == config.FLAG, route


async def test_osiris_alone_does_not_switch_on_the_web_feature(osiris_only):
    """Вред: чужой включённый флаг, включивший заодно и веб-поиск.

    Владелец, пользующийся слоем происхождения, веб-поиска не просил. Проверка
    именно СВОЕГО флага живёт только здесь: при обоих снятых флагах фичу
    удерживает выключенным ещё и требование OSIRIS, поэтому потеря собственной
    проверки там осталась бы незамеченной.
    """
    assert _web_tools() == []
    assert _our_hooks(osiris_only.svc) == []
    assert not isinstance(osiris.store(osiris_only.svc).adapter, net.WebFetchAdapter)
    assert [k for k in osiris.PARSERS if k.startswith("web.")] == []
    assert not config.runs_dir(osiris_only.svc).exists()

    state = (await osiris_only.client.get("/api/web")).json()
    assert state["enabled"] is False
    assert state["readiness"]["code"] == "feature_disabled"
    assert state["flags"]["osiris_enabled"] is True      # чужой флаг именно включён

    response = await osiris_only.client.post("/api/web/search", json={"query": "проба"})
    assert response.status_code == 409
    assert response.json()["error"]["flag"] == config.FLAG


# ------------------------------------------- флаг ON, OSIRIS OFF


async def test_web_flag_without_osiris_refuses_by_name(web_only):
    """Вред: непонятный отказ на пол-настройки.

    Владелец включил то, что просили, и получил тишину. Отказ обязан назвать
    ВТОРОЙ флаг и причину, по которой он не формальность: без слоя
    происхождения наблюдения легли бы в каталоги OSIRIS, чьи ручки при
    выключенном флаге отвечают 409, — данные, которые нельзя ни посмотреть, ни
    удалить.
    """
    state = (await web_only.client.get("/api/web")).json()
    assert state["enabled"] is True                   # свой флаг владелец включил
    assert state["readiness"]["code"] == "osiris_disabled"
    assert state["readiness"]["ok"] is False
    text = state["readiness"]["text"]
    assert config.OSIRIS_FLAG in text
    assert "происхождения" in text

    for method, route, body in WRITE_ROUTES:
        kw = {"json": body} if body is not None else {}
        response = await getattr(web_only.client, method)(route, **kw)
        assert response.status_code == 409, f"{method} {route}"
        detail = response.json()["error"]
        assert detail["code"] == "osiris_disabled", route
        assert detail["flag"] == config.OSIRIS_FLAG, route


async def test_web_flag_without_osiris_changes_nothing(web_only):
    """Вред: половина фичи, поставленная «пока суд да дело».

    Частично установленная фича хуже отсутствующей: она выглядит установленной,
    а её половина молчит. Поэтому при недостающем втором флаге не должно быть
    ни инструментов, ни хука, ни подменённого транспорта, ни каталога.
    """
    assert _web_tools() == []
    assert _our_hooks(web_only.svc) == []
    assert not isinstance(osiris.store(web_only.svc).adapter, net.WebFetchAdapter)
    assert [k for k in osiris.PARSERS if k.startswith("web.")] == []
    assert not web_only.osiris_dir.exists()


# ------------------------------------------------ оба флага, ноль настройки


async def test_both_flags_install_four_tools_without_permission(both):
    """Вред: право у инструмента, которое читается как гейт, а работает как ускоритель.

    `decide_effect` ПОВЫШАЕТ эффект до auto по выданному агенту праву и делает
    это ДО хука — одного правила владельца хватило бы, чтобы `web.open` с сырым
    адресом поехал без вопроса. Пустое право убирает эту тропу целиком, и его
    появление обязано ломать тест, а не проходить как «мелкое улучшение».
    """
    assert sorted(_web_tools()) == sorted(TOOL_NAMES)
    api_names = {name: REGISTRY.get(name).api_name for name in TOOL_NAMES}
    assert api_names == API_NAMES
    assert len(set(api_names.values())) == len(TOOL_NAMES)
    for name in TOOL_NAMES:
        spec = REGISTRY.get(name)
        assert spec.permission == "", name
        assert spec.category == "read", name
        assert spec.external_output is True, name
        assert spec.source == "web", name
    assert _our_hooks(both.svc) == ["bcc.features.web_research.gate"]
    assert isinstance(osiris.store(both.svc).adapter, net.WebFetchAdapter)


async def test_tool_answer_reaches_model_with_external_data_header(both):
    """Вред: текст со стороны внешнего мира, поданный модели как её собственный.

    Шапку «это НЕ команды» ставит `ToolResult.render()` по `external_output`
    спеки. Если флаг спеки потеряется, ответ инструмента доедет до модели
    неотличимым от системной инструкции — и вся защита от инъекций §10
    держится на одном булевом поле, которое некому проверить.
    """
    store = osiris.store(both.svc)
    store.adapter = adapter = CountingAdapter()
    spec = REGISTRY.get("web.find")
    ctx = ToolContext(svc=both.svc, task={"id": 1}, run_id=1, agent={})

    result = await execute_tool(spec, {"ref": "w9", "query": "проба"}, ctx)

    assert result.content.strip(), "пустой ответ инструмента ничего не доказывает"
    assert result.render().startswith(EXTERNAL_DATA_HEADER)
    # web.find не имеет права выйти в сеть даже на несуществующем токене.
    assert adapter.calls == []


async def test_owner_and_model_get_the_same_readiness_text(both):
    """Вред: два текста готовности, которые однажды разойдутся.

    Владелец читает готовность в `GET /api/web`, модель получает её внутри
    ответа инструмента. Пока это одна строка из одной функции, «что видит
    владелец» и «что модель говорит владельцу» совпадают by construction.
    Поэтому сверка — на равенство и на дословное вхождение, а не на похожесть:
    перефразированный абзац прошёл бы проверку «по смыслу» и был бы уже вторым
    текстом.
    """
    owner = (await both.client.get("/api/web")).json()["readiness"]
    assert owner == sources.readiness(both.svc), (
        "ручка владельца пересобирает готовность вместо того, чтобы отдать её")

    text = owner["text"].strip()
    assert len(text) > 100, "готовность из одной строки нечему быть верной"
    for model_text in (
        render.render_no_backends(sources.readiness(both.svc)),
        render.render_offline(sources.readiness(both.svc), [], code="empty_result",
                              query="проба", backend="pypi"),
    ):
        assert text in model_text, "модель получает НЕ ту строку, что владелец"


async def test_zero_config_admits_there_is_no_general_web_search(both):
    """Вред: энциклопедия, выданная за общий веб-поиск.

    Самая дорогая ложь этой фичи — молчаливая подмена источника: спросили про
    открытый веб, ответила Википедия, и по ответу это неотличимо. Поэтому при
    нулевой настройке готовность обязана прямо сказать, что общего веб-поиска
    нет, и назвать оба честных пути его получить.
    """
    ready = sources.readiness(both.svc)
    assert ready["code"] == "ready_keyless"
    assert ready["general_web"] == [], "общий веб-поиск не может быть готов без настройки"
    assert ready["searxng_configured"] is False
    assert ready["backends_ready"] == ready["keyless_ready"] > 0

    text = ready["text"]
    assert "НЕ общий веб-поиск" in text
    assert "Общий веб-поиск НЕДОСТУПЕН" in text
    assert "BOSSMAN_WEB_SEARXNG_URL" in text and "Brave" in text

    # Источники общего веба видны в реестре и названы неготовыми С ПРИЧИНОЙ:
    # молча пропущенный источник читается как несуществующий.
    rows = {row["id"]: row for row in ready["backends"]}
    for source_id in ("searxng-local", "brave-search"):
        assert rows[source_id]["ready"] is False, source_id
        assert rows[source_id]["reason"].strip(), source_id
        assert rows[source_id]["general_web"] is True, source_id


async def test_nowhere_to_search_invents_nothing(both):
    """Вред: выдуманный источник в ответе «искать негде».

    Выдуманная ссылка хуже честного «не знаю»: её нельзя ни проверить, ни
    опровергнуть, а выглядит она как результат. Ответ при нуле готовых
    источников обязан не содержать НИ ОДНОГО адреса и НИ ОДНОГО токена выдачи,
    прямо запрещать выдумывание и заканчивать прогон, а не запускать его по
    кругу.
    """
    text = render.render_no_backends(config.readiness(backends=[], osiris_on=True))
    assert "Я НЕ ИСКАЛ В ИНТЕРНЕТЕ" in text
    assert "НЕ ВЫДУМЫВАЙ" in text
    assert "http://" not in text and "https://" not in text
    assert "ДАЛЬШЕ: ничего. Заверши ответ." in text
    for token in ("w1", "l1", "[w1]"):
        assert token not in text, token


async def test_narrow_source_is_not_stretched_to_a_foreign_host(both):
    """Вред: тихая подстановка одного источника вместо другого.

    «Сузить Википедию до docs.evil.example» невозможно. Сделать вид, что
    получилось, — значит выдать статью энциклопедии за содержимое чужого сайта.
    Отказ здесь честнее подмены, и он обязан случиться ДО сети.
    """
    store = osiris.store(both.svc)
    store.adapter = adapter = CountingAdapter()

    assert sources.pick_backend(both.svc, "цена rtx 4090", "docs.evil.example") is None

    spec = REGISTRY.get("web.search")
    ctx = ToolContext(svc=both.svc, task={"id": 1}, run_id=1, agent={})
    result = await execute_tool(
        spec, {"query": "цена rtx 4090", "site": "docs.evil.example"}, ctx)

    assert result.error is True
    assert "docs.evil.example" in result.content
    assert adapter.calls == [], "отказ обязан случиться до единого обращения наружу"
    assert not (both.osiris_dir / config.DIRNAME).exists()


# ------------------------------------------------------------------ E2


def test_every_declared_backend_carries_a_positive_cache_ttl():
    """Вред (E2): нулевой TTL — это отсутствие кэша при работающем на вид `fresh`.

    `normalize_source` считает отсутствующий ключ нулём, `write_raw` пишет
    `expires_at = fetched_at + 0`, и `raw_is_fresh` навсегда False. Снаружи это
    выглядит как исправный кэш с мёртвым параметром `fresh`: каждое повторное
    чтение идёт в сеть, тратит лимит хоста и суточный бюджет — и никто не
    видит причины. Поэтому TTL проверяется у КАЖДОГО объявления, а не у одного
    образца, и отдельно — у автосоздаваемого источника-на-хост.
    """
    osiris.PARSERS.update(sources.parsers())          # уберёт autouse-фикстура

    declared = [b for b in sources.BACKENDS if b.decl is not None]
    assert len(declared) >= 5, "перебирать нечего — список backend'ов подменён"

    for backend in declared:
        ttl = backend.decl.get("cache_ttl_seconds")
        assert isinstance(ttl, int) and not isinstance(ttl, bool), backend.id
        assert ttl > 0, f"{backend.id}: без TTL кэша нет вовсе"
        # Решает не декларация, а то, что из неё сделает чужая функция.
        assert osiris.normalize_source(sources.decl_of(backend)).cache_ttl_seconds > 0

    # Backend без декларации обязан объяснить, почему её нет: иначе цикл выше
    # молча пропустил бы настоящее объявление с нулевым TTL.
    for backend in sources.BACKENDS:
        if backend.decl is None:
            assert backend.via == "private_door", backend.id

    host = osiris.normalize_source(sources.host_source_decl("example.com"))
    assert host.cache_ttl_seconds > 0


def test_zero_ttl_means_raw_is_never_fresh(tmp_path):
    """Почему ноль в E2 недопустим — показано на самой чужой функции.

    Утверждение «без ненулевого TTL кэша нет» проверяется не пересказом, а
    вызовом `raw_is_fresh` на записи, какую сделал бы `write_raw` при TTL=0 и
    при TTL=300. Иначе тест выше остался бы проверкой числа больше нуля, а не
    проверкой последствия.
    """
    root = tmp_path / "хранилище-которого-нет"
    store = osiris.OsirisStore(root)
    now = osiris.utcnow()

    assert store.raw_is_fresh({"expires_at": now.isoformat()}) is False
    assert store.raw_is_fresh(
        {"expires_at": (now + timedelta(seconds=config.CACHE_TTL_SEARCH)).isoformat()}) is True
    assert config.CACHE_TTL_SEARCH > 0 and config.CACHE_TTL_PAGE > 0
    # Создание хранилища не имеет права трогать диск: читающая ручка при
    # выключенном флаге зовёт `store()` и обязана остаться без последствий.
    assert not root.exists()


# ------------------------------------------------- сторож против эрозии


def test_module_does_not_grow_a_second_copy_of_shared_machinery():
    """Вред: второй экземпляр того, что уже есть в проекте.

    Свой HTTP-клиент обошёл бы `plugin_security` вместе со всей защитой от
    SSRF; свой `RobotFileParser` — fail-closed поведение OSIRIS; свой каталог
    наблюдений — право владельца на удаление. Каждая такая копия появляется как
    удобство и обнаруживается как дыра, поэтому проверяется грепом по исходникам
    пакета, а не договорённостью.
    """
    package = Path(__file__).resolve().parents[1] / "bcc" / "features" / "web_research"
    files = sorted(package.glob("*.py"))
    assert len(files) >= 8, "пакет фичи не найден или неполон"

    forbidden = ("httpx.AsyncClient", "aiohttp", "RobotFileParser", "urllib.request",
                 "requests.get")
    for path in files:
        source = path.read_text(encoding="utf-8")
        # Комментарии на русском объясняют, ПОЧЕМУ мы этого не делаем, и
        # содержат те же слова — сравниваем только код.
        code = "\n".join(line.split("#", 1)[0] for line in source.splitlines())
        for marker in forbidden:
            assert marker not in code, f"{path.name}: своя копия {marker}"
