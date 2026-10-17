"""Каталог проверенных MCP-серверов: топ-10, которые владелец ставит одной кнопкой.

Это НЕ второй MCP-клиент. Каталог только описывает известные серверы и записывает
выбранный в ту же таблицу `mcp_servers`, что и `POST /api/mcp/servers`; запуск,
политика разрешений (`mcp.policy`), санитизация описаний и удаление остаются в
`tools_mcp` / `skills`. Команда запуска проходит тот же allowlist бинарников (F-014)
ДО записи в БД.

Правила каталога:
  * версии закреплены (`pkg==x` / `pkg@x`): «latest» молча меняет поведение;
  * ключей нет ни у одного сервера — секреты в каталог не попадают;
  * серверам с доступом к диску/репозиторию нужен явный путь владельца
    (`param`), корень диска и домашняя папка отклоняются;
  * ещё ~100 кандидатов описаны в docs/integrations/PUBLIC_API_MCP_BACKLOG_100_RU.md
    и сюда НЕ входят, пока не пройдут ту же проверку.
"""
from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field

import sqlalchemy as sa
from fastapi import APIRouter, HTTPException, Request

from ..db import utcnow
from ..v2.mcp_hub import MCPServerSpec, command_policy_refusal
from ..v2.tables import mcp_servers as mcp_servers_t
from . import Feature

router = APIRouter(prefix="/mcp/catalog", tags=["mcp"])


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    title: str
    runner: str                    # "npx" | "uvx"
    package: str                   # уже с закреплённой версией
    purpose: str
    risk: str                      # read | network | write
    upstream: str
    extra_args: tuple[str, ...] = ()
    param: str = ""                # имя обязательного параметра-пути ("" = нет)
    param_flag: str = ""           # флаг перед путём ("" = путь позиционный)
    marker: str = ""               # что обязано лежать в папке (".git" для репозитория)
    needs: tuple[str, ...] = field(default_factory=tuple)
    script: str = ""               # runner "node24": entry file inside node_modules (packages pre-installed, versions pinned)

    def available(self) -> bool:
        if self.runner != "node24":
            return True
        node, prefix = os.environ.get("BOSSMAN_NODE24", ""), os.environ.get("BOSSMAN_MCP_NODE24_PREFIX", "")
        return bool(node and prefix and Path(node).is_file() and (Path(prefix) / "node_modules" / self.script).is_file())

    def argv(self, path: str = "") -> list[str]:
        if self.runner == "node24":
            head = [os.environ.get("BOSSMAN_NODE24", ""),
                    str(Path(os.environ.get("BOSSMAN_MCP_NODE24_PREFIX", "")) / "node_modules" / self.script)]
            return [*head, *self.extra_args]
        head = ["npx", "-y", self.package] if self.runner == "npx" else ["uvx", self.package]
        argv = [*head, *self.extra_args]
        if self.param:
            argv += ([self.param_flag, path] if self.param_flag else [path])
        return argv

    def public(self, installed: bool) -> dict:
        return dict(id=self.id, title=self.title, purpose=self.purpose, risk=self.risk,
                    runner=self.runner, package=self.package, upstream=self.upstream,
                    param=self.param, needs=list(self.needs), installed=installed, available=self.available())


