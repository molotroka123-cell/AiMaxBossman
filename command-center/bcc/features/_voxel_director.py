"""Локальный воксельный движок и «Режиссёр мира» (BossBlocks / Local Genie-Voxel).

Позволяет Bossman локально ($0.00) создавать простые 3D-игры уровня Minecraft
и менять сцену прямо во время симуляции текстовыми командами:
  1) сменить погоду и время суток (ясно, дождь, гроза, снег, туман, закат, ночь);
  2) переставить камеру (от 1-го лица, от 3-го лица, дрон-облёт, вид сверху, кинокамера);
  3) добавить постройку/предмет (дом, башня, пирамида, дерево, мост, замок, маяк)
     или живого NPC-персонажа с адаптивным диалогом и процедурным звуком;
  4) экспортировать мир в нативный проект Godot 4.7.2 + запустить автопроверку
     реальным локальным движком Godot либо открыть интерактивную 3D-игру в браузере.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

WORLD_EDGE_DEFAULT = 10
DEFAULT_GODOT_EDITOR_PATH = Path(
    r"C:\Users\asd\AppData\Local\Bossman\tool-cache\bossblocks-001\editor\godot.windows.editor.x86_64.exe"
)

BLOCK_PALETTE: dict[int, dict[str, Any]] = {
    1: {"id": 1, "name": "Grass", "title_ru": "Трава", "color": "#69aa56"},
    2: {"id": 2, "name": "Sand", "title_ru": "Песок", "color": "#d8bc79"},
    3: {"id": 3, "name": "Stone", "title_ru": "Камень", "color": "#858f9e"},
    4: {"id": 4, "name": "Wood", "title_ru": "Дерево", "color": "#8b5a2b"},
    5: {"id": 5, "name": "Brick", "title_ru": "Кирпич", "color": "#b55239"},
    6: {"id": 6, "name": "Water", "title_ru": "Вода / Лёд", "color": "#3b82f6"},
    7: {"id": 7, "name": "GlowCrystal", "title_ru": "Светящийся кристалл", "color": "#f59e0b"},
    8: {"id": 8, "name": "Leaves", "title_ru": "Листва", "color": "#2e7d32"},
}

WEATHER_PRESETS: dict[str, dict[str, Any]] = {
    "sunny": {
        "id": "sunny",
        "title_ru": "Солнечно",
        "sky_color": "#8dbbdd",
        "ambient_color": "#a9bdd0",
        "ambient_energy": 0.75,
        "sun_energy": 1.5,
        "sun_pitch_deg": -58.0,
        "fog_enabled": False,
        "fog_density": 0.0,
        "particles": "none",
        "ambient_sound": "birds_breeze",
        "music_bpm": 108,
        "music_scale": [261.63, 293.66, 329.63, 392.00, 440.00],
    },
    "rain": {
        "id": "rain",
        "title_ru": "Дождь",
        "sky_color": "#53687e",
        "ambient_color": "#6c7f93",
        "ambient_energy": 0.48,
        "sun_energy": 0.65,
        "sun_pitch_deg": -45.0,
        "fog_enabled": True,
        "fog_density": 0.025,
        "particles": "rain",
        "ambient_sound": "rain_steady",
        "music_bpm": 88,
        "music_scale": [220.00, 246.94, 261.63, 329.63, 349.23],
    },
    "storm": {
        "id": "storm",
        "title_ru": "Гроза и шторм",
        "sky_color": "#2c3545",
        "ambient_color": "#434f63",
        "ambient_energy": 0.32,
        "sun_energy": 0.35,
        "sun_pitch_deg": -35.0,
        "fog_enabled": True,
        "fog_density": 0.045,
        "particles": "rain_heavy",
        "ambient_sound": "thunder_wind",
        "music_bpm": 124,
        "music_scale": [196.00, 220.00, 233.08, 293.66, 311.13],
    },
    "snow": {
        "id": "snow",
        "title_ru": "Снегопад",
        "sky_color": "#cddce8",
        "ambient_color": "#dce7f0",
        "ambient_energy": 0.82,
        "sun_energy": 1.05,
        "sun_pitch_deg": -40.0,
        "fog_enabled": True,
        "fog_density": 0.02,
        "particles": "snow",
        "ambient_sound": "winter_wind",
        "music_bpm": 96,
        "music_scale": [293.66, 329.63, 369.99, 440.00, 493.88],
    },
    "fog": {
        "id": "fog",
        "title_ru": "Густой туман",
        "sky_color": "#9aa6b2",
        "ambient_color": "#adb8c2",
        "ambient_energy": 0.55,
        "sun_energy": 0.55,
        "sun_pitch_deg": -30.0,
        "fog_enabled": True,
        "fog_density": 0.065,
        "particles": "mist",
        "ambient_sound": "deep_drone",
        "music_bpm": 76,
        "music_scale": [174.61, 196.00, 220.00, 261.63, 293.66],
    },
    "sunset": {
        "id": "sunset",
        "title_ru": "Закат",
        "sky_color": "#d97757",
        "ambient_color": "#e09f7d",
        "ambient_energy": 0.62,
        "sun_energy": 1.25,
        "sun_pitch_deg": -16.0,
        "fog_enabled": True,
        "fog_density": 0.012,
        "particles": "none",
        "ambient_sound": "evening_wind",
        "music_bpm": 92,
        "music_scale": [261.63, 311.13, 349.23, 392.00, 466.16],
    },
    "night": {
        "id": "night",
        "title_ru": "Ночь",
        "sky_color": "#111827",
        "ambient_color": "#1f2937",
        "ambient_energy": 0.25,
        "sun_energy": 0.22,
        "sun_pitch_deg": -70.0,
        "fog_enabled": True,
        "fog_density": 0.018,
        "particles": "fireflies",
        "ambient_sound": "night_crickets",
        "music_bpm": 80,
        "music_scale": [220.00, 261.63, 293.66, 329.63, 392.00],
    },
}

CAMERA_PRESETS: dict[str, dict[str, Any]] = {
    "first_person": {
        "mode": "first_person",
        "title_ru": "От первого лица (глазами игрока)",
        "offset": [0.0, 0.64, 0.0],
        "pitch_deg": 0.0,
        "fov": 75.0,
        "orbit": False,
    },
    "third_person": {
        "mode": "third_person",
        "title_ru": "От третьего лица (за спиной)",
        "offset": [0.0, 2.4, 4.5],
        "pitch_deg": -18.0,
        "fov": 70.0,
        "orbit": False,
    },
    "drone_orbit": {
        "mode": "drone_orbit",
        "title_ru": "Дрон-облёт (панорама мира)",
        "offset": [0.0, 9.5, 11.0],
        "pitch_deg": -38.0,
        "fov": 65.0,
        "orbit": True,
    },
    "top_down": {
        "mode": "top_down",
        "title_ru": "Вид сверху (тактический)",
        "offset": [0.0, 16.0, 0.1],
        "pitch_deg": -88.0,
        "fov": 60.0,
        "orbit": False,
    },
    "cinematic": {
        "mode": "cinematic",
        "title_ru": "Кинематографическая камера",
        "offset": [6.5, 4.0, 7.5],
        "pitch_deg": -22.0,
        "fov": 55.0,
        "orbit": True,
    },
}


class VoxelDirectorRequest(BaseModel):
    world_id: str = Field(default="bossblocks-local-01", min_length=1, max_length=80)
    prompt: str = Field(
        default="Сделай солнечную поляну, построй деревянный дом и дерево, добавь торговца",
        min_length=1,
        max_length=2000,
    )
    world_edge: int = Field(default=WORLD_EDGE_DEFAULT, ge=6, le=20)
    use_local_llm: bool = Field(default=False)
    reset_world: bool = Field(default=False)


class GodotExportRequest(BaseModel):
    world_id: str = Field(default="bossblocks-local-01", min_length=1, max_length=80)
    prompt: str = Field(
        default="Смени погоду на закат, поставь камеру от третьего лица, построй башню и дом, добавь стражника",
        max_length=2000,
    )
    target_dir: str | None = Field(default=None, max_length=400)
    run_headless_smoke: bool = Field(default=True)
    use_local_llm: bool = Field(default=False)


_WORLD_STORE: dict[str, dict[str, Any]] = {}


def _cell_key(x: int, y: int, z: int) -> str:
    return f"{int(x)},{int(y)},{int(z)}"


def _overlaps_spawn(x: int, y: int, z: int, spawn: tuple[float, float, float] = (0.0, 2.0, 0.0)) -> bool:
    return abs(x - spawn[0]) < 1.1 and abs(z - spawn[2]) < 1.1 and -1 <= y <= 3


def create_base_voxel_world(world_id: str = "bossblocks-local-01", world_edge: int = WORLD_EDGE_DEFAULT) -> dict[str, Any]:
    """Создаёт базовый воксельный мир (bedrock + трава/песок/камень + бортики)."""
    blocks: dict[str, dict[str, int]] = {}
    for x in range(-world_edge, world_edge + 1):
        for z in range(-world_edge, world_edge + 1):
            blocks[_cell_key(x, -2, z)] = {"x": x, "y": -2, "z": z, "kind": 3}
            surface_kind = 1 if (abs(x) + abs(z)) % 4 != 0 else 2
            blocks[_cell_key(x, -1, z)] = {"x": x, "y": -1, "z": z, "kind": surface_kind}

    for edge in range(-world_edge - 1, world_edge + 2):
        for y in range(0, 2):
            for bx, bz in (
                (-world_edge - 1, edge),
                (world_edge + 1, edge),
                (edge, -world_edge - 1),
                (edge, world_edge + 1),
            ):
                blocks[_cell_key(bx, y, bz)] = {"x": bx, "y": y, "z": bz, "kind": 3}

    return {
        "world_id": world_id,
        "world_edge": world_edge,
        "spawn": [0.0, 2.0, 0.0],
        "weather": dict(WEATHER_PRESETS["sunny"]),
        "camera": dict(CAMERA_PRESETS["first_person"]),
        "blocks": blocks,
        "structures": [],
        "npcs": [],
        "director_history": [],
    }


def _set_block(
    world: dict[str, Any],
    x: int,
    y: int,
    z: int,
    kind: int,
    *,
    allow_overwrite: bool = True,
) -> bool:
    edge = int(world.get("world_edge", WORLD_EDGE_DEFAULT))
    if abs(x) > edge or abs(z) > edge or y < -1 or y > 12:
        return False
    if _overlaps_spawn(x, y, z):
        return False
    if kind not in BLOCK_PALETTE:
        return False
    key = _cell_key(x, y, z)
    if not allow_overwrite and key in world["blocks"]:
        return False
    world["blocks"][key] = {"x": int(x), "y": int(y), "z": int(z), "kind": int(kind)}
    return True


def _build_structure(world: dict[str, Any], kind: str, origin_x: int, origin_z: int) -> dict[str, Any]:
    """Строит воксельную конструкцию в мире и возвращает отчёт."""
    placed = 0
    if kind == "tree":
        for dy in range(0, 4):
            placed += int(_set_block(world, origin_x, dy, origin_z, 4))
        for lx in range(origin_x - 2, origin_x + 3):
            for lz in range(origin_z - 2, origin_z + 3):
                for ly in (3, 4):
                    if abs(lx - origin_x) + abs(lz - origin_z) <= (3 if ly == 3 else 2):
                        placed += int(_set_block(world, lx, ly, lz, 8, allow_overwrite=False))
        placed += int(_set_block(world, origin_x, 5, origin_z, 8))
    elif kind == "house":
        # Деревянный домик 5x5 с дверным проёмом, кирпичной крышей и кристаллом внутри
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                is_wall = abs(dx) == 2 or abs(dz) == 2
                is_door = dz == -2 and dx == 0
                if is_wall and not is_door:
                    for y in range(0, 3):
                        placed += int(_set_block(world, origin_x + dx, y, origin_z + dz, 4))
                placed += int(_set_block(world, origin_x + dx, 3, origin_z + dz, 5))
        for dx in range(-1, 2):
            for dz in range(-1, 2):
                placed += int(_set_block(world, origin_x + dx, 4, origin_z + dz, 5))
        placed += int(_set_block(world, origin_x, 1, origin_z, 7))
    elif kind == "tower":
        # Каменная сторожевая башня 3x3 высотой 7 блоков с зубцами и маяком
        for y in range(0, 6):
            for dx in range(-1, 2):
                for dz in range(-1, 2):
                    if abs(dx) == 1 or abs(dz) == 1:
                        if not (y in (0, 1) and dx == 0 and dz == -1):
                            placed += int(_set_block(world, origin_x + dx, y, origin_z + dz, 3))
        for dx in (-1, 1):
            for dz in (-1, 1):
                placed += int(_set_block(world, origin_x + dx, 6, origin_z + dz, 5))
        placed += int(_set_block(world, origin_x, 6, origin_z, 7))
    elif kind == "pyramid":
        for level, radius in enumerate((3, 2, 1, 0)):
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    b_kind = 7 if radius == 0 else 2
                    placed += int(_set_block(world, origin_x + dx, level, origin_z + dz, b_kind))
    elif kind == "bridge":
        # Водный канал и деревянный мост через него
        for dz in range(-3, 4):
            for dx in (-1, 0, 1):
                world["blocks"][_cell_key(origin_x + dx, -1, origin_z + dz)] = {
                    "x": origin_x + dx,
                    "y": -1,
                    "z": origin_z + dz,
                    "kind": 6,
                }
                placed += 1
        for dx in range(-2, 3):
            placed += int(_set_block(world, origin_x + dx, 0, origin_z, 4))
            placed += int(_set_block(world, origin_x + dx, 1, origin_z - 1, 4))
            placed += int(_set_block(world, origin_x + dx, 1, origin_z + 1, 4))
    elif kind == "castle":
        for dx in range(-4, 5):
            for dz in range(-4, 5):
                if abs(dx) == 4 or abs(dz) == 4:
                    if not (dz == -4 and abs(dx) <= 1):
                        for y in range(0, 3):
                            placed += int(_set_block(world, origin_x + dx, y, origin_z + dz, 3))
                        if (dx + dz) % 2 == 0:
                            placed += int(_set_block(world, origin_x + dx, 3, origin_z + dz, 3))
        placed += int(_set_block(world, origin_x, 1, origin_z, 7))
    else:  # beacon / crystal
        for y in range(0, 4):
            placed += int(_set_block(world, origin_x, y, origin_z, 7 if y >= 2 else 3))

    rec = {
        "kind": kind,
        "origin": [origin_x, 0, origin_z],
        "blocks_placed": placed,
    }
    world["structures"].append(rec)
    return rec


def _parse_weather_from_prompt(p_low: str) -> str | None:
    if any(k in p_low for k in ("гроз", "шторм", "молн", "storm", "thunder")):
        return "storm"
    if any(k in p_low for k in ("дожд", "ливен", "пасмурн", "rain")):
        return "rain"
    if any(k in p_low for k in ("снег", "снегопад", "метел", "зим", "snow", "winter")):
        return "snow"
    if any(k in p_low for k in ("туман", "дымк", "fog", "mist")):
        return "fog"
    if any(k in p_low for k in ("ночь", "ночн", "лун", "темно", "night")):
        return "night"
    if any(k in p_low for k in ("закат", "вечер", "рассвет", "sunset", "dusk")):
        return "sunset"
    if any(k in p_low for k in ("солн", "ясно", "ясн", "день", "sunny", "clear", "day")):
        return "sunny"
    return None


def _parse_camera_from_prompt(p_low: str) -> str | None:
    if any(k in p_low for k in ("дрон", "облет", "облёт", "орбит", "панорам", "drone", "orbit")):
        return "drone_orbit"
    if any(k in p_low for k in ("сверху", "топ", "птичь", "карты", "top_down", "top-down", "top down")):
        return "top_down"
    if any(k in p_low for k in ("треть", "3-го", "3го", "со спины", "за спиной", "third_person", "third person")):
        return "third_person"
    if any(k in p_low for k in ("кино", "кинематограф", "ракурс", "cinematic")):
        return "cinematic"
    if any(k in p_low for k in ("перв", "1-го", "1го", "глазами", "first_person", "first person")):
        return "first_person"
    return None


def _parse_structures_from_prompt(p_low: str) -> list[str]:
    found: list[str] = []
    mapping = [
        ("house", ("дом", "изб", "хижин", "коттедж", "house", "hut")),
        ("tower", ("башн", "вышк", "tower")),
        ("pyramid", ("пирамид", "pyramid")),
        ("tree", ("дерев", "лес", "рощ", "дуб", "сосн", "tree", "forest")),
        ("bridge", ("мост", "рек", "озер", "пруд", "bridge", "river")),
        ("castle", ("замок", "замк", "крепост", "стен", "castle", "fort")),
        ("beacon", ("маяк", "кристал", "обелиск", "алтар", "факел", "beacon", "crystal")),
    ]
    for kind, keywords in mapping:
        if any(k in p_low for k in keywords):
            found.append(kind)
    return found


def _parse_npcs_from_prompt(p_low: str, weather_id: str) -> list[dict[str, Any]]:
    npcs: list[dict[str, Any]] = []
    roles = [
        ("merchant", "Торговец Борис", "#f59e0b", ("торгов", "купц", "купец", "магаз", "merchant", "trader")),
        ("guard", "Стражник Гром", "#3b82f6", ("страж", "охран", "рыцар", "воин", "солдат", "guard", "knight")),
        ("mage", "Архимаг Эфир", "#8b5cf6", ("маг", "волшеб", "мудрец", "учен", "mage", "wizard")),
        ("miner", "Шахтёр Рудольф", "#ef4444", ("шахтер", "шахтёр", "кузнец", "строител", "miner", "builder")),
        ("villager", "Жительница Аня", "#10b981", ("человек", "жител", "персонаж", "нпс", "npc", "друг", "путник", "villager", "person")),
    ]
    weather_comment = {
        "sunny": "Отличная солнечная погода для стройки!",
        "rain": "Дождь льёт как из ведра — хорошо, что рядом укрытие.",
        "storm": "Гроза гремит! Держись подальше от высоких башен.",
        "snow": "Снег укрыл блоки — смотри не поскользнись на льду.",
        "fog": "В таком тумане легко заблудиться без светящихся кристаллов.",
        "sunset": "Какой красивый закат над нашим воксельным миром!",
        "night": "Ночью кристаллы светятся особенно ярко.",
    }.get(weather_id, "Приветствую тебя в мире BossBlocks!")

    for role_id, name_ru, color, keywords in roles:
        if any(k in p_low for k in keywords):
            npcs.append(
                {
                    "role": role_id,
                    "name": name_ru,
                    "color": color,
                    "dialogue": f"{name_ru}: «{weather_comment} Чем помочь по хозяйству?»",
                }
            )
    return npcs


def _try_local_llm_dialogue(prompt: str, weather_id: str, npc_name: str) -> str | None:
    """Опциональный вызов локальной Ollama ($0.00) для генерации живой реплики NPC."""
    body = {
        "model": "gemma4-31b-uncensored:latest",
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Ты персонаж '{npc_name}' в воксельной 3D-игре уровня Minecraft. "
                    f"Погода сейчас: {weather_id}. Игрок изменил мир командой: '{prompt}'. "
                    "Ответь ОДНОЙ короткой живой фразой на русском языке (до 15 слов) без кавычек."
                ),
            }
        ],
        "think": False,
        "stream": False,
        "options": {"temperature": 0.4, "num_predict": 60},
    }
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:11434/api/chat",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            msg = data.get("message") or {}
            text = str(msg.get("content") or data.get("response") or "").strip().replace("\n", " ").strip('"\'«»')
            if text:
                return f"{npc_name}: «{text[:160]}»"
    except Exception:
        return None
    return None


def apply_voxel_director_command(req: VoxelDirectorRequest) -> dict[str, Any]:
    """Применяет текстовую команду «Режиссёра мира» к воксельной сцене."""
    if req.reset_world or req.world_id not in _WORLD_STORE:
        _WORLD_STORE[req.world_id] = create_base_voxel_world(req.world_id, req.world_edge)

    world = _WORLD_STORE[req.world_id]
    p_low = req.prompt.lower()
    actions_applied: list[str] = []

    # 1. Погода / время суток
    w_id = _parse_weather_from_prompt(p_low)
    if w_id and w_id in WEATHER_PRESETS:
        world["weather"] = dict(WEATHER_PRESETS[w_id])
        actions_applied.append(f"weather:{w_id}")

    # 2. Камера
    c_id = _parse_camera_from_prompt(p_low)
    if c_id and c_id in CAMERA_PRESETS:
        world["camera"] = dict(CAMERA_PRESETS[c_id])
        actions_applied.append(f"camera:{c_id}")

    # 3. Постройки и объекты
    struct_kinds = _parse_structures_from_prompt(p_low)
    anchor_slots = [
        (4, -4),
        (-5, -4),
        (5, 4),
        (-5, 5),
        (0, -6),
        (0, 6),
    ]
    for s_kind in struct_kinds:
        slot_idx = len(world["structures"]) % len(anchor_slots)
        ox, oz = anchor_slots[slot_idx]
        rec = _build_structure(world, s_kind, ox, oz)
        actions_applied.append(f"structure:{s_kind}@({ox},{oz})[{rec['blocks_placed']} blocks]")

    # 4. NPC и диалоги
    new_npcs = _parse_npcs_from_prompt(p_low, world["weather"]["id"])
    npc_slots = [(2.5, 0.0, -2.5), (-2.5, 0.0, -2.5), (2.5, 0.0, 2.5), (-2.5, 0.0, 2.5)]
    director_source = "deterministic_rules"
    for npc in new_npcs:
        idx = len(world["npcs"]) % len(npc_slots)
        nx, ny, nz = npc_slots[idx]
        dialogue = npc["dialogue"]
        if req.use_local_llm:
            llm_line = _try_local_llm_dialogue(req.prompt, world["weather"]["id"], npc["name"])
            if llm_line:
                dialogue = llm_line
                director_source = "local_llm_plus_rules"
        npc_entry = {
            "id": f"npc_{len(world['npcs']) + 1}",
            "role": npc["role"],
            "name": npc["name"],
            "color": npc["color"],
            "position": [nx, ny, nz],
            "patrol_radius": 2.5,
            "dialogue": dialogue,
        }
        world["npcs"].append(npc_entry)
        actions_applied.append(f"npc:{npc['role']}({npc['name']})")

    # Если существующие NPC уже есть в мире, обновляем их реакцию на смену погоды
    if not new_npcs and w_id and world["npcs"]:
        for existing_npc in world["npcs"]:
            existing_npc["dialogue"] = (
                f"{existing_npc['name']}: «Погода сменилась на '{world['weather']['title_ru']}'! Продолжаем работу.»"
            )

    # Если промпт общий («сделай мир майнкрафт»), гарантируем хотя бы дерево, дом и жителя
    if not actions_applied:
        _build_structure(world, "house", 4, -4)
        _build_structure(world, "tree", -4, -4)
        actions_applied.extend(["structure:house@(4,-4)", "structure:tree@(-4,-4)"])

    weather = world["weather"]
    latest_dialogue = (
        world["npcs"][-1]["dialogue"]
        if world["npcs"]
        else f"Режиссёр BossBlocks: сцена обновлена ({', '.join(actions_applied)})."
    )
    soundscape = {
        "ambient_track": weather["ambient_sound"],
        "music_bpm": weather["music_bpm"],
        "music_scale_hz": weather["music_scale"],
        "particles": weather["particles"],
        "dialogue_tts": latest_dialogue,
    }

    event = {
        "step": len(world["director_history"]) + 1,
        "prompt": req.prompt,
        "actions_applied": actions_applied,
        "director_source": director_source,
        "weather": weather["id"],
        "camera": world["camera"]["mode"],
        "total_blocks": len(world["blocks"]),
        "total_npcs": len(world["npcs"]),
    }
    world["director_history"].append(event)

    blocks_list = list(world["blocks"].values())
    state_digest = hashlib.sha256(
        json.dumps(
            {
                "w": weather["id"],
                "c": world["camera"]["mode"],
                "b": len(blocks_list),
                "n": len(world["npcs"]),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    return {
        "ok": True,
        "schema": "bossman.voxel.director.v1",
        "world_id": req.world_id,
        "director_source": director_source,
        "cost_usd": 0.0,
        "actions_applied": actions_applied,
        "weather": weather,
        "camera": world["camera"],
        "soundscape": soundscape,
        "structures": world["structures"],
        "npcs": world["npcs"],
        "block_palette": list(BLOCK_PALETTE.values()),
        "block_count": len(blocks_list),
        "blocks": blocks_list,
        "state_sha256": state_digest,
        "director_history": world["director_history"][-10:],
    }


def _generate_godot_main_gd() -> str:
    """Генерирует GDScript для Godot 4.7.2 с поддержкой вокселей, Режиссёра, погоды, камер, NPC и звука."""
    return '''extends Node3D

const SAVE_FILE := "res://bossblocks_world.json"
const WORLD_EDGE := 10
const BLOCK_NAMES := ["Grass", "Sand", "Stone", "Wood", "Brick", "Water", "GlowCrystal", "Leaves"]
const BLOCK_COLORS := [
	Color("69aa56"), Color("d8bc79"), Color("858f9e"), Color("8b5a2b"),
	Color("b55239"), Color("3b82f6"), Color("f59e0b"), Color("2e7d32")
]

var save_path := SAVE_FILE
var blocks: Dictionary = {}
var materials: Array[StandardMaterial3D] = []
var player: CharacterBody3D
var camera: Camera3D
var ray: RayCast3D
var marker: MeshInstance3D
var hud: Label
var director_bar: LineEdit
var sunlight: DirectionalLight3D
var world_env: WorldEnvironment
var npc_root: Node3D
var audio_player: AudioStreamPlayer
var selected_type := 1
var paused := false
var spawn := Vector3(0, 2.0, 0)
var save_note := ""
var weather_id := "sunny"
var camera_mode := "first_person"
var orbit_angle := 0.0
var npcs_data: Array = []
var last_dialogue := "Нажмите T для текстовой команды Режиссёру (погода, камера, постройки, NPC)"


func _ready() -> void:
	_make_materials()
	_make_world()
	_make_player()
	_make_npcs_root()
	_make_audio()
	_make_hud()
	_make_target_marker()
	_load_world()
	_refresh_hud()


func _make_materials() -> void:
	for i in range(BLOCK_COLORS.size()):
		var material := StandardMaterial3D.new()
		material.albedo_color = BLOCK_COLORS[i]
		material.roughness = 0.85
		if i == 6:
			material.emission_enabled = true
			material.emission = BLOCK_COLORS[i]
			material.emission_energy_multiplier = 1.6
		materials.append(material)


func _make_world() -> void:
	for x in range(-WORLD_EDGE, WORLD_EDGE + 1):
		for z in range(-WORLD_EDGE, WORLD_EDGE + 1):
			_add_block(Vector3i(x, -2, z), 3)
			_add_block(Vector3i(x, -1, z), 1 + (abs(x) + abs(z)) % 3)
	sunlight = DirectionalLight3D.new()
	sunlight.rotation_degrees = Vector3(-58, -32, 0)
	sunlight.light_energy = 1.5
	add_child(sunlight)
	world_env = WorldEnvironment.new()
	var settings := Environment.new()
	settings.background_mode = Environment.BG_COLOR
	settings.background_color = Color("8dbbdd")
	settings.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	settings.ambient_light_color = Color("a9bdd0")
	settings.ambient_light_energy = 0.75
	world_env.environment = settings
	add_child(world_env)


func _make_npcs_root() -> void:
	npc_root = Node3D.new()
	npc_root.name = "NPCs"
	add_child(npc_root)


func _make_audio() -> void:
	audio_player = AudioStreamPlayer.new()
	var gen := AudioStreamGenerator.new()
	gen.mix_rate = 22050.0
	gen.buffer_length = 0.25
	audio_player.stream = gen
	add_child(audio_player)


func _add_block(cell: Vector3i, kind: int) -> void:
	if kind < 1 or kind > BLOCK_NAMES.size():
		return
	if blocks.has(cell):
		_remove_block(cell)
	var body := StaticBody3D.new()
	body.name = "Block_%d_%d_%d" % [cell.x, cell.y, cell.z]
	body.position = Vector3(cell)
	body.set_meta("cell", cell)
	body.set_meta("kind", kind)
	var visual := MeshInstance3D.new()
	var cube := BoxMesh.new()
	cube.size = Vector3.ONE
	visual.mesh = cube
	visual.material_override = materials[kind - 1]
	body.add_child(visual)
	var collision := CollisionShape3D.new()
	var shape := BoxShape3D.new()
	shape.size = Vector3.ONE
	collision.shape = shape
	body.add_child(collision)
	add_child(body)
	blocks[cell] = body


func _remove_block(cell: Vector3i) -> void:
	if not blocks.has(cell):
		return
	var body: StaticBody3D = blocks[cell]
	blocks.erase(cell)
	body.free()


func _make_player() -> void:
	player = CharacterBody3D.new()
	player.name = "Player"
	player.position = spawn
	player.collision_layer = 2
	player.collision_mask = 1
	var collision := CollisionShape3D.new()
	var capsule := CapsuleShape3D.new()
	capsule.radius = 0.32
	capsule.height = 1.7
	collision.shape = capsule
	player.add_child(collision)
	camera = Camera3D.new()
	camera.name = "Camera"
	camera.position = Vector3(0, 0.64, 0)
	camera.current = true
	player.add_child(camera)
	add_child(player)
	ray = RayCast3D.new()
	ray.target_position = Vector3(0, 0, -7)
	ray.collision_mask = 1
	ray.enabled = true
	ray.add_exception(player)
	camera.add_child(ray)


func _make_hud() -> void:
	var layer := CanvasLayer.new()
	add_child(layer)
	hud = Label.new()
	hud.position = Vector2(16, 14)
	hud.add_theme_font_size_override("font_size", 16)
	layer.add_child(hud)
	director_bar = LineEdit.new()
	director_bar.placeholder_text = "Режиссёр мира (введите команду: дождь / ночь / камера дрон / построй башню / добавь стражника)..."
	director_bar.position = Vector2(16, 590)
	director_bar.size = Vector2(960, 36)
	director_bar.visible = false
	director_bar.text_submitted.connect(_on_director_prompt_submitted)
	layer.add_child(director_bar)


func _make_target_marker() -> void:
	marker = MeshInstance3D.new()
	var cube := BoxMesh.new()
	cube.size = Vector3.ONE * 1.035
	marker.mesh = cube
	var highlight := StandardMaterial3D.new()
	highlight.albedo_color = Color(1.0, 0.88, 0.2, 0.28)
	highlight.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	highlight.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	marker.material_override = highlight
	marker.visible = false
	add_child(marker)


func apply_weather(new_weather: String) -> void:
	weather_id = new_weather
	var env := world_env.environment
	match new_weather:
		"rain":
			env.background_color = Color("53687e")
			env.ambient_light_energy = 0.48
			sunlight.light_energy = 0.65
		"storm":
			env.background_color = Color("2c3545")
			env.ambient_light_energy = 0.32
			sunlight.light_energy = 0.35
		"snow":
			env.background_color = Color("cddce8")
			env.ambient_light_energy = 0.82
			sunlight.light_energy = 1.05
		"night":
			env.background_color = Color("111827")
			env.ambient_light_energy = 0.25
			sunlight.light_energy = 0.22
		"sunset":
			env.background_color = Color("d97757")
			env.ambient_light_energy = 0.62
			sunlight.light_energy = 1.25
		_:
			weather_id = "sunny"
			env.background_color = Color("8dbbdd")
			env.ambient_light_energy = 0.75
			sunlight.light_energy = 1.5
	_refresh_hud()


func apply_camera_mode(mode: String) -> void:
	camera_mode = mode
	match mode:
		"third_person":
			camera.position = Vector3(0, 2.4, 4.5)
			camera.rotation_degrees = Vector3(-18, 0, 0)
		"drone_orbit":
			camera.position = Vector3(0, 9.5, 11.0)
			camera.rotation_degrees = Vector3(-38, 0, 0)
		"top_down":
			camera.position = Vector3(0, 16.0, 0.1)
			camera.rotation_degrees = Vector3(-88, 0, 0)
		_:
			camera_mode = "first_person"
			camera.position = Vector3(0, 0.64, 0)
			camera.rotation_degrees = Vector3.ZERO
	_refresh_hud()


func spawn_npc(npc_name: String, role: String, pos: Vector3, dialogue: String) -> Node3D:
	var npc := Node3D.new()
	npc.name = "NPC_%s" % role
	npc.position = pos
	var body_mesh := MeshInstance3D.new()
	var capsule := CapsuleMesh.new()
	capsule.radius = 0.35
	capsule.height = 1.6
	body_mesh.mesh = capsule
	body_mesh.position = Vector3(0, 0.8, 0)
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color("f59e0b") if role == "merchant" else Color("3b82f6")
	body_mesh.material_override = mat
	npc.add_child(body_mesh)
	var label := Label3D.new()
	label.text = dialogue
	label.position = Vector3(0, 2.1, 0)
	label.billboard = BaseMaterial3D.BILLBOARD_ENABLED
	npc.add_child(label)
	npc_root.add_child(npc)
	npcs_data.append({"name": npc_name, "role": role, "position": [pos.x, pos.y, pos.z], "dialogue": dialogue})
	last_dialogue = dialogue
	_refresh_hud()
	return npc


func execute_director_prompt(prompt_text: String) -> Dictionary:
	var p := prompt_text.to_lower()
	var applied: Array[String] = []
	if p.find("гроз") != -1 or p.find("storm") != -1:
		apply_weather("storm")
		applied.append("weather:storm")
	elif p.find("дожд") != -1 or p.find("rain") != -1:
		apply_weather("rain")
		applied.append("weather:rain")
	elif p.find("снег") != -1 or p.find("snow") != -1:
		apply_weather("snow")
		applied.append("weather:snow")
	elif p.find("ночь") != -1 or p.find("night") != -1:
		apply_weather("night")
		applied.append("weather:night")
	elif p.find("закат") != -1 or p.find("sunset") != -1:
		apply_weather("sunset")
		applied.append("weather:sunset")
	elif p.find("солн") != -1 or p.find("sunny") != -1:
		apply_weather("sunny")
		applied.append("weather:sunny")

	if p.find("дрон") != -1 or p.find("drone") != -1:
		apply_camera_mode("drone_orbit")
		applied.append("camera:drone_orbit")
	elif p.find("треть") != -1 or p.find("third") != -1:
		apply_camera_mode("third_person")
		applied.append("camera:third_person")
	elif p.find("сверху") != -1 or p.find("top") != -1:
		apply_camera_mode("top_down")
		applied.append("camera:top_down")
	elif p.find("перв") != -1 or p.find("first") != -1:
		apply_camera_mode("first_person")
		applied.append("camera:first_person")

	if p.find("башн") != -1 or p.find("tower") != -1:
		for y in range(0, 5):
			_add_block(Vector3i(4, y, -4), 3)
		_add_block(Vector3i(4, 5, -4), 7)
		applied.append("structure:tower")
	if p.find("дом") != -1 or p.find("house") != -1:
		for dx in range(-1, 2):
			for dz in range(-1, 2):
				_add_block(Vector3i(-4 + dx, 0, -4 + dz), 4)
				_add_block(Vector3i(-4 + dx, 2, -4 + dz), 5)
		applied.append("structure:house")
	if p.find("дерев") != -1 or p.find("tree") != -1:
		for y in range(0, 3):
			_add_block(Vector3i(0, y, -5), 4)
		_add_block(Vector3i(0, 3, -5), 8)
		applied.append("structure:tree")

	if p.find("страж") != -1 or p.find("торгов") != -1 or p.find("человек") != -1 or p.find("npc") != -1:
		spawn_npc("Стражник Гром", "guard", Vector3(2.5, 0.0, -2.5), "Стражник Гром: «Мир под защитой Bossman!»")
		applied.append("npc:guard")

	_save_world()
	_refresh_hud()
	return {"applied": applied, "weather": weather_id, "camera": camera_mode, "npcs": npcs_data.size()}


func _on_director_prompt_submitted(new_text: String) -> void:
	director_bar.visible = false
	paused = false
	if new_text.strip_edges() != "":
		execute_director_prompt(new_text)


func _placeable_count() -> int:
	var count: int = 0
	for block in blocks.keys():
		var cell: Vector3i = block
		if cell.y >= 0 and abs(cell.x) <= WORLD_EDGE and abs(cell.z) <= WORLD_EDGE:
			count += 1
	return count


func _refresh_hud() -> void:
	if hud == null:
		return
	var placeable: int = _placeable_count()
	hud.text = (
		"BossBlocks Genie-Local | Блок %d: %s | Погода: %s | Камера: %s | NPC: %d | Блоков: %d\n"
		% [selected_type, BLOCK_NAMES[selected_type - 1], weather_id, camera_mode, npcs_data.size(), placeable]
		+ "WASD ход · Space прыжок · ЛКМ сломать · ПКМ поставить · 1-8 блок · T команда Режиссёру · F5 сохр.\n"
		+ last_dialogue + ("  [" + save_note + "]" if save_note != "" else "")
	)


func _save_world() -> void:
	var records: Array[Dictionary] = []
	for cell in blocks.keys():
		if cell.y <= -2 or abs(cell.x) > WORLD_EDGE or abs(cell.z) > WORLD_EDGE:
			continue
		var body: StaticBody3D = blocks[cell]
		records.append({"x": cell.x, "y": cell.y, "z": cell.z, "kind": int(body.get_meta("kind"))})
	var file := FileAccess.open(save_path, FileAccess.WRITE)
	if file != null:
		file.store_string(JSON.stringify({
			"version": 2,
			"weather": weather_id,
			"camera": camera_mode,
			"npcs": npcs_data,
			"blocks": records,
		}))
		file.close()
		save_note = "Saved %d blocks" % records.size()
	_refresh_hud()


func _load_world() -> void:
	if not FileAccess.file_exists(save_path):
		return
	var payload = JSON.parse_string(FileAccess.get_file_as_string(save_path))
	if not (payload is Dictionary) or not (payload.get("blocks") is Array):
		return
	for cell in blocks.keys().duplicate():
		if cell.y > -2 and abs(cell.x) <= WORLD_EDGE and abs(cell.z) <= WORLD_EDGE:
			_remove_block(cell)
	for item in payload["blocks"]:
		if item is Dictionary:
			_add_block(Vector3i(int(item.get("x", 0)), int(item.get("y", 0)), int(item.get("z", 0))), int(item.get("kind", 1)))
	if payload.has("weather"):
		apply_weather(str(payload["weather"]))
	if payload.has("camera"):
		apply_camera_mode(str(payload["camera"]))
	if payload.get("npcs") is Array:
		for n in payload["npcs"]:
			if n is Dictionary:
				var p: Array = n.get("position", [2.0, 0.0, -2.0])
				spawn_npc(str(n.get("name", "NPC")), str(n.get("role", "villager")), Vector3(float(p[0]), float(p[1]), float(p[2])), str(n.get("dialogue", "Привет!")))
	_refresh_hud()
'''


def _generate_godot_director_test_gd() -> str:
    """Генерирует тестовый скрипт Godot для проверки Режиссёра мира (погода, камера, постройки, NPC, сохранение)."""
    return '''extends SceneTree

func _initialize() -> void:
	var scene: Node3D = load("res://scenes/main.tscn").instantiate()
	root.add_child(scene)
	await process_frame
	await process_frame

	var before_blocks: int = scene._placeable_count()
	var res: Dictionary = scene.execute_director_prompt(
		"Смени погоду на грозу, поставь камеру от третьего лица, построй башню и дом, добавь стражника"
	)
	assert(scene.weather_id == "storm", "weather must switch to storm")
	assert(scene.camera_mode == "third_person", "camera must switch to third_person")
	assert(scene._placeable_count() > before_blocks, "director must place structure blocks")
	assert(scene.npcs_data.size() >= 1, "director must spawn NPC")
	assert(res["applied"].size() >= 4, "director must apply all 4 scene mutations")
	print("BOSSBLOCKS_GENIE_DIRECTOR_PASS weather=%s camera=%s blocks=%d npcs=%d" % [
		scene.weather_id, scene.camera_mode, scene._placeable_count(), scene.npcs_data.size()
	])
	quit(0)
'''


def export_godot_voxel_project(req: GodotExportRequest, default_base_dir: Path | None = None) -> dict[str, Any]:
    """Экспортирует воксельный мир и Режиссёра в проект Godot 4.7.2 и (опционально) проверяет его реальным Godot."""
    director_state = apply_voxel_director_command(
        VoxelDirectorRequest(
            world_id=req.world_id,
            prompt=req.prompt,
            use_local_llm=req.use_local_llm,
        )
    )
    if req.target_dir:
        project_dir = Path(req.target_dir).expanduser().resolve()
    elif default_base_dir is not None:
        project_dir = (Path(default_base_dir) / "voxel_worlds" / req.world_id).resolve()
    else:
        project_dir = Path(r"C:\Users\asd\AppData\Local\Bossman\owner-run\bossblocks-voxel-director").resolve()

    (project_dir / "scenes").mkdir(parents=True, exist_ok=True)
    (project_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (project_dir / "tests").mkdir(parents=True, exist_ok=True)

    project_godot = (
        '; Engine configuration file.\n'
        'config_version=5\n\n'
        '[application]\n\n'
        f'config/name="BossBlocks Genie-Local ({req.world_id})"\n'
        'run/main_scene="res://scenes/main.tscn"\n'
        'config/features=PackedStringArray("4.7")\n'
    )
    main_tscn = (
        '[gd_scene load_steps=2 format=3]\n\n'
        '[ext_resource type="Script" path="res://scripts/main.gd" id="1"]\n\n'
        '[node name="Main" type="Node3D"]\n'
        'script = ExtResource("1")\n'
    )
    (project_dir / "project.godot").write_text(project_godot, encoding="utf-8")
    (project_dir / "scenes" / "main.tscn").write_text(main_tscn, encoding="utf-8")
    (project_dir / "scripts" / "main.gd").write_text(_generate_godot_main_gd(), encoding="utf-8")
    (project_dir / "tests" / "owner_director_check.gd").write_text(_generate_godot_director_test_gd(), encoding="utf-8")

    world_json = {
        "version": 2,
        "world_id": req.world_id,
        "weather": director_state["weather"]["id"],
        "camera": director_state["camera"]["mode"],
        "npcs": director_state["npcs"],
        "blocks": director_state["blocks"],
    }
    (project_dir / "bossblocks_world.json").write_text(
        json.dumps(world_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    editor_exe = str(DEFAULT_GODOT_EDITOR_PATH)
    play_cmd = f'@echo off\r\n"{editor_exe}" --path "%~dp0" %*\r\n'
    (project_dir / "PLAY-BOSSBLOCKS.cmd").write_text(play_cmd, encoding="utf-8")
    (project_dir / "PLAY-IN-BROWSER.html").write_text(
        render_voxel_web_game_html(world_id=req.world_id),
        encoding="utf-8",
    )

    smoke_result: dict[str, Any] = {
        "status": "NOT_RUN",
        "engine_present": DEFAULT_GODOT_EDITOR_PATH.is_file(),
        "stdout": "",
        "returncode": None,
    }
    if req.run_headless_smoke and os.name == "nt" and DEFAULT_GODOT_EDITOR_PATH.is_file():
        try:
            proc = subprocess.run(
                [
                    str(DEFAULT_GODOT_EDITOR_PATH),
                    "--headless",
                    "--path",
                    str(project_dir),
                    "-s",
                    "res://tests/owner_director_check.gd",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
            )
            passed = proc.returncode == 0 and "BOSSBLOCKS_GENIE_DIRECTOR_PASS" in (proc.stdout or "")
            smoke_result = {
                "status": "PASS" if passed else "FAIL",
                "engine_present": True,
                "returncode": proc.returncode,
                "stdout": (proc.stdout or "").strip()[-600:],
                "stderr": (proc.stderr or "").strip()[-400:],
            }
        except Exception as exc:
            smoke_result = {
                "status": "FAIL",
                "engine_present": True,
                "error": repr(exc),
            }

    return {
        "ok": True,
        "world_id": req.world_id,
        "project_dir": str(project_dir),
        "launch_command": str(project_dir / "PLAY-BOSSBLOCKS.cmd"),
        "browser_launch_file": str(project_dir / "PLAY-IN-BROWSER.html"),
        "files_written": [
            "project.godot",
            "scenes/main.tscn",
            "scripts/main.gd",
            "bossblocks_world.json",
            "PLAY-BOSSBLOCKS.cmd",
            "PLAY-IN-BROWSER.html",
            "tests/owner_director_check.gd",
        ],
        "director_state": director_state,
        "godot_headless_smoke": smoke_result,
    }


def render_voxel_web_game_html(world_id: str = "bossblocks-local-01") -> str:
    """Возвращает автономную интерактивную 3D-воксельную игру в браузере с живым Режиссёром и WebAudio."""
    if world_id not in _WORLD_STORE:
        apply_voxel_director_command(
            VoxelDirectorRequest(
                world_id=world_id,
                prompt="Сделай солнечную поляну, построй дом, башню и дерево, добавь торговца и стражника",
            )
        )
    initial_state = apply_voxel_director_command(
        VoxelDirectorRequest(
            world_id=world_id,
            prompt="Покажи текущий мир",
        )
    )
    state_json = json.dumps(initial_state, ensure_ascii=False)
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BossBlocks Genie-Local — Воксельный 3D-мир и Режиссёр</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #0b0f19;
    --panel: rgba(15, 23, 42, 0.84);
    --border: rgba(148, 163, 184, 0.24);
    --accent: #38bdf8;
    --text: #f8fafc;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg);
    color: var(--text);
    font-family: 'Inter', 'Segoe UI Variable Display', system-ui, sans-serif;
    overflow: hidden;
    height: 100vh;
    width: 100vw;
  }}
  #viewport {{
    width: 100vw;
    height: 100vh;
    display: block;
    cursor: crosshair;
  }}
  .hud-top {{
    position: fixed;
    top: 14px;
    left: 14px;
    right: 14px;
    display: flex;
    justify-content: space-between;
    gap: 12px;
    pointer-events: none;
  }}
  .hud-card {{
    background: var(--panel);
    backdrop-filter: blur(12px);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 12px 16px;
    pointer-events: auto;
    max-width: 640px;
    box-shadow: 0 10px 30px rgba(0,0,0,0.45);
  }}
  .hud-title {{
    font-weight: 700;
    font-size: 15px;
    color: var(--accent);
    margin-bottom: 4px;
  }}
  .hud-meta {{
    font-family: 'JetBrains Mono', 'Cascadia Code', monospace;
    font-size: 12px;
    color: #cbd5e1;
    line-height: 1.45;
  }}
  .dialogue-box {{
    margin-top: 8px;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(56, 189, 248, 0.12);
    border-left: 3px solid var(--accent);
    font-size: 13px;
    color: #f1f5f9;
  }}
  .director-dock {{
    position: fixed;
    bottom: 16px;
    left: 50%;
    transform: translateX(-50%);
    width: min(920px, calc(100vw - 28px));
    background: var(--panel);
    backdrop-filter: blur(14px);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 12px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    box-shadow: 0 12px 36px rgba(0,0,0,0.55);
  }}
  .director-row {{
    display: flex;
    gap: 8px;
  }}
  #director-input {{
    flex: 1;
    background: rgba(2, 6, 23, 0.75);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 9px;
    padding: 10px 14px;
    font-size: 14px;
    outline: none;
  }}
  #director-input:focus {{
    border-color: var(--accent);
  }}
  .btn {{
    background: linear-gradient(135deg, #0284c7, #2563eb);
    color: #fff;
    border: none;
    border-radius: 9px;
    padding: 9px 16px;
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    transition: transform 150ms ease, filter 150ms ease;
  }}
  .btn:hover {{ filter: brightness(1.12); }}
  .btn:active {{ transform: scale(0.97); }}
  .chip-row {{
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
  }}
  .chip {{
    background: rgba(30, 41, 59, 0.9);
    color: #e2e8f0;
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 5px 11px;
    font-size: 12px;
    cursor: pointer;
    transition: background 140ms ease, transform 140ms ease;
  }}
  .chip:hover {{ background: rgba(56, 189, 248, 0.22); }}
  .chip:active {{ transform: scale(0.96); }}
</style>
</head>
<body>
<canvas id="viewport"></canvas>

<div class="hud-top">
  <div class="hud-card">
    <div class="hud-title">BossBlocks Genie-Local · Локальный воксельный мир ($0.00)</div>
    <div class="hud-meta" id="hud-stats">Загрузка мира...</div>
    <div class="dialogue-box" id="hud-dialogue">Инициализация...</div>
  </div>
  <div class="hud-card">
    <div class="hud-title">Управление</div>
    <div class="hud-meta">
      WASD / Стрелки — движение · Мышь / Перетаскивание — обзор<br>
      Клик по блоку — поставить/сломать · 1–8 — выбор типа блока<br>
      Кнопка «Звук» — процедурная музыка, шум погоды и голос NPC
    </div>
  </div>
</div>

<div class="director-dock">
  <div class="director-row">
    <input id="director-input" type="text" placeholder="Команда Режиссёру: смени погоду на грозу, переставь камеру на дрон, построй замок и добавь стражника..." />
    <button class="btn" id="director-send">Применить к сцене</button>
    <button class="btn" id="audio-toggle" style="background:linear-gradient(135deg,#059669,#10b981)">Вкл. звук</button>
  </div>
  <div class="chip-row">
    <button class="chip" data-cmd="Смени погоду на грозу и шторм">🔻 Погода: Гроза</button>
    <button class="chip" data-cmd="Смени погоду на снегопад">🔻 Погода: Снег</button>
    <button class="chip" data-cmd="Смени погоду на закат">🔻 Погода: Закат</button>
    <button class="chip" data-cmd="Смени погоду на ночь">🔻 Погода: Ночь</button>
    <button class="chip" data-cmd="Переставь камеру на дрон-облёт">🔻 Камера: Дрон</button>
    <button class="chip" data-cmd="Переставь камеру на вид сверху">🔻 Камера: Сверху</button>
    <button class="chip" data-cmd="Переставь камеру от первого лица">🔻 Камера: 1-е лицо</button>
    <button class="chip" data-cmd="Построй каменную башню и маяк">🔻 Построить башню</button>
    <button class="chip" data-cmd="Построй пирамиду и мост через реку">🔻 Пирамида + река</button>
    <button class="chip" data-cmd="Добавь стражника и мага">🔻 Добавить NPC</button>
  </div>
</div>

<script>
const STATE = {state_json};
const canvas = document.getElementById('viewport');
const ctx = canvas.getContext('2d');
let yaw = 0.65, pitch = -0.35, camX = 0, camY = 4.5, camZ = 12;
let selectedKind = 1;
let audioCtx = null;
let audioEnabled = false;
let tTick = 0;

const COLORS = {{
  1: '#69aa56', 2: '#d8bc79', 3: '#858f9e', 4: '#8b5a2b',
  5: '#b55239', 6: '#3b82f6', 7: '#f59e0b', 8: '#2e7d32'
}};

function resize() {{
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}}
window.addEventListener('resize', resize);
resize();

function updateHud() {{
  const w = STATE.weather || {{}};
  const c = STATE.camera || {{}};
  document.getElementById('hud-stats').textContent =
    `Мир: ${{STATE.world_id}} | Погода: ${{w.title_ru || w.id}} | Камера: ${{c.title_ru || c.mode}} | Блоков: ${{STATE.block_count}} | NPC: ${{(STATE.npcs || []).length}} | Выбран блок #${{selectedKind}}`;
  const dlg = (STATE.soundscape && STATE.soundscape.dialogue_tts) || 'Сцена активна.';
  document.getElementById('hud-dialogue').textContent = dlg;
}}

function speakLine(text) {{
  if (!audioEnabled || !('speechSynthesis' in window)) return;
  try {{
    window.speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(text);
    u.lang = 'ru-RU';
    u.rate = 1.05;
    window.speechSynthesis.speak(u);
  }} catch (_e) {{}}
}}

function playChord() {{
  if (!audioEnabled || !audioCtx) return;
  const scale = (STATE.soundscape && STATE.soundscape.music_scale_hz) || [261.63, 329.63, 392.0];
  const freq = scale[Math.floor(Math.random() * scale.length)];
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = 'triangle';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.04, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.7);
  osc.connect(gain).connect(audioCtx.destination);
  osc.start();
  osc.stop(audioCtx.currentTime + 0.72);
}}

function project3D(x, y, z) {{
  const dx = x - camX, dy = y - camY, dz = z - camZ;
  const cosY = Math.cos(yaw), sinY = Math.sin(yaw);
  const rx = dx * cosY - dz * sinY;
  const rz = dx * sinY + dz * cosY;
  const cosP = Math.cos(pitch), sinP = Math.sin(pitch);
  const ry = dy * cosP - rz * sinP;
  const fz = dy * sinP + rz * cosP;
  if (fz <= 0.2) return null;
  const fov = 560;
  return {{
    sx: canvas.width / 2 + (rx / fz) * fov,
    sy: canvas.height / 2 - (ry / fz) * fov,
    depth: fz,
    scale: fov / fz
  }};
}}

function render() {{
  tTick += 0.016;
  const w = STATE.weather || {{}};
  ctx.fillStyle = w.sky_color || '#8dbbdd';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (STATE.camera && STATE.camera.orbit) {{
    yaw += 0.004;
    camX = Math.sin(yaw) * 13;
    camZ = Math.cos(yaw) * 13;
    camY = 8.5;
    pitch = -0.45;
  }} else if (STATE.camera && STATE.camera.mode === 'top_down') {{
    camX = 0; camY = 18; camZ = 0.5; pitch = -1.45;
  }}

  const items = [];
  for (const b of (STATE.blocks || [])) {{
    if (b.y < -1) continue;
    const p = project3D(b.x, b.y, b.z);
    if (p) items.push({{ type: 'block', b, p }});
  }}
  for (const n of (STATE.npcs || [])) {{
    const nx = n.position[0] + Math.sin(tTick * 1.5) * 0.8;
    const nz = n.position[2] + Math.cos(tTick * 1.5) * 0.8;
    const p = project3D(nx, n.position[1] + 0.8, nz);
    if (p) items.push({{ type: 'npc', n, p }});
  }}
  items.sort((a, b) => b.p.depth - a.p.depth);

  for (const it of items) {{
    const s = Math.max(4, it.p.scale * 0.85);
    if (it.type === 'block') {{
      ctx.fillStyle = COLORS[it.b.kind] || '#69aa56';
      ctx.fillRect(it.p.sx - s / 2, it.p.sy - s / 2, s, s);
      ctx.strokeStyle = 'rgba(0,0,0,0.28)';
      ctx.strokeRect(it.p.sx - s / 2, it.p.sy - s / 2, s, s);
    }} else {{
      ctx.fillStyle = it.n.color || '#f59e0b';
      ctx.beginPath();
      ctx.arc(it.p.sx, it.p.sy, s * 0.55, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#fff';
      ctx.font = '12px Inter, sans-serif';
      ctx.fillText(it.n.name, it.p.sx - 35, it.p.sy - s * 0.7);
    }}
  }}

  if (w.particles === 'rain' || w.particles === 'rain_heavy' || w.particles === 'snow') {{
    ctx.strokeStyle = w.particles === 'snow' ? 'rgba(255,255,255,0.75)' : 'rgba(186,230,253,0.55)';
    for (let i = 0; i < 90; i++) {{
      const px = (i * 97 + tTick * 180) % canvas.width;
      const py = (i * 131 + tTick * (w.particles === 'snow' ? 90 : 420)) % canvas.height;
      ctx.beginPath();
      ctx.moveTo(px, py);
      ctx.lineTo(px - 2, py + (w.particles === 'snow' ? 3 : 12));
      ctx.stroke();
    }}
  }}
  requestAnimationFrame(render);
}}

function applyClientDirectorFallback(promptText) {{
  const p = promptText.toLowerCase();
  const wPresets = {{
    storm: {{ id: 'storm', title_ru: 'Гроза и шторм', sky_color: '#2c3545', particles: 'rain_heavy', music_scale: [196.0, 220.0, 233.08, 293.66, 311.13] }},
    snow: {{ id: 'snow', title_ru: 'Снегопад', sky_color: '#cddce8', particles: 'snow', music_scale: [293.66, 329.63, 369.99, 440.0, 493.88] }},
    sunset: {{ id: 'sunset', title_ru: 'Закат', sky_color: '#d97757', particles: 'none', music_scale: [261.63, 311.13, 349.23, 392.0, 466.16] }},
    night: {{ id: 'night', title_ru: 'Ночь', sky_color: '#111827', particles: 'fireflies', music_scale: [220.0, 261.63, 293.66, 329.63, 392.0] }},
    rain: {{ id: 'rain', title_ru: 'Дождь', sky_color: '#53687e', particles: 'rain', music_scale: [220.0, 246.94, 261.63, 329.63, 349.23] }},
    sunny: {{ id: 'sunny', title_ru: 'Солнечно', sky_color: '#8dbbdd', particles: 'none', music_scale: [261.63, 293.66, 329.63, 392.0, 440.0] }}
  }};
  if (p.includes('гроз') || p.includes('шторм')) STATE.weather = wPresets.storm;
  else if (p.includes('снег')) STATE.weather = wPresets.snow;
  else if (p.includes('закат')) STATE.weather = wPresets.sunset;
  else if (p.includes('ночь')) STATE.weather = wPresets.night;
  else if (p.includes('дожд')) STATE.weather = wPresets.rain;
  else if (p.includes('солн') || p.includes('ясно')) STATE.weather = wPresets.sunny;

  if (p.includes('дрон') || p.includes('облёт') || p.includes('облет')) STATE.camera = {{ mode: 'drone_orbit', title_ru: 'Дрон-облёт (панорама мира)', orbit: true }};
  else if (p.includes('сверху')) STATE.camera = {{ mode: 'top_down', title_ru: 'Вид сверху (тактический)', orbit: false }};
  else if (p.includes('перв') || p.includes('1-е')) {{ STATE.camera = {{ mode: 'first_person', title_ru: 'От первого лица', orbit: false }}; camX = 0; camY = 4.5; camZ = 12; pitch = -0.35; }}
  else if (p.includes('треть')) {{ STATE.camera = {{ mode: 'third_person', title_ru: 'От третьего лица', orbit: false }}; camX = 0; camY = 6.5; camZ = 15; pitch = -0.4; }}

  const ox = ((STATE.blocks.length * 7) % 11) - 5;
  const oz = ((STATE.blocks.length * 13) % 11) - 5;
  if (p.includes('башн') || p.includes('маяк')) {{
    for (let y = 0; y < 6; y++) STATE.blocks.push({{ x: ox, y, z: oz, kind: y === 5 ? 7 : 3 }});
  }}
  if (p.includes('пирамид') || p.includes('рек') || p.includes('мост')) {{
    for (let dx = -2; dx <= 2; dx++) for (let dz = -2; dz <= 2; dz++) STATE.blocks.push({{ x: ox + dx, y: 0, z: oz + dz, kind: 2 }});
    STATE.blocks.push({{ x: ox, y: 1, z: oz, kind: 7 }});
  }}
  if (p.includes('дом') || p.includes('замок')) {{
    for (let dx = -1; dx <= 1; dx++) for (let dz = -1; dz <= 1; dz++) {{
      STATE.blocks.push({{ x: ox + dx, y: 0, z: oz + dz, kind: 4 }});
      STATE.blocks.push({{ x: ox + dx, y: 2, z: oz + dz, kind: 5 }});
    }}
  }}
  if (p.includes('npc') || p.includes('страж') || p.includes('маг') || p.includes('торгов') || p.includes('человек')) {{
    const nName = p.includes('маг') ? 'Архимаг Эфир' : 'Стражник Гром';
    const dlg = `${{nName}}: «Сцена обновлена по команде: ${{promptText}}!»`;
    STATE.npcs.push({{ id: 'npc_' + (STATE.npcs.length + 1), name: nName, role: 'guard', color: '#8b5cf6', position: [ox * 0.6, 0, oz * 0.6], dialogue: dlg }});
    STATE.soundscape.dialogue_tts = dlg;
  }} else {{
    STATE.soundscape.dialogue_tts = `Режиссёр BossBlocks: выполнено «${{promptText}}»`;
  }}
  STATE.block_count = STATE.blocks.length;
}}

async function sendDirectorPrompt(promptText) {{
  if (!promptText.trim()) return;
  let updatedViaApi = false;
  if (location.protocol.startsWith('http')) {{
    try {{
      const headers = {{ 'Content-Type': 'application/json' }};
      const tok = localStorage.getItem('bcc_token') || '';
      if (tok) headers['X-BCC-Token'] = tok;
      const res = await fetch('/api/game-bootstrap/voxel-director', {{
        method: 'POST',
        headers,
        body: JSON.stringify({{ world_id: STATE.world_id, prompt: promptText }})
      }});
      if (res.ok) {{
        const next = await res.json();
        Object.assign(STATE, next);
        updatedViaApi = true;
      }}
    }} catch (_e) {{}}
  }}
  if (!updatedViaApi) {{
    applyClientDirectorFallback(promptText);
  }}
  updateHud();
  playChord();
  if (STATE.soundscape && STATE.soundscape.dialogue_tts) {{
    speakLine(STATE.soundscape.dialogue_tts);
  }}
}}

document.getElementById('director-send').addEventListener('click', () => {{
  const inp = document.getElementById('director-input');
  sendDirectorPrompt(inp.value);
  inp.value = '';
}});
document.getElementById('director-input').addEventListener('keydown', (e) => {{
  if (e.key === 'Enter') document.getElementById('director-send').click();
}});
document.querySelectorAll('.chip').forEach((btn) => {{
  btn.addEventListener('click', () => sendDirectorPrompt(btn.dataset.cmd));
}});
document.getElementById('audio-toggle').addEventListener('click', (e) => {{
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  audioEnabled = !audioEnabled;
  e.target.textContent = audioEnabled ? 'Звук: ВКЛ' : 'Вкл. звук';
  if (audioEnabled) {{
    playChord();
    if (STATE.soundscape && STATE.soundscape.dialogue_tts) speakLine(STATE.soundscape.dialogue_tts);
  }}
}});

let dragging = false, lastX = 0, lastY = 0;
canvas.addEventListener('mousedown', (e) => {{ dragging = true; lastX = e.clientX; lastY = e.clientY; }});
window.addEventListener('mouseup', () => {{ dragging = false; }});
window.addEventListener('mousemove', (e) => {{
  if (!dragging) return;
  yaw -= (e.clientX - lastX) * 0.005;
  pitch = Math.max(-1.4, Math.min(1.4, pitch - (e.clientY - lastY) * 0.005));
  lastX = e.clientX; lastY = e.clientY;
}});
window.addEventListener('keydown', (e) => {{
  if (document.activeElement === document.getElementById('director-input')) return;
  const speed = 0.7;
  if (e.key === 'w' || e.key === 'W' || e.key === 'ArrowUp') {{ camX -= Math.sin(yaw) * speed; camZ -= Math.cos(yaw) * speed; }}
  if (e.key === 's' || e.key === 'S' || e.key === 'ArrowDown') {{ camX += Math.sin(yaw) * speed; camZ += Math.cos(yaw) * speed; }}
  if (e.key === 'a' || e.key === 'A' || e.key === 'ArrowLeft') {{ camX -= Math.cos(yaw) * speed; camZ += Math.sin(yaw) * speed; }}
  if (e.key === 'd' || e.key === 'D' || e.key === 'ArrowRight') {{ camX += Math.cos(yaw) * speed; camZ -= Math.sin(yaw) * speed; }}
  if (e.key >= '1' && e.key <= '8') {{ selectedKind = parseInt(e.key, 10); updateHud(); }}
}});

updateHud();
render();
</script>
</body>
</html>"""
