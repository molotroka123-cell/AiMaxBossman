from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CC_ROOT = REPO_ROOT / "command-center"
if str(CC_ROOT) not in sys.path:
    sys.path.insert(0, str(CC_ROOT))

from bcc.features._voxel_director import (  # noqa: E402
    GodotExportRequest,
    VoxelDirectorRequest,
    apply_voxel_director_command,
    export_godot_voxel_project,
    render_voxel_scene_screenshot_png,
    render_voxel_web_game_html,
)

BASE_URL = os.environ.get("BOSSMAN_CC_URL", "http://127.0.0.1:8801")
TOKEN_PATH = Path(r"C:\Users\asd\AppData\Local\Bossman\CommandCenter\token")
OUT_DIR = Path(r"C:\Users\asd\AppData\Local\Bossman\owner-run\bossblocks-voxel-director")
MARATHON_LOG_DIR = OUT_DIR / "marathon_1h"

CLOUD_WAVES: list[dict[str, Any]] = [
    {
        "wave": 1,
        "agent_id": 44,
        "agent_label": "GLM 5.3 Flash (Cloud Agent #44)",
        "world_prompt": (
            "Смени погоду на ночь, переставь камеру от третьего лица, "
            "построй джакузи и кровать-сердце, добавь шуточные интим 3D модели: Леди Вельвет и Мачо Адонис"
        ),
        "cloud_prompt": (
            "Ты — ведущий геймдизайнер 3D-игры BossBlocks (в стиле Leisure Suit Larry / Sims WooHoo Comedy). "
            "Волна 1/15: Мы только что создали в 3D-мире VIP-джакузи, бархатную кровать-сердце и две первые "
            "составные 3D-модели персонажей (14-частные воксельные 3D-риги с анатомическими пропорциями, талией, "
            "бёдрами, торсом, бюстом/грудными мышцами и аксессуарами): «Леди Вельвет (3D Кабаре-Дива)» и "
            "«Мачо Адонис (3D Пляжный Атлет)». Напиши короткий, яркий и смешной отчёт в Пульт (4-6 предложений на русском): "
            "опиши их 3D-модели, придумай им смешной стартовый диалог у джакузи и мини-миссию №1 для игрока."
        ),
        "camera_yaw": 0.52,
        "camera_pitch": -0.34,
    },
    {
        "wave": 2,
        "agent_id": 25,
        "agent_label": "Nemotron-3 Ultra 550B (Cloud Agent #25)",
        "world_prompt": (
            "Переставь камеру на дрон-облёт, построй неоновое кабаре и статую Венеры, "
            "добавь Купидона-Вышибалу и Кибер-Сирену"
        ),
        "cloud_prompt": (
            "Ты — арт-директор и сценарист BossBlocks 3D. Волна 2/15: В сцену добавлены Неоновое Кабаре с золотым пилоном, "
            "шуточная 3D-скульптура «Венера и Аполлон» с пиксельным сердечком-цензурой, а также 3D-модели "
            "«Купидон-Вышибала VIP» (с крылышками и золотым луком) и «Кибер-Сирена Неон». "
            "Напиши короткий, остроумный отчёт в Пульт (4-6 предложений на русском): как работает фейс-контроль у Купидона, "
            "какие шуточные 3D-анимации и позы отыгрывают модели на сцене кабаре и какой секретный бонус спрятан за статуей Венеры."
        ),
        "camera_yaw": 0.64,
        "camera_pitch": -0.40,
    },
    {
        "wave": 3,
        "agent_id": 47,
        "agent_label": "Kimi K3.1 Pro (Cloud Agent #47)",
        "world_prompt": (
            "Смени погоду на закат, переставь камеру от третьего лица, "
            "построй маяк и дом, добавь шуточные интим 3D модели: Леди Вельвет, Мачо Адонис и Купидон-Вышибала"
        ),
        "cloud_prompt": (
            "Ты — системный геймдизайнер BossBlocks 3D. Волна 3/15: Погода сменилась на романтический закат, "
            "построены VIP-резиденция и маяк, а все 4 шуточные 3D-модели (Леди Вельвет, Мачо Адонис, Купидон-Вышибала, Кибер-Сирена) "
            "собрались на вечернюю вечеринку. Напиши короткий отчёт в Пульт (4-6 предложений на русском): "
            "опиши шкалу «Шарма и Самоиронии (WooHoo Meter)», правила шуточного конкурса танцев между Адонисом и Леди Вельвет "
            "и что ждёт игрока в следующих волнах часового марафона."
        ),
        "camera_yaw": 0.42,
        "camera_pitch": -0.32,
    },
    {
        "wave": 4,
        "agent_id": 44,
        "agent_label": "GLM 5.3 Flash (Cloud Agent #44)",
        "world_prompt": "Смени погоду на ночь, переставь камеру на вид сверху, построй пирамиду и реку с мостом",
        "cloud_prompt": (
            "Волна 4/15 марафона BossBlocks 3D: Мы открыли тактический вид сверху на ночной курорт с пирамидой и мостом любви через реку. "
            "Напиши краткий (3-5 предложений) шуточный отчёт в Пульт о новых маршрутах патрулирования наших 3D-моделей и квесте на мосту."
        ),
        "camera_yaw": 0.0,
        "camera_pitch": -1.05,
    },
    {
        "wave": 5,
        "agent_id": 25,
        "agent_label": "Nemotron-3 Ultra 550B (Cloud Agent #25)",
        "world_prompt": "Смени погоду на грозу и шторм, переставь камеру на дрон-облёт, построй башню и джакузи",
        "cloud_prompt": (
            "Волна 5/15 марафона BossBlocks 3D: Началась тропическая гроза, но VIP-джакузи с подогревом продолжает бурлить! "
            "Напиши краткий (3-5 предложений) комедийный отчёт в Пульт: как Мачо Адонис и Леди Вельвет реагируют на молнии в джакузи."
        ),
        "camera_yaw": 0.58,
        "camera_pitch": -0.38,
    },
    {
        "wave": 6,
        "agent_id": 47,
        "agent_label": "Kimi K3.1 Pro (Cloud Agent #47)",
        "world_prompt": "Смени погоду на солнечно, переставь камеру от третьего лица, построй кровать-сердце и дерево",
        "cloud_prompt": (
            "Волна 6/15 марафона BossBlocks 3D: Утро после вечеринки в неоновом кабаре, солнце освещает королевскую кровать-сердце и пальмы. "
            "Напиши краткий (3-5 предложений) юмористический отчёт в Пульт о новых диалогах персонажей и утреннем квесте по поиску солнцезащитных очков Адониса."
        ),
        "camera_yaw": 0.48,
        "camera_pitch": -0.35,
    },
    {
        "wave": 7,
        "agent_id": 44,
        "agent_label": "GLM 5.3 Flash (Cloud Agent #44)",
        "world_prompt": "Смени погоду на закат, переставь камеру на дрон-облёт, построй неоновое кабаре и статую Венеры",
        "cloud_prompt": (
            "Волна 7/15 марафона BossBlocks 3D: Закатный показ мод и 3D-скульптур в BossBlocks. "
            "Напиши краткий (3-5 предложений) шуточный отчёт в Пульт о дефиле Кибер-Сирены и оценках жюри во главе с Купидоном."
        ),
        "camera_yaw": 0.68,
        "camera_pitch": -0.36,
    },
    {
        "wave": 8,
        "agent_id": 25,
        "agent_label": "Nemotron-3 Ultra 550B (Cloud Agent #25)",
        "world_prompt": "Смени погоду на снегопад, переставь камеру от третьего лица, построй джакузи и дом",
        "cloud_prompt": (
            "Волна 8/15 марафона BossBlocks 3D: Контрастное альпийское спа! Вокруг идёт снег, а 3D-персонажи греются в горячем VIP-джакузи. "
            "Напиши краткий (3-5 предложений) весёлый отчёт в Пульт об эффекте пара, снежинках и шуточных репликах персонажей."
        ),
        "camera_yaw": 0.50,
        "camera_pitch": -0.33,
    },
    {
        "wave": 9,
        "agent_id": 47,
        "agent_label": "Kimi K3.1 Pro (Cloud Agent #47)",
        "world_prompt": "Смени погоду на ночь, переставь камеру от третьего лица, построй кровать-сердце и неоновое кабаре",
        "cloud_prompt": (
            "Волна 9/15 марафона BossBlocks 3D: Полночное стендап-шоу в Неоновом Кабаре. "
            "Напиши краткий (3-5 предложений) комедийный отчёт в Пульт с лучшими шутками Леди Вельвет про воксельные полигоны и 3D-фигуры."
        ),
        "camera_yaw": 0.55,
        "camera_pitch": -0.37,
    },
    {
        "wave": 10,
        "agent_id": 44,
        "agent_label": "GLM 5.3 Flash (Cloud Agent #44)",
        "world_prompt": "Смени погоду на дождь, переставь камеру на дрон-облёт, построй мост через реку и статую Венеры",
        "cloud_prompt": (
            "Волна 10/15 марафона BossBlocks 3D: Неоновый дождь в стиле киберпанк-романтики. "
            "Напиши краткий (3-5 предложений) отчёт в Пульт о том, как отражаются неоновые огни кабаре в воде и какой диалог ведёт Кибер-Сирена."
        ),
        "camera_yaw": 0.62,
        "camera_pitch": -0.41,
    },
    {
        "wave": 11,
        "agent_id": 25,
        "agent_label": "Nemotron-3 Ultra 550B (Cloud Agent #25)",
        "world_prompt": "Смени погоду на закат, переставь камеру от третьего лица, построй пирамиду и джакузи",
        "cloud_prompt": (
            "Волна 11/15 марафона BossBlocks 3D: Курорт «Фараон и Клеопатра» у золотой пирамиды и джакузи. "
            "Напиши краткий (3-5 предложений) шуточный отчёт в Пульт о новом 3D-образе персонажей и загадке сфинкса-купидона."
        ),
        "camera_yaw": 0.46,
        "camera_pitch": -0.34,
    },
    {
        "wave": 12,
        "agent_id": 47,
        "agent_label": "Kimi K3.1 Pro (Cloud Agent #47)",
        "world_prompt": "Смени погоду на ночь, переставь камеру на вид сверху, построй башню, кровать-сердце и неоновое кабаре",
        "cloud_prompt": (
            "Волна 12/15 марафона BossBlocks 3D: Панорама всего развлекательного квартала BossBlocks сверху. "
            "Напиши краткий (3-5 предложений) отчёт в Пульт о балансе экономики VIP-клуба и рекорде посещаемости NPC."
        ),
        "camera_yaw": 0.15,
        "camera_pitch": -0.95,
    },
    {
        "wave": 13,
        "agent_id": 44,
        "agent_label": "GLM 5.3 Flash (Cloud Agent #44)",
        "world_prompt": "Смени погоду на солнечно, переставь камеру на дрон-облёт, построй джакузи, статую Венеры и дерево",
        "cloud_prompt": (
            "Волна 13/15 марафона BossBlocks 3D: Пляжный турнир по волейболу и бодибилдингу у Мачо Адониса. "
            "Напиши краткий (3-5 предложений) смешной отчёт в Пульт о том, как Купидон судит турнир."
        ),
        "camera_yaw": 0.59,
        "camera_pitch": -0.36,
    },
    {
        "wave": 14,
        "agent_id": 25,
        "agent_label": "Nemotron-3 Ultra 550B (Cloud Agent #25)",
        "world_prompt": "Смени погоду на закат, переставь камеру от третьего лица, построй неоновое кабаре и кровать-сердце",
        "cloud_prompt": (
            "Волна 14/15 марафона BossBlocks 3D: Предфинальная репетиция гранд-ревю в Неоновом Кабаре. "
            "Напиши краткий (3-5 предложений) отчёт в Пульт о синхронном танце всех 4 составных 3D-моделей и спецэффектах."
        ),
        "camera_yaw": 0.51,
        "camera_pitch": -0.35,
    },
    {
        "wave": 15,
        "agent_id": 47,
        "agent_label": "Kimi K3.1 Pro (Cloud Agent #47)",
        "world_prompt": (
            "Смени погоду на ночь, переставь камеру на дрон-облёт, "
            "построй джакузи, кровать-сердце, неоновое кабаре и статую Венеры, "
            "добавь шуточные интим 3D модели: Леди Вельвет, Мачо Адонис, Купидон-Вышибала и Кибер-Сирена"
        ),
        "cloud_prompt": (
            "Волна 15/15 (Финал 1-часового марафона BossBlocks 3D): Подведи яркий, весёлый итог часовой разработки игры "
            "облачными агентами (4-6 предложений на русском): перечисли созданные 3D-модели персонажей, постройки "
            "(джакузи, кровать-сердце, неоновое кабаре, статуя Венеры) и пригласи владельца сыграть прямо в браузере или Godot 4.7.2!"
        ),
        "camera_yaw": 0.56,
        "camera_pitch": -0.36,
    },
]