CATALOG: tuple[CatalogEntry, ...] = (
    CatalogEntry("fetch", "Fetch — страницы в Markdown", "uvx", "mcp-server-fetch==2026.10.10",
                 "Читает веб-страницы и превращает их в текст для модели.", "network",
                 "https://github.com/modelcontextprotocol/servers/tree/main/src/fetch"),
    CatalogEntry("time", "Time — время и часовые пояса", "uvx", "mcp-server-time==2026.10.10",
                 "Точное время и пересчёт между часовыми поясами без сети.", "read",
                 "https://github.com/modelcontextprotocol/servers/tree/main/src/time"),
    CatalogEntry("git", "Git — чтение и коммиты репозитория", "uvx", "mcp-server-git==2026.10.10",
                 "Статус, diff, лог и коммиты ОДНОГО репозитория, который выбрал владелец.", "write",
                 "https://github.com/modelcontextprotocol/servers/tree/main/src/git",
                 param="repository", param_flag="--repository", marker=".git"),
    CatalogEntry("memory", "Memory — граф знаний", "npx", "@modelcontextprotocol/server-memory@1.0.0",
                 "Простая долговременная память агента в виде графа сущностей.", "read",
                 "https://github.com/modelcontextprotocol/servers/tree/main/src/memory"),
    CatalogEntry("sequential-thinking", "Sequential Thinking — пошаговое рассуждение", "npx",
                 "@modelcontextprotocol/server-sequential-thinking@1.0.0",
                 "Структурирует длинные рассуждения для слабых локальных моделей.", "read",
                 "https://github.com/modelcontextprotocol/servers/tree/main/src/sequentialthinking"),
    CatalogEntry("filesystem", "Filesystem — файлы в одной папке", "npx",
                 "@modelcontextprotocol/server-filesystem@1.0.0",
                 "Чтение и запись файлов ТОЛЬКО внутри папки, которую указал владелец.", "write",
                 "https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem",
                 param="root"),
    CatalogEntry("context7", "Context7 — актуальная документация библиотек", "npx",
                 "@upstash/context7-mcp@4.4.2",
                 "Свежие доки и примеры кода библиотек вместо устаревших знаний модели.", "network",
                 "https://github.com/upstash/context7"),
    CatalogEntry("playwright", "Playwright — управление браузером", "npx", "@playwright/mcp@0.0.83",
                 "Открывает страницы и кликает по ним в отдельном браузере без вашего профиля.", "write",
                 "https://github.com/microsoft/playwright-mcp", extra_args=("--headless", "--isolated"),
                 needs=("браузер Chromium (npx playwright install chromium)",)),
    CatalogEntry("duckduckgo", "DuckDuckGo — поиск без ключа", "uvx", "duckduckgo-mcp-server==0.7.0",
                 "Веб-поиск и чтение найденных страниц без API-ключа.", "network",
                 "https://github.com/nickclyde/duckduckgo-mcp-server"),
    CatalogEntry("arxiv", "arXiv — научные статьи", "uvx", "arxiv-mcp-server==0.8.2",
                 "Поиск и чтение статей arXiv; скачанные PDF лежат в локальной папке сервера.", "network",
                 "https://github.com/blazickjp/arxiv-mcp-server"),
    # Публичные API без ключа (по списку public-apis), обёрнутые готовыми MCP-серверами.
    CatalogEntry("open-meteo", "Open-Meteo — погода, качество воздуха, высоты", "npx",
                 "open-meteo-mcp-server@2.5.2",
                 "Прогноз и архив погоды, воздух, море, геокодинг по бесплатному API без ключа.", "network",
                 "https://github.com/cmer81/open-meteo-mcp"),
    CatalogEntry("wikipedia", "Wikipedia — статьи и справки", "uvx", "wikipedia-mcp==2.0.1",
                 "Поиск, краткое содержание и факты из Википедии по открытому API без ключа.", "network",
                 "https://github.com/rudra-ravi/wikipedia-mcp"),
    # Procurement / geo / trade (War Terminal context). Need Node >= 22-24: run on the separate runtime/node24 with packages
    # pre-installed at exact versions in BOSSMAN_MCP_NODE24_PREFIX (npx on the system Node 20 crashes for them). Verified live 17.10.2026.
    CatalogEntry("osm", "OpenStreetMap — геокодинг и маршруты", "node24", "osm-mcp@0.3.2",
                 "Геокодинг (Nominatim/Photon), маршруты (OSRM/Valhalla), объекты рядом (Overpass) без ключа.", "network",
                 "https://github.com/ni-c/osm-mcp", script="osm-mcp/dist/index.js", needs=("Node 24 runtime",)),
    CatalogEntry("worldbank", "World Bank — показатели стран и проекты", "node24", "@cyanheads/worldbank-mcp-server@0.5.3",
                 "Показатели World Bank (ВВП, военные расходы и др.) и поиск проектов.", "network",
                 "https://github.com/cyanheads/worldbank-mcp-server", script="@cyanheads/worldbank-mcp-server/dist/index.js",
                 needs=("Node 24 runtime",)),
    CatalogEntry("ted-eu", "TED — тендеры ЕС", "node24", "@pipeworx/mcp-ted-eu@0.1.3",
                 "Поиск уведомлений TED, награды, история поставщиков, профили заказчиков (официальный API v3).", "network",
                 "https://github.com/pipeworx-io/mcp-ted-eu", script="@pipeworx/mcp-ted-eu/bin/cli.js", needs=("Node 24 runtime",)),
    CatalogEntry("sam-gov", "SAM.gov + OFAC + USAspending", "node24", "@cliwant/mcp-sam-gov@1.17.0",
                 "Госзакупки США, исключения SAM, награды USAspending, проверка OFAC — без ключа (153 инструмента).", "network",
                 "https://github.com/cliwant/mcp-sam-gov", script="@cliwant/mcp-sam-gov/dist/server.js", needs=("Node 24 runtime",)),
    CatalogEntry("comtrade", "UN Comtrade — мировая торговля", "node24", "@cyanheads/un-comtrade-mcp-server@0.2.0",
                 "Торговые потоки, главные партнёры и товары по HS-кодам (публичный preview без ключа).", "network",
                 "https://github.com/cyanheads/un-comtrade-mcp-server", script="@cyanheads/un-comtrade-mcp-server/dist/index.js",
                 needs=("Node 24 runtime",)),
)
BY_ID = {e.id: e for e in CATALOG}


