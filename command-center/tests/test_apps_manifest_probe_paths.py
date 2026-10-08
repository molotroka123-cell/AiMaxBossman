"""Карточка приложения читает живые данные по путям из ЕГО манифеста.

Дефект (воспроизведён 2026-10-08 на 6462a434): манифест ai-3d-maker велел
лаунчеру брать метрики с `GET /api/metrics`, а приложение отдаёт их только на
`GET /metrics` (так же записано в его собственном `control_contract`). Опрос
получал 404, метрики молча пропадали, а факт «Очередь» брался из несуществующего
ключа `metrics.queue_depth` — карточка показывала «пусто» при задании в очереди.

Здесь проверяется настоящая связка: опрос центра (`apps._probe` +
`apps._resolve_facts`) против НАСТОЯЩЕГО ASGI-приложения ai-3d-maker в процессе.
Это не проверка на ПК владельца: порт, uvicorn и Windows здесь не участвуют.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from bcc.features import apps

REPO = Path(__file__).resolve().parents[2]
APPS = REPO / "apps"
MAKER = APPS / "ai-3d-maker"
if str(MAKER / "src") not in sys.path:
    sys.path.insert(0, str(MAKER / "src"))

from ai_3d_maker.api import build_app  # noqa: E402
from ai_3d_maker.config import Settings  # noqa: E402
from ai_3d_maker.control import ControlPlane  # noqa: E402

# Приложения, у которых в репозитории есть только манифест, без кода. Их карточку
# нечем проверить; список явный, чтобы новая «карточка без приложения» не прошла молча.
MANIFEST_ONLY = {"solana-volume-suite"}


def _plane(tmp_path: Path) -> ControlPlane:
    s = Settings()
    s.printer_profile = MAKER / "profiles" / "elegoo_neptune_3_plus.json"
    s.material_profile = MAKER / "profiles" / "material_defaults.json"
    s.data_dir = tmp_path / "maker-data"
    s.allow_physical_print = False
    s.printer_transport = "simulator"
    return ControlPlane(s)


async def _card(plane: ControlPlane, monkeypatch) -> tuple[dict, list[dict]]:
    monkeypatch.setattr(apps, "APPS_DIR", APPS)
    desc = apps._describe(MAKER / "app.manifest.yaml")
    assert desc is not None
    transport = httpx.ASGITransport(app=build_app(plane))
    async with httpx.AsyncClient(transport=transport) as client:
        live = await apps._probe(desc, client)
    return live, apps._resolve_facts(desc, live)


def _fact(facts: list[dict], label: str) -> dict:
    return next(f for f in facts if f["label"] == label)


@pytest.mark.asyncio
async def test_3d_maker_card_reads_live_metrics_and_queue(tmp_path, monkeypatch):
    plane = _plane(tmp_path)
    plane.store.create("job-a", "design", {})   # одно задание в статусе queued
    live, facts = await _card(plane, monkeypatch)
    assert live["status"] == "LIVE"
    assert live["metrics"].get("app") == "ai-3d-maker", "метрики не дошли до карточки"
    assert _fact(facts, "Очередь") == {"label": "Очередь", "value": "1", "live": True}


@pytest.mark.asyncio
async def test_3d_maker_empty_queue_uses_fallback_not_a_made_up_number(tmp_path, monkeypatch):
    # Негативный контроль: без заданий значения нет — показывается запасной текст,
    # а не выдуманное число и не «живой» ноль.
    live, facts = await _card(_plane(tmp_path), monkeypatch)
    assert live["metrics"].get("app") == "ai-3d-maker"
    assert _fact(facts, "Очередь") == {"label": "Очередь", "value": "пусто", "live": False}


# ------------------------------------------------------------- все манифесты

def _route_literals(app_dir: Path) -> set[str]:
    """Строковые литералы вида "/..." в исходниках приложения (без тестов)."""
    found: set[str] = set()
    for path in app_dir.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and node.value.startswith("/"):
                found.add(node.value)
    return found


def _unserved_probe_paths(ui: dict, routes: set[str]) -> list[str]:
    return [f"{key}={ui[key]}" for key in ("health_path", "metrics_path")
            if ui.get(key) and ui[key] not in routes]


def _manifests() -> list[tuple[str, Path, dict]]:
    out = []
    for manifest in sorted(APPS.glob("*/app.manifest.yaml")):
        raw = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        out.append((str(raw.get("id") or manifest.parent.name), manifest.parent, raw))
    return out


def test_every_card_probes_a_path_its_app_actually_serves():
    missing, manifest_only = [], set()
    for app_id, app_dir, raw in _manifests():
        routes = _route_literals(app_dir)
        if not routes:
            manifest_only.add(app_id)
            continue
        ui = raw.get("ui") if isinstance(raw.get("ui"), dict) else {}
        missing += [f"{app_id}: {item}" for item in _unserved_probe_paths(ui, routes)]
    assert missing == [], f"карточка опрашивает путь, которого приложение не отдаёт: {missing}"
    assert manifest_only == MANIFEST_ONLY, (
        f"приложения без кода в репозитории: {sorted(manifest_only)}; "
        "их карточка не может быть проверена — обновите MANIFEST_ONLY осознанно")


def test_route_check_still_rejects_the_original_3d_maker_defect():
    # Негативный контроль самой проверки: прежнее значение манифеста ловится.
    routes = _route_literals(MAKER)
    assert "/metrics" in routes
    assert _unserved_probe_paths({"metrics_path": "/api/metrics"}, routes) == \
        ["metrics_path=/api/metrics"]