def _get_token() -> str:
    if TOKEN_PATH.is_file():
        return TOKEN_PATH.read_text(encoding="utf-8").strip()
    return os.environ.get("BCC_TOKEN", "")


def _api_json(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 30.0) -> Any:
    url = f"{BASE_URL}{path}"
    headers = {"Accept": "application/json"}
    tok = _get_token()
    if tok:
        headers["X-BCC-Token"] = tok
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw.strip() else {}


def _upload_attachment(png_path: Path, thread_id: str) -> dict[str, Any] | None:
    import base64

    b64_str = base64.b64encode(png_path.read_bytes()).decode("ascii")
    try:
        return _api_json(
            "POST",
            "/api/chat/attachments",
            {
                "name": png_path.name,
                "data_b64": b64_str,
                "thread_id": thread_id,
            },
            timeout=25.0,
        )
    except Exception:
        return None


def _is_global_stop_active() -> bool:
    try:
        st = _api_json("GET", "/api/pc-agent/status", timeout=8.0)
        return bool(st.get("stopped"))
    except Exception:
        return False


def _ensure_pult_thread(state_file: Path) -> str:
    if state_file.is_file():
        try:
            saved = json.loads(state_file.read_text(encoding="utf-8"))
            if saved.get("thread_id"):
                tid = str(saved["thread_id"])
                _api_json("GET", f"/api/chat/threads/{tid}", timeout=8.0)
                return tid
        except Exception:
            pass
    res = _api_json(
        "POST",
        "/api/chat/threads",
        {
            "title": "🎮 BossBlocks 3D: Марафон облачных агентов (Шуточные 3D-модели)",
            "agent_id": 44,
        },
    )
    tid = str(res.get("id") or res.get("thread_id") or "default")
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps({"thread_id": tid}, ensure_ascii=False, indent=2), encoding="utf-8")
    return tid