def _name(entry: CatalogEntry) -> str:
    return f"catalog-{entry.id}"


def validated_path(raw: object, marker: str = "") -> str:
    """Абсолютный существующий каталог; корень диска и домашняя папка — отказ."""
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(422, {"message": "Укажите папку: для этого сервера путь обязателен."})
    given = raw.strip()
    p = os.path.realpath(given)
    if not os.path.isabs(given) or not os.path.isdir(p):
        raise HTTPException(422, {"message": "Нужен абсолютный путь к существующей папке."})
    home = os.path.realpath(os.path.expanduser("~"))
    if os.path.dirname(p) == p or p == home:
        raise HTTPException(422, {"message": "Корень диска и домашняя папка слишком широки — "
                                             "выберите конкретную рабочую папку."})
    if marker and not os.path.exists(os.path.join(p, marker)):
        raise HTTPException(422, {"message": f"В папке нет {marker}: сервер не сможет стартовать. "
                                             "Выберите корень git-репозитория."})
    return p


@router.get("")
async def list_catalog(request: Request) -> dict:
    svc = request.app.state.svc
    async with svc.db.session() as s:
        have = {r[0] for r in (await s.execute(sa.select(mcp_servers_t.c.name))).fetchall()}
    return {"servers": [e.public(_name(e) in have) for e in CATALOG],
            "backlog_doc": "docs/integrations/PUBLIC_API_MCP_BACKLOG_100_RU.md"}


@router.post("/{entry_id}/install")
async def install(entry_id: str, request: Request) -> dict:
    entry = BY_ID.get(entry_id)
    if entry is None:
        raise HTTPException(404, {"message": "Такого сервера нет в каталоге."})
    svc = request.app.state.svc
    body = await request.json() if (await request.body()) else {}
    if not entry.available():
        raise HTTPException(409, {"code": "NODE24_RUNTIME_MISSING", "message": "Нужен отдельный Node 24 и предустановленные пакеты",
                                  "hint": "задайте BOSSMAN_NODE24 (node.exe) и BOSSMAN_MCP_NODE24_PREFIX (папка с node_modules)"})
    path = validated_path(body.get("path"), entry.marker) if entry.param else ""
    spec = MCPServerSpec(id=_name(entry), name=_name(entry), transport="stdio",
                         command=entry.argv(path))
    refusal = command_policy_refusal(spec.command)
    if refusal:
        await svc.bus.emit("mcp.command_refused", server=spec.name, detail=refusal[:400])
        raise HTTPException(403, {"message": f"команда запуска MCP отклонена: {refusal}"})
    try:
        async with svc.db.session() as s:
            res = await s.execute(sa.insert(mcp_servers_t).values(
                name=spec.name, transport="stdio", command=spec.command, url="",
                enabled=True, status="unknown", created_at=utcnow()))
            sid = int(res.inserted_primary_key[0])
            await s.commit()
    except sa.exc.IntegrityError:
        raise HTTPException(409, {"message": f"«{entry.title}» уже подключён.",
                                  "hint": "Удалите существующий сервер в списке MCP и поставьте заново."}) from None
    return {"id": sid, "name": spec.name, "risk": entry.risk,
            "next": f"POST /api/mcp/runtime/servers/{sid}/connect — первый запуск скачает пакет."}


FEATURE = Feature(name="mcp_catalog", router=router)