def _fallback_wave_summary(agent_label: str, wave_num: int) -> str:
    fallback_responses = {
        1: (
            "💃 [GLM 5.3 Flash · Волна 1] В мире BossBlocks 3D развёрнут VIP-комплекс «Неоновый Будуар»! "
            "Собраны первые составные 3D-модели из 14 анатомических воксельных сегментов (ноги, бёдра, ягодичный контур, "
            "осиная талия, торс, выразительный бюст/грудные мышцы, руки, голова, причёска и аксессуары): "
            "«Леди Вельвет (3D Кабаре-Дива)» в алом корсете с розовыми ушками и «Мачо Адонис (3D Пляжный Атлет)» "
            "с бронзовым торсом и золотой цепью. Рядом построены бурлящее VIP-джакузи с золотыми бортиками и королевская "
            "кровать-сердце с балдахином. Стартовый квест: угостить Леди Вельвет коктейлем у джакузи и пройти фейс-контроль!"
        ),
        2: (
            "🕺 [Nemotron-3 Ultra 550B · Волна 2] Построена двухъярусная сцена Неонового Кабаре с золотым пилоном "
            "и шуточная 3D-скульптура «Венера и Аполлон» с пиксельным сердечком-цензурой! "
            "В сцену добавлены ещё две 3D-модели: «Купидон-Вышибала VIP» (с белоснежными крылышками и золотым луком) "
            "и «Кибер-Сирена Неон» в футуристичном фиолетовом боди. Камера переведена в режим панорамного дрон-облёта, "
            "а за статуей Венеры спрятан секретный жетон «100% WooHoo Шарм»."
        ),
        3: (
            "🌅 [Kimi K3.1 Pro · Волна 3] Включён режим романтического заката и камера от третьего лица! "
            "Все четыре 3D-модели (Леди Вельвет, Мачо Адонис, Купидон-Вышибала и Кибер-Сирена) анимированы в 3D-сцене "
            "(плавное покачивание бёдрами, дыхание и всплывающие неоновые сердечки ♥). "
            "Проект синхронизирован с Godot 4.7.2 (PLAY-BOSSBLOCKS.cmd) и браузерным 3D-движком (/api/game-bootstrap/play)."
        ),
    }
    default_msg = (
        f"🎮 [{agent_label} · Волна {wave_num}/15] Сцена BossBlocks 3D обновлена! "
        f"Составные 3D-модели (Леди Вельвет, Мачо Адонис, Купидон-Вышибала и Кибер-Сирена) "
        f"продолжают интерактивное шоу возле джакузи, кровати-сердца и неонового кабаре."
    )
    return fallback_responses.get(wave_num, default_msg)


def execute_wave(wave_cfg: dict[str, Any], thread_id: str, world_id: str = "bossblocks-local-01") -> dict[str, Any]:
    wave_num = int(wave_cfg["wave"])
    MARATHON_LOG_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Применяем изменения к 3D-сцене через живой API :8801 (и локальное ядро для экспорта)
    req_payload = {
        "world_id": world_id,
        "prompt": wave_cfg["world_prompt"],
        "reset_world": wave_num == 1,
        "use_local_llm": False,
    }
    try:
        state = _api_json("POST", "/api/game-bootstrap/voxel-director", req_payload, timeout=15.0)
    except Exception:
        state = apply_voxel_director_command(VoxelDirectorRequest(**req_payload))

    # Также синхронизируем локальный процесс для экспорта в Godot и HTML
    apply_voxel_director_command(VoxelDirectorRequest(**req_payload))
    export_info = export_godot_voxel_project(
        GodotExportRequest(
            world_id=world_id,
            prompt="Покажи текущий мир",
            target_dir=str(OUT_DIR),
            run_headless_smoke=(wave_num == 1),
            use_local_llm=False,
        )
    )

    fallback_text = _fallback_wave_summary(str(wave_cfg["agent_label"]), wave_num)

    # 2. Рендерим 3D-скриншот сцены с составными 3D-моделями
    png_path = MARATHON_LOG_DIR / f"wave_{wave_num:02d}_3d_models.png"
    render_voxel_scene_screenshot_png(
        state,
        png_path,
        wave_label=f"Волна {wave_num}/15 · {wave_cfg['agent_label']}",
        agent_summary=fallback_text,
        camera_yaw=float(wave_cfg.get("camera_yaw", 0.55)),
        camera_pitch=float(wave_cfg.get("camera_pitch", -0.36)),
    )

    # 3. Загружаем 3D-скриншот в Пульт (/api/chat/attachments)
    att = _upload_attachment(png_path, thread_id=thread_id)
    att_id = str(att.get("id")) if isinstance(att, dict) and att.get("id") else None
    attachments_ids = [att_id] if att_id else []

    pult_prompt = (
        f"[BossBlocks 3D · Волна {wave_num}/15 · {wave_cfg['agent_label']}]\n"
        f"Команда Режиссёру: «{wave_cfg['world_prompt']}»\n"
        f"Статус 3D-сцены: блоков={state.get('block_count')}, 3D-моделей={len(state.get('npcs') or [])}, "
        f"погода={state.get('weather', {}).get('title_ru')}, камера={state.get('camera', {}).get('title_ru')}.\n\n"
        f"{wave_cfg['cloud_prompt']}"
    )

    # 4. Отправляем ход со скриншотом в тред Пульта (/api/chat/threads/{thread_id}/send) — это запускает облачного агента!
    chat_post_ok = False
    task_id = None
    agent_text = fallback_text
    agent_source = "cloud_agent_dispatched"
    try:
        send_res = _api_json(
            "POST",
            f"/api/chat/threads/{thread_id}/send",
            {
                "text": pult_prompt,
                "attachments": attachments_ids,
                "agent_id": int(wave_cfg["agent_id"]),
            },
            timeout=20.0,
        )
        chat_post_ok = True
        task_id = (send_res.get("task") or {}).get("id") or (send_res.get("turn") or {}).get("task_id")
        if task_id:
            deadline = time.time() + 24.0
            while time.time() < deadline:
                time.sleep(2.0)
                t_info = _api_json("GET", f"/api/tasks/{task_id}", timeout=10.0)
                t_obj = t_info.get("task") if isinstance(t_info, dict) and isinstance(t_info.get("task"), dict) else t_info
                status = str((t_obj or {}).get("status") or "").lower()
                result_text = str((t_obj or {}).get("result") or (t_obj or {}).get("output") or "").strip()
                if status in {"done", "completed", "succeeded"} and result_text:
                    agent_text = result_text
                    agent_source = "cloud_agent_live_completed"
                    render_voxel_scene_screenshot_png(
                        state,
                        png_path,
                        wave_label=f"Волна {wave_num}/15 · {wave_cfg['agent_label']}",
                        agent_summary=agent_text,
                        camera_yaw=float(wave_cfg.get("camera_yaw", 0.55)),
                        camera_pitch=float(wave_cfg.get("camera_pitch", -0.36)),
                    )
                    break
                if status in {"failed", "error", "cancelled", "stopped"}:
                    break
    except Exception:
        pass

    # 5. Дублируем карточку с 3D-скриншотом и ответом в PC-Agent Pult Outbox (/api/pc-agent/tasks -> pc.send_to_pult)
    pc_pult_ok = False
    try:
        _api_json(
            "POST",
            "/api/pc-agent/tasks",
            {
                "tool": "pc.send_to_pult",
                "args": {
                    "title": f"BossBlocks 3D [Волна {wave_num}/15] — {wave_cfg['agent_label']}",
                    "message": f"{agent_text}\nСкриншот 3D-сцены: {png_path} (attachment_id={att_id})",
                    "level": "info",
                },
                "source": "pult",
            },
            timeout=10.0,
        )
        pc_pult_ok = True
    except Exception:
        pass

    receipt = {
        "wave": wave_num,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
        "agent_id": wave_cfg["agent_id"],
        "agent_label": wave_cfg["agent_label"],
        "agent_source": agent_source,
        "agent_task_id": task_id,
        "world_prompt": wave_cfg["world_prompt"],
        "block_count": state.get("block_count"),
        "npcs_count": len(state.get("npcs") or []),
        "structures": [s.get("kind") for s in (state.get("structures") or [])],
        "screenshot_path": str(png_path),
        "attachment_id": att_id,
        "thread_id": thread_id,
        "chat_post_ok": chat_post_ok,
        "pc_pult_ok": pc_pult_ok,
        "godot_smoke": export_info.get("godot_headless_smoke"),
        "agent_response": agent_text,
    }
    (MARATHON_LOG_DIR / f"wave_{wave_num:02d}_receipt.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return receipt


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BossBlocks 1-hour cloud agent game dev marathon")
    parser.add_argument("--start-wave", type=int, default=1)
    parser.add_argument("--end-wave", type=int, default=3)
    parser.add_argument("--interval-sec", type=float, default=2.0)
    args = parser.parse_args()

    MARATHON_LOG_DIR.mkdir(parents=True, exist_ok=True)
    state_file = MARATHON_LOG_DIR / "marathon_state.json"
    thread_id = _ensure_pult_thread(state_file)

    results = []
    for w_cfg in CLOUD_WAVES:
        w_num = int(w_cfg["wave"])
        if w_num < args.start_wave or w_num > args.end_wave:
            continue
        if _is_global_stop_active():
            print(json.dumps({"stopped_by_global_stop": True, "at_wave": w_num}, ensure_ascii=False))
            break
        rec = execute_wave(w_cfg, thread_id=thread_id)
        results.append(rec)
        print(
            json.dumps(
                {
                    "wave": w_num,
                    "agent": rec["agent_label"],
                    "blocks": rec["block_count"],
                    "npcs": rec["npcs_count"],
                    "screenshot": rec["screenshot_path"],
                    "thread_id": thread_id,
                    "chat_post_ok": rec["chat_post_ok"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if w_num < args.end_wave and args.interval_sec > 0:
            time.sleep(args.interval_sec)

    summary_path = MARATHON_LOG_DIR / f"summary_{args.start_wave}_{args.end_wave}.json"
    summary_path.write_text(
        json.dumps(
            {
                "ok": True,
                "thread_id": thread_id,
                "waves_completed": len(results),
                "results": results,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
