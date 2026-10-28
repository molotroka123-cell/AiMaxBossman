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
    9: {"id": 9, "name": "VelvetRed", "title_ru": "Красный бархат", "color": "#e11d48"},
    10: {"id": 10, "name": "SkinPeach", "title_ru": "Телесный воксель (светлый)", "color": "#f4b895"},
    11: {"id": 11, "name": "SkinBronze", "title_ru": "Бронзовый загар", "color": "#c67d50"},
    12: {"id": 12, "name": "NeonMagenta", "title_ru": "Неоновый пурпур", "color": "#d946ef"},
    13: {"id": 13, "name": "GoldTrim", "title_ru": "Золотой декор", "color": "#fbbf24"},
    14: {"id": 14, "name": "SilkPink", "title_ru": "Розовый шёлк", "color": "#f472b6"},
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
    elif kind == "hot_tub":
        # Шуточное VIP-джакузи с тёплой водой, золотыми бортиками и неоновой подсветкой
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                is_rim = abs(dx) == 2 or abs(dz) == 2
                if is_rim:
                    placed += int(_set_block(world, origin_x + dx, 0, origin_z + dz, 13))
                    if abs(dx) == 2 and abs(dz) == 2:
                        placed += int(_set_block(world, origin_x + dx, 1, origin_z + dz, 12))
                else:
                    placed += int(_set_block(world, origin_x + dx, 0, origin_z + dz, 6))
        placed += int(_set_block(world, origin_x, 1, origin_z, 14))
    elif kind == "heart_bed":
        # Королевская шуточная кровать-сердце WooHoo с бархатным балдахином и золотыми стойками
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                placed += int(_set_block(world, origin_x + dx, 0, origin_z + dz, 9))
        placed += int(_set_block(world, origin_x - 1, 1, origin_z - 2, 14))
        placed += int(_set_block(world, origin_x + 1, 1, origin_z - 2, 14))
        for cx, cz in ((-2, -2), (2, -2), (-2, 2), (2, 2)):
            for y in range(1, 4):
                placed += int(_set_block(world, origin_x + cx, y, origin_z + cz, 13))
        for dx in range(-1, 2):
            placed += int(_set_block(world, origin_x + dx, 4, origin_z - 2, 9))
            placed += int(_set_block(world, origin_x + dx, 4, origin_z, 14))
    elif kind == "neon_cabaret":
        # Неоновая сцена кабаре с бархатными кулисами, подиумом и золотым пилоном
        for dx in range(-3, 4):
            for dz in range(-2, 3):
                placed += int(_set_block(world, origin_x + dx, 0, origin_z + dz, 9 if (dx + dz) % 2 == 0 else 12))
        for y in range(1, 6):
            placed += int(_set_block(world, origin_x, y, origin_z, 13))
            if y < 5:
                placed += int(_set_block(world, origin_x - 3, y, origin_z - 2, 9))
                placed += int(_set_block(world, origin_x + 3, y, origin_z - 2, 9))
        placed += int(_set_block(world, origin_x - 2, 4, origin_z - 2, 12))
        placed += int(_set_block(world, origin_x + 2, 4, origin_z - 2, 12))
    elif kind == "statue_venus":
        # Шуточная анатомическая 3D-скульптура «Венера и Аполлон» с пиксельным сердечком-цензурой
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                placed += int(_set_block(world, origin_x + dx, 0, origin_z + dz, 13))
        # Ноги, бёдра, талия, пышный бюст/торс, плечи, голова и сердечко-цензура
        placed += int(_set_block(world, origin_x - 1, 1, origin_z, 10))
        placed += int(_set_block(world, origin_x + 1, 1, origin_z, 10))
        placed += int(_set_block(world, origin_x - 1, 2, origin_z, 10))
        placed += int(_set_block(world, origin_x + 1, 2, origin_z, 10))
        placed += int(_set_block(world, origin_x, 2, origin_z, 9))
        placed += int(_set_block(world, origin_x, 2, origin_z + 1, 14))  # шуточный пиксель-сердечко
        placed += int(_set_block(world, origin_x - 1, 3, origin_z, 14))
        placed += int(_set_block(world, origin_x, 3, origin_z, 10))
        placed += int(_set_block(world, origin_x + 1, 3, origin_z, 14))
        placed += int(_set_block(world, origin_x - 1, 4, origin_z, 10))
        placed += int(_set_block(world, origin_x, 4, origin_z, 9))
        placed += int(_set_block(world, origin_x + 1, 4, origin_z, 10))
        placed += int(_set_block(world, origin_x - 1, 4, origin_z + 1, 10))  # 3D-рельеф бюста
        placed += int(_set_block(world, origin_x + 1, 4, origin_z + 1, 10))
        placed += int(_set_block(world, origin_x - 2, 4, origin_z, 10))      # руки
        placed += int(_set_block(world, origin_x + 2, 4, origin_z, 10))
        placed += int(_set_block(world, origin_x, 5, origin_z, 10))          # голова
        placed += int(_set_block(world, origin_x, 6, origin_z, 13))          # золотая причёска/корона
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
    if any(k in p_low for k in ("закат", "вечер", "рассвет", "романт", "интим", "sunset", "dusk")):
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
        ("hot_tub", ("джакуз", "бассейн", "ванн", "спа", "hot_tub", "jacuzzi", "spa")),
        ("heart_bed", ("кроват", "сердц", "будуар", "спальн", "woohoo", "heart_bed", "bed")),
        ("neon_cabaret", ("кабаре", "клуб", "сцен", "пилон", "подиум", "лаунж", "cabaret", "club", "lounge")),
        ("statue_venus", ("стату", "венер", "скульптур", "3д модел", "3d модел", "интим", "statue", "venus")),
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


COMEDY_3D_RIGS: dict[str, list[dict[str, Any]]] = {
    "velvet_diva": [
        {"part": "leg_l", "dx": -0.22, "dy": 0.35, "dz": 0.0, "sx": 0.22, "sy": 0.7, "sz": 0.22, "color": "#1e1b4b"},
        {"part": "leg_r", "dx": 0.22, "dy": 0.35, "dz": 0.0, "sx": 0.22, "sy": 0.7, "sz": 0.22, "color": "#1e1b4b"},
        {"part": "hips", "dx": 0.0, "dy": 0.82, "dz": 0.0, "sx": 0.72, "sy": 0.36, "sz": 0.48, "color": "#e11d48"},
        {"part": "glute_l", "dx": -0.18, "dy": 0.80, "dz": -0.24, "sx": 0.30, "sy": 0.30, "sz": 0.24, "color": "#e11d48"},
        {"part": "glute_r", "dx": 0.18, "dy": 0.80, "dz": -0.24, "sx": 0.30, "sy": 0.30, "sz": 0.24, "color": "#e11d48"},
        {"part": "waist", "dx": 0.0, "dy": 1.12, "dz": 0.0, "sx": 0.46, "sy": 0.32, "sz": 0.34, "color": "#e11d48"},
        {"part": "torso", "dx": 0.0, "dy": 1.42, "dz": 0.0, "sx": 0.64, "sy": 0.38, "sz": 0.40, "color": "#f4b895"},
        {"part": "bust_l", "dx": -0.16, "dy": 1.40, "dz": 0.24, "sx": 0.28, "sy": 0.26, "sz": 0.26, "color": "#f472b6"},
        {"part": "bust_r", "dx": 0.16, "dy": 1.40, "dz": 0.24, "sx": 0.28, "sy": 0.26, "sz": 0.26, "color": "#f472b6"},
        {"part": "arm_l", "dx": -0.44, "dy": 1.32, "dz": 0.08, "sx": 0.18, "sy": 0.62, "sz": 0.18, "color": "#f4b895"},
        {"part": "arm_r", "dx": 0.44, "dy": 1.32, "dz": 0.08, "sx": 0.18, "sy": 0.62, "sz": 0.18, "color": "#f4b895"},
        {"part": "head", "dx": 0.0, "dy": 1.82, "dz": 0.0, "sx": 0.44, "sy": 0.44, "sz": 0.44, "color": "#f4b895"},
        {"part": "hair", "dx": 0.0, "dy": 1.98, "dz": -0.06, "sx": 0.52, "sy": 0.36, "sz": 0.48, "color": "#fbbf24"},
        {"part": "ear_l", "dx": -0.16, "dy": 2.28, "dz": 0.0, "sx": 0.12, "sy": 0.38, "sz": 0.10, "color": "#f472b6"},
        {"part": "ear_r", "dx": 0.16, "dy": 2.28, "dz": 0.0, "sx": 0.12, "sy": 0.38, "sz": 0.10, "color": "#f472b6"},
    ],
    "pool_adonis": [
        {"part": "leg_l", "dx": -0.24, "dy": 0.38, "dz": 0.0, "sx": 0.26, "sy": 0.75, "sz": 0.26, "color": "#c67d50"},
        {"part": "leg_r", "dx": 0.24, "dy": 0.38, "dz": 0.0, "sx": 0.26, "sy": 0.75, "sz": 0.26, "color": "#c67d50"},
        {"part": "trunks", "dx": 0.0, "dy": 0.84, "dz": 0.0, "sx": 0.68, "sy": 0.34, "sz": 0.44, "color": "#fbbf24"},
        {"part": "abs", "dx": 0.0, "dy": 1.16, "dz": 0.04, "sx": 0.58, "sy": 0.36, "sz": 0.38, "color": "#c67d50"},
        {"part": "chest", "dx": 0.0, "dy": 1.50, "dz": 0.06, "sx": 0.86, "sy": 0.42, "sz": 0.48, "color": "#c67d50"},
        {"part": "pec_l", "dx": -0.20, "dy": 1.50, "dz": 0.26, "sx": 0.34, "sy": 0.26, "sz": 0.20, "color": "#d97706"},
        {"part": "pec_r", "dx": 0.20, "dy": 1.50, "dz": 0.26, "sx": 0.34, "sy": 0.26, "sz": 0.20, "color": "#d97706"},
        {"part": "bicep_l", "dx": -0.58, "dy": 1.52, "dz": 0.12, "sx": 0.28, "sy": 0.56, "sz": 0.28, "color": "#c67d50"},
        {"part": "bicep_r", "dx": 0.58, "dy": 1.52, "dz": 0.12, "sx": 0.28, "sy": 0.56, "sz": 0.28, "color": "#c67d50"},
        {"part": "head", "dx": 0.0, "dy": 1.92, "dz": 0.0, "sx": 0.44, "sy": 0.44, "sz": 0.44, "color": "#c67d50"},
        {"part": "shades", "dx": 0.0, "dy": 1.96, "dz": 0.24, "sx": 0.42, "sy": 0.14, "sz": 0.10, "color": "#0f172a"},
    ],
    "cupid_bouncer": [
        {"part": "leg_l", "dx": -0.20, "dy": 0.32, "dz": 0.0, "sx": 0.24, "sy": 0.64, "sz": 0.24, "color": "#f4b895"},
        {"part": "leg_r", "dx": 0.20, "dy": 0.32, "dz": 0.0, "sx": 0.24, "sy": 0.64, "sz": 0.24, "color": "#f4b895"},
        {"part": "shorts", "dx": 0.0, "dy": 0.78, "dz": 0.0, "sx": 0.74, "sy": 0.36, "sz": 0.54, "color": "#f472b6"},
        {"part": "belly", "dx": 0.0, "dy": 1.18, "dz": 0.08, "sx": 0.78, "sy": 0.52, "sz": 0.62, "color": "#f4b895"},
        {"part": "wing_l", "dx": -0.48, "dy": 1.38, "dz": -0.28, "sx": 0.42, "sy": 0.50, "sz": 0.12, "color": "#f8fafc"},
        {"part": "wing_r", "dx": 0.48, "dy": 1.38, "dz": -0.28, "sx": 0.42, "sy": 0.50, "sz": 0.12, "color": "#f8fafc"},
        {"part": "head", "dx": 0.0, "dy": 1.68, "dz": 0.0, "sx": 0.46, "sy": 0.46, "sz": 0.46, "color": "#f4b895"},
        {"part": "halo", "dx": 0.0, "dy": 2.04, "dz": 0.0, "sx": 0.48, "sy": 0.10, "sz": 0.48, "color": "#fbbf24"},
    ],
    "cyber_siren": [
        {"part": "leg_l", "dx": -0.20, "dy": 0.36, "dz": 0.0, "sx": 0.22, "sy": 0.72, "sz": 0.22, "color": "#06b6d4"},
        {"part": "leg_r", "dx": 0.20, "dy": 0.36, "dz": 0.0, "sx": 0.22, "sy": 0.72, "sz": 0.22, "color": "#06b6d4"},
        {"part": "hips", "dx": 0.0, "dy": 0.84, "dz": 0.0, "sx": 0.70, "sy": 0.36, "sz": 0.46, "color": "#d946ef"},
        {"part": "torso", "dx": 0.0, "dy": 1.32, "dz": 0.0, "sx": 0.60, "sy": 0.52, "sz": 0.38, "color": "#d946ef"},
        {"part": "bust_l", "dx": -0.15, "dy": 1.38, "dz": 0.24, "sx": 0.26, "sy": 0.24, "sz": 0.24, "color": "#06b6d4"},
        {"part": "bust_r", "dx": 0.15, "dy": 1.38, "dz": 0.24, "sx": 0.26, "sy": 0.24, "sz": 0.24, "color": "#06b6d4"},
        {"part": "head", "dx": 0.0, "dy": 1.80, "dz": 0.0, "sx": 0.42, "sy": 0.42, "sz": 0.42, "color": "#f4b895"},
        {"part": "visor", "dx": 0.0, "dy": 1.84, "dz": 0.22, "sx": 0.40, "sy": 0.14, "sz": 0.10, "color": "#06b6d4"},
    ],
}


def _parse_npcs_from_prompt(p_low: str, weather_id: str) -> list[dict[str, Any]]:
    npcs: list[dict[str, Any]] = []
    roles = [
        (
            "velvet_diva",
            "Леди Вельвет (3D Кабаре-Дива)",
            "#e11d48",
            ("вельвет", "дива", "кабаре", "интим", "шуточн", "красотк", "модел", "бикини", "корсет", "velvet", "diva", "intimate"),
            "Леди Вельвет: «Ой, Босс, мои воксельные формы прошли строгий 3D-аудит! Запрыгивай в джакузи!» ♥",
        ),
        (
            "pool_adonis",
            "Мачо Адонис (3D Пляжный Атлет)",
            "#f59e0b",
            ("адонис", "мачо", "атлет", "пляж", "качок", "бодибилдер", "интим", "шуточн", "adonis", "hunk"),
            "Мачо Адонис: «Смотри на эти кубики пресса — чистый 100% воксельный протеин без единого полигона!» ♥",
        ),
        (
            "cupid_bouncer",
            "Купидон-Вышибала VIP",
            "#f472b6",
            ("купидон", "амур", "романт", "сердц", "любов", "вышибал", "cupid", "woohoo"),
            "Купидон-Вышибала: «Фейс-контроль пройден! В нашем VIP-будуаре сегодня только шуточный флирт и горячие пиксели!» ♥",
        ),
        (
            "cyber_siren",
            "Кибер-Сирена Неон",
            "#d946ef",
            ("сирен", "неон", "танцовщ", "клуб", "тусовк", "siren", "dancer"),
            "Кибер-Сирена: «Неоновый ритм 128 BPM! Даже видеокарта краснеет от наших танцев!» ♥",
        ),
        ("merchant", "Торговец Борис", "#f59e0b", ("торгов", "купц", "купец", "магаз", "merchant", "trader"), ""),
        ("guard", "Стражник Гром", "#3b82f6", ("страж", "охран", "рыцар", "воин", "солдат", "guard", "knight"), ""),
        ("mage", "Архимаг Эфир", "#8b5cf6", ("маг", "волшеб", "мудрец", "учен", "mage", "wizard"), ""),
        ("miner", "Шахтёр Рудольф", "#ef4444", ("шахтер", "шахтёр", "кузнец", "строител", "miner", "builder"), ""),
        ("villager", "Жительница Аня", "#10b981", ("человек", "жител", "персонаж", "нпс", "npc", "друг", "путник", "villager", "person"), ""),
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

    seen_roles: set[str] = set()
    for role_id, name_ru, color, keywords, custom_line in roles:
        if role_id not in seen_roles and any(k in p_low for k in keywords):
            seen_roles.add(role_id)
            npcs.append(
                {
                    "role": role_id,
                    "name": name_ru,
                    "color": color,
                    "rig": COMEDY_3D_RIGS.get(role_id, COMEDY_3D_RIGS["velvet_diva"]),
                    "dialogue": custom_line or f"{name_ru}: «{weather_comment} Чем помочь по хозяйству?»",
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
            "rig": npc.get("rig", COMEDY_3D_RIGS["velvet_diva"]),
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
        "total_blocks": len(blocks_list := list(world["blocks"].values())),
        "total_npcs": len(world["npcs"]),
    }
    world["director_history"].append(event)

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
    """Генерирует GDScript для Godot 4.7.2 с поддержкой вокселей, Режиссёра, погоды, камер, 3D-персонажей и звука."""
    return '''extends Node3D

const SAVE_FILE := "res://bossblocks_world.json"
const WORLD_EDGE := 10
const BLOCK_NAMES := [
	"Grass", "Sand", "Stone", "Wood", "Brick", "Water", "GlowCrystal", "Leaves",
	"VelvetRed", "SkinPeach", "SkinBronze", "NeonMagenta", "GoldTrim", "SilkPink"
]
const BLOCK_COLORS := [
	Color("69aa56"), Color("d8bc79"), Color("858f9e"), Color("8b5a2b"),
	Color("b55239"), Color("3b82f6"), Color("f59e0b"), Color("2e7d32"),
	Color("e11d48"), Color("f4b895"), Color("c67d50"), Color("d946ef"),
	Color("fbbf24"), Color("f472b6")
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
var last_dialogue := "Нажмите T для текстовой команды Режиссёру (погода, камера, постройки, 3D-персонажи)"


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
		material.roughness = 0.78
		if i == 6 or i == 11 or i == 12:
			material.emission_enabled = true
			material.emission = BLOCK_COLORS[i]
			material.emission_energy_multiplier = 1.4
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
	director_bar.placeholder_text = "Режиссёр мира (введите команду: ночь / дрон / джакузи / кровать-сердце / леди вельвет / мачо адонис)..."
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


func _add_rig_part(parent: Node3D, offset: Vector3, size: Vector3, color_hex: String) -> void:
	var part := MeshInstance3D.new()
	if size.y > size.x * 1.35:
		var cap := CapsuleMesh.new()
		cap.radius = max(0.06, (size.x + size.z) * 0.26)
		cap.height = max(cap.radius * 2.1, size.y * 1.08)
		cap.radial_segments = 48
		cap.rings = 24
		part.mesh = cap
	else:
		var sph := SphereMesh.new()
		sph.radius = 0.5
		sph.height = 1.0
		sph.radial_segments = 48
		sph.rings = 32
		part.mesh = sph
		part.scale = size * 1.12
	part.position = offset
	var mat := StandardMaterial3D.new()
	mat.albedo_color = Color(color_hex.trim_prefix("#"))
	mat.roughness = 0.28
	mat.metallic_specular = 0.72
	mat.rim_enabled = true
	mat.rim = 0.45
	part.material_override = mat
	parent.add_child(part)


func spawn_npc(npc_name: String, role: String, pos: Vector3, dialogue: String) -> Node3D:
	var npc := Node3D.new()
	npc.name = "NPC_%s" % role
	npc.position = pos
	var skin := "#f4b895" if role != "pool_adonis" else "#c67d50"
	var outfit := "#e11d48" if role == "velvet_diva" else ("#3b82f6" if role == "pool_adonis" else "#f472b6")
	_add_rig_part(npc, Vector3(-0.18, 0.45, 0.0), Vector3(0.22, 0.85, 0.22), skin)
	_add_rig_part(npc, Vector3(0.18, 0.45, 0.0), Vector3(0.22, 0.85, 0.22), skin)
	_add_rig_part(npc, Vector3(0.0, 0.92, 0.0), Vector3(0.62, 0.30, 0.40), outfit)
	_add_rig_part(npc, Vector3(-0.16, 0.90, -0.22), Vector3(0.26, 0.28, 0.24), outfit)
	_add_rig_part(npc, Vector3(0.16, 0.90, -0.22), Vector3(0.26, 0.28, 0.24), outfit)
	_add_rig_part(npc, Vector3(0.0, 1.38, 0.0), Vector3(0.56, 0.56, 0.32), outfit if role == "velvet_diva" else skin)
	_add_rig_part(npc, Vector3(-0.16, 1.48, 0.24), Vector3(0.28, 0.28, 0.28), skin)
	_add_rig_part(npc, Vector3(0.16, 1.48, 0.24), Vector3(0.28, 0.28, 0.28), skin)
	_add_rig_part(npc, Vector3(-0.42, 1.35, 0.0), Vector3(0.18, 0.68, 0.18), skin)
	_add_rig_part(npc, Vector3(0.42, 1.35, 0.0), Vector3(0.18, 0.68, 0.18), skin)
	_add_rig_part(npc, Vector3(0.0, 1.92, 0.0), Vector3(0.44, 0.44, 0.44), skin)
	_add_rig_part(npc, Vector3(0.0, 2.18, -0.05), Vector3(0.50, 0.24, 0.50), "#fbbf24")
	if role == "velvet_diva":
		_add_rig_part(npc, Vector3(-0.15, 2.46, 0.0), Vector3(0.12, 0.42, 0.10), "#f472b6")
		_add_rig_part(npc, Vector3(0.15, 2.46, 0.0), Vector3(0.12, 0.42, 0.10), "#f472b6")
	var label := Label3D.new()
	label.text = dialogue
	label.position = Vector3(0, 2.8, 0)
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
	if p.find("джакуз") != -1 or p.find("hot_tub") != -1:
		for dx in range(-2, 3):
			for dz in range(-2, 3):
				_add_block(Vector3i(4 + dx, 0, 4 + dz), 6 if abs(dx) < 2 and abs(dz) < 2 else 13)
		applied.append("structure:hot_tub")
	if p.find("кроват") != -1 or p.find("сердц") != -1 or p.find("будуар") != -1:
		for dx in range(-2, 3):
			for dz in range(-2, 3):
				_add_block(Vector3i(-4 + dx, 0, 4 + dz), 9)
		applied.append("structure:heart_bed")
	if p.find("кабаре") != -1 or p.find("неон") != -1:
		for y in range(0, 5):
			_add_block(Vector3i(0, y, 5), 13)
		applied.append("structure:neon_cabaret")

	if p.find("страж") != -1 or p.find("торгов") != -1 or p.find("человек") != -1 or p.find("npc") != -1 or p.find("интим") != -1 or p.find("вельвет") != -1 or p.find("адонис") != -1 or p.find("модел") != -1:
		spawn_npc("Леди Вельвет (3D Кабаре-Дива)", "velvet_diva", Vector3(2.5, 0.0, -2.5), "Леди Вельвет: «Добро пожаловать в VIP-кабаре BossBlocks! ♥»")
		applied.append("npc:velvet_diva")

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
    """Экспортирует 3D-мир и Режиссёра в проект Godot 4.7.2 и (опционально) проверяет его реальным Godot."""
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
    """Возвращает интерактивную 3D-игру с гладкими органическими PBR 3D-моделями персонажей (без квадратиков!)."""
    if world_id not in _WORLD_STORE:
        apply_voxel_director_command(
            VoxelDirectorRequest(
                world_id=world_id,
                prompt="Смени погоду на ночь, построй джакузи, кровать-сердце и неоновое кабаре, добавь шуточные интим 3D модели: Леди Вельвет, Мачо Адонис, Кибер-Сирена и Купидон-Вышибала",
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
<title>BossBlocks Genie-Local — Гладкие 3D-Модели Кабаре & Будуар</title>
<style>
  :root {{
    color-scheme: dark;
    --bg: #090614;
    --panel: rgba(15, 12, 32, 0.84);
    --border: rgba(244, 114, 182, 0.34);
    --accent: #f472b6;
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
    cursor: grab;
  }}
  #viewport:active {{ cursor: grabbing; }}
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
    backdrop-filter: blur(14px);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 12px 16px;
    pointer-events: auto;
    max-width: 680px;
    box-shadow: 0 12px 34px rgba(0,0,0,0.55);
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
    color: #e2e8f0;
    line-height: 1.45;
  }}
  .dialogue-box {{
    margin-top: 8px;
    padding: 8px 10px;
    border-radius: 8px;
    background: rgba(244, 114, 182, 0.14);
    border-left: 3px solid var(--accent);
    font-size: 13px;
    color: #fff;
  }}
  .director-dock {{
    position: fixed;
    bottom: 16px;
    left: 50%;
    transform: translateX(-50%);
    width: min(960px, calc(100vw - 28px));
    background: var(--panel);
    backdrop-filter: blur(16px);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 12px 14px;
    display: flex;
    flex-direction: column;
    gap: 8px;
    box-shadow: 0 14px 40px rgba(0,0,0,0.65);
  }}
  .director-row {{ display: flex; gap: 8px; }}
  #director-input {{
    flex: 1;
    background: rgba(9, 6, 20, 0.85);
    color: var(--text);
    border: 1px solid var(--border);
    border-radius: 10px;
    padding: 10px 14px;
    font-size: 14px;
    outline: none;
  }}
  #director-input:focus {{ border-color: #38bdf8; }}
  .btn {{
    background: linear-gradient(135deg, #ec4899, #8b5cf6);
    color: #fff;
    border: none;
    border-radius: 10px;
    padding: 9px 16px;
    font-weight: 600;
    font-size: 13px;
    cursor: pointer;
    transition: transform 150ms ease, filter 150ms ease;
  }}
  .btn:hover {{ filter: brightness(1.14); }}
  .btn:active {{ transform: scale(0.97); }}
  .chip-row {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .chip {{
    background: rgba(30, 20, 54, 0.92);
    color: #f8fafc;
    border: 1px solid var(--border);
    border-radius: 999px;
    padding: 5px 12px;
    font-size: 12px;
    cursor: pointer;
    transition: background 140ms ease, transform 140ms ease;
  }}
  .chip:hover {{ background: rgba(244, 114, 182, 0.28); }}
  .chip:active {{ transform: scale(0.96); }}
</style>
</head>
<body>
<canvas id="viewport"></canvas>

<div class="hud-top">
  <div class="hud-card">
    <div class="hud-title">BossBlocks Genie-Local · Гладкие 3D-Модели & VIP-Будуар (Subsurface 3D Shader)</div>
    <div class="hud-meta" id="hud-stats">Загрузка 3D-сцены...</div>
    <div class="dialogue-box" id="hud-dialogue">Инициализация...</div>
  </div>
  <div class="hud-card">
    <div class="hud-title">Управление 3D-камерой и сценой</div>
    <div class="hud-meta">
      Мышь (Зажать и крутить) — 3D-орбита вокруг моделей · Колесо — зум<br>
      WASD / Стрелки — перемещение камеры · Клик по чипам — смена поз и света<br>
      Рендер: Гладкие 3D-эллипсоиды и капсулы с Subsurface Skin & Satin Specular (0 квадратиков!)
    </div>
  </div>
</div>

<div class="director-dock">
  <div class="director-row">
    <input id="director-input" type="text" placeholder="Команда Режиссёру: добавь Леди Вельвет и Мачо Адониса у джакузи, включи закат или неоновую ночь..." />
    <button class="btn" id="director-send">Применить к 3D-сцене</button>
    <button class="btn" id="pose-toggle" style="background:linear-gradient(135deg,#f59e0b,#ec4899)">Танец / Поза ♥</button>
    <button class="btn" id="audio-toggle" style="background:linear-gradient(135deg,#059669,#10b981)">Вкл. звук</button>
  </div>
  <div class="chip-row">
    <button class="chip" data-cmd="Добавь шуточные интим 3D модели: Леди Вельвет, Мачо Адонис, Кибер-Сирена и Купидон-Вышибала">💃 Все 4 гладкие 3D-модели крупным планом</button>
    <button class="chip" data-cmd="Построй кровать-сердце, джакузи, неоновое кабаре и статую Венеры">🛁 VIP-Джакузи + Бархатная Кровать-Сердце</button>
    <button class="chip" data-cmd="Смени погоду на ночь и переставь камеру на дрон-облёт">🌙 Неоновая Ночь + 3D-Орбита</button>
    <button class="chip" data-cmd="Смени погоду на закат и переставь камеру от третьего лица">🌅 Романтический Закат (Крупный план)</button>
    <button class="chip" data-cmd="Переставь камеру на вид сверху">🎥 Камера: Вид сверху</button>
    <button class="chip" data-cmd="Смени погоду на грозу и шторм">⚡ Погода: Неоновый дождь</button>
  </div>
</div>

<script>
const STATE = {state_json};
const canvas = document.getElementById('viewport');
const ctx = canvas.getContext('2d');
let yaw = 0.18, pitch = -0.18, camDist = 7.4;
let targetX = 0.0, targetY = 1.35, targetZ = 0.0;
let danceSpeed = 1.0;
let audioCtx = null, audioEnabled = false, tTick = 0;

function resize() {{
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}}
window.addEventListener('resize', resize);
resize();

function updateHud() {{
  const w = STATE.weather || {{}};
  const c = STATE.camera || {{}};
  const npcs = STATE.npcs || [];
  document.getElementById('hud-stats').textContent =
    `Мир: ${{STATE.world_id}} | Атмосфера: ${{w.title_ru || 'Неоновый будуар'}} | Камера: ${{c.title_ru || 'Крупный 3D-план'}} | Гладких 3D-моделей: ${{Math.max(4, npcs.length)}}`;
  const dlg = (STATE.soundscape && STATE.soundscape.dialogue_tts) || 'Леди Вельвет: «Добро пожаловать в гладкий 3D VIP-будуар! Никаких квадратиков ♥»';
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
  const scale = (STATE.soundscape && STATE.soundscape.music_scale_hz) || [261.63, 329.63, 392.0, 493.88];
  const freq = scale[Math.floor(Math.random() * scale.length)];
  const osc = audioCtx.createOscillator();
  const gain = audioCtx.createGain();
  osc.type = 'sine';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(0.05, audioCtx.currentTime);
  gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + 0.85);
  osc.connect(gain).connect(audioCtx.destination);
  osc.start();
  osc.stop(audioCtx.currentTime + 0.88);
}}

function getCamPos() {{
  const cx = targetX + Math.sin(yaw) * Math.cos(pitch) * camDist;
  const cy = targetY - Math.sin(pitch) * camDist;
  const cz = targetZ + Math.cos(yaw) * Math.cos(pitch) * camDist;
  return {{ cx, cy: Math.max(0.5, cy), cz }};
}}

function project3D(x, y, z) {{
  const {{ cx, cy, cz }} = getCamPos();
  const fx = targetX - cx, fy = targetY - cy, fz = targetZ - cz;
  const fl = Math.hypot(fx, fy, fz) || 1;
  const fwdX = fx / fl, fwdY = fy / fl, fwdZ = fz / fl;
  let rx = -fwdZ, ry = 0, rz = fwdX;
  const rl = Math.hypot(rx, rz) || 1;
  rx /= rl; rz /= rl;
  const ux = ry * fwdZ - rz * fwdY;
  const uy = rz * fwdX - rx * fwdZ;
  const uz = rx * fwdY - ry * fwdX;

  const vx = x - cx, vy = y - cy, vz = z - cz;
  const depth = vx * fwdX + vy * fwdY + vz * fwdZ;
  if (depth <= 0.3) return null;
  const px = vx * rx + vy * ry + vz * rz;
  const py = vx * ux + vy * uy + vz * uz;
  const fov = Math.min(canvas.width, canvas.height) * 0.92;
  return {{
    sx: canvas.width * 0.5 + (px / depth) * fov,
    sy: canvas.height * 0.54 - (py / depth) * fov,
    depth,
    scale: fov / depth
  }};
}}

function shadeColor(hex, mult, addR = 0, addG = 0, addB = 0) {{
  const h = (hex || '#f4b895').replace('#', '');
  const r = Math.min(255, Math.max(0, Math.round(parseInt(h.slice(0,2), 16) * mult + addR)));
  const g = Math.min(255, Math.max(0, Math.round(parseInt(h.slice(2,4), 16) * mult + addG)));
  const b = Math.min(255, Math.max(0, Math.round(parseInt(h.slice(4,6), 16) * mult + addB)));
  return `rgb(${{r}},${{g}},${{b}})`;
}}

function drawSmooth3DEllipsoid(sx, sy, rx, ry, rotRad, baseHex, sheen = 0.55, rimHex = '#f472b6') {{
  ctx.save();
  ctx.translate(sx, sy);
  ctx.rotate(rotRad);
  ctx.beginPath();
  ctx.ellipse(0, 0, Math.max(1.5, rx), Math.max(1.5, ry), 0, 0, Math.PI * 2);

  const grad = ctx.createRadialGradient(-rx * 0.32, -ry * 0.35, Math.max(1, rx * 0.08), 0, 0, Math.max(rx, ry) * 1.08);
  grad.addColorStop(0.0, shadeColor(baseHex, 1.32, Math.round(75 * sheen), Math.round(55 * sheen), Math.round(65 * sheen)));
  grad.addColorStop(0.38, shadeColor(baseHex, 1.06, 12, 4, 8));
  grad.addColorStop(0.78, shadeColor(baseHex, 0.72));
  grad.addColorStop(0.95, shadeColor(baseHex, 0.48));
  grad.addColorStop(1.0, rimHex);

  ctx.fillStyle = grad;
  ctx.fill();
  ctx.restore();
}}

function drawSmooth3DCapsule(p1, p2, r1, r2, baseHex, sheen = 0.52) {{
  const dx = p2.sx - p1.sx, dy = p2.sy - p1.sy;
  const len = Math.hypot(dx, dy);
  if (len < 1) {{
    drawSmooth3DEllipsoid(p1.sx, p1.sy, r1, r1, 0, baseHex, sheen);
    return;
  }}
  const ang = Math.atan2(dy, dx);
  const nx = -Math.sin(ang), ny = Math.cos(ang);
  ctx.save();
  ctx.beginPath();
  ctx.arc(p1.sx, p1.sy, Math.max(1.5, r1), ang + Math.PI * 0.5, ang + Math.PI * 1.5);
  ctx.lineTo(p2.sx - nx * r2, p2.sy - ny * r2);
  ctx.arc(p2.sx, p2.sy, Math.max(1.5, r2), ang - Math.PI * 0.5, ang + Math.PI * 0.5);
  ctx.closePath();

  const mx = (p1.sx + p2.sx) * 0.5, my = (p1.sy + p2.sy) * 0.5;
  const maxR = Math.max(r1, r2, 2);
  const grad = ctx.createLinearGradient(mx - nx * maxR, my - ny * maxR, mx + nx * maxR, my + ny * maxR);
  grad.addColorStop(0.0, shadeColor(baseHex, 0.52));
  grad.addColorStop(0.28, shadeColor(baseHex, 1.04, 10, 4, 8));
  grad.addColorStop(0.56, shadeColor(baseHex, 1.30, Math.round(68 * sheen), Math.round(48 * sheen), Math.round(58 * sheen)));
  grad.addColorStop(0.88, shadeColor(baseHex, 0.72));
  grad.addColorStop(1.0, '#f472b6');

  ctx.fillStyle = grad;
  ctx.fill();
  ctx.restore();
}}

function buildSmoothCharacterPrimitives(charDef, t) {{
  const prims = [];
  const bx = charDef.x, by = charDef.y, bz = charDef.z;
  const phase = charDef.phase || 0;
  const sway = Math.sin(t * 2.6 * danceSpeed + phase) * 0.12;
  const breath = Math.sin(t * 3.8 * danceSpeed + phase) * 0.025;
  const hipShift = Math.sin(t * 2.6 * danceSpeed + phase) * 0.09;
  const skin = charDef.skin;
  const outfit = charDef.outfit;
  const hair = charDef.hair;
  const isFemale = charDef.gender !== 'male';

  const addSphere = (lx, ly, lz, rx, ry, rz, col, sheen = 0.55, tag = '') => {{
    const p = project3D(bx + lx, by + ly, bz + lz);
    if (!p) return;
    prims.push({{
      kind: 'ellipsoid',
      depth: p.depth - (rz * 0.35),
      sx: p.sx,
      sy: p.sy,
      rx: p.scale * rx,
      ry: p.scale * ry,
      rot: sway * 0.25,
      col,
      sheen,
      tag
    }});
  }};

  const addLimb = (x1, y1, z1, x2, y2, z2, r1, r2, col, sheen = 0.48) => {{
    const p1 = project3D(bx + x1, by + y1, bz + z1);
    const p2 = project3D(bx + x2, by + y2, bz + z2);
    if (!p1 || !p2) return;
    prims.push({{
      kind: 'capsule',
      depth: (p1.depth + p2.depth) * 0.5,
      p1,
      p2,
      r1: p1.scale * r1,
      r2: p2.scale * r2,
      col,
      sheen
    }});
  }};

  // Гладкие ступни / каблучки, голени и округлые бёдра
  addSphere(-0.19, 0.08, 0.08, 0.09, 0.07, 0.14, isFemale ? outfit : '#1e293b', 0.85);
  addSphere(0.19, 0.08, 0.08, 0.09, 0.07, 0.14, isFemale ? outfit : '#1e293b', 0.85);
  addLimb(-0.19, 0.10, 0.0, -0.20 + hipShift * 0.3, 0.56, 0.02, 0.075, 0.105, skin, 0.52);
  addLimb(0.19, 0.10, 0.0, 0.20 + hipShift * 0.3, 0.56, 0.02, 0.075, 0.105, skin, 0.52);
  addLimb(-0.20 + hipShift * 0.3, 0.56, 0.02, -0.18 + hipShift, 1.04, 0.0, 0.11, isFemale ? 0.165 : 0.155, skin, 0.58);
  addLimb(0.20 + hipShift * 0.3, 0.56, 0.02, 0.18 + hipShift, 1.04, 0.0, 0.11, isFemale ? 0.165 : 0.155, skin, 0.58);

  // Округлый таз, бёдра и ягодичный рельеф (песочные часы / атлет)
  const hipW = isFemale ? 0.31 : 0.25;
  addSphere(hipShift, 1.06, 0.0, hipW, 0.20, 0.22, outfit, 0.78);
  addSphere(-0.13 + hipShift, 1.03, -0.12, isFemale ? 0.16 : 0.13, 0.16, 0.16, outfit, 0.82);
  addSphere(0.13 + hipShift, 1.03, -0.12, isFemale ? 0.16 : 0.13, 0.16, 0.16, outfit, 0.82);

  // Осиная талия и гладкий скульптурный торс
  const waistW = isFemale ? 0.185 : 0.23;
  addSphere(hipShift * 0.5, 1.28, 0.0, waistW, 0.19, 0.16, isFemale && charDef.corset ? outfit : skin, 0.62);
  const chestW = isFemale ? 0.25 : 0.32;
  addSphere(sway * 0.4, 1.54 + breath, 0.0, chestW, 0.24, 0.19, isFemale && charDef.corset ? outfit : skin, 0.68);

  // Рельеф груди / грудных мышц с мягким анатомическим бликом
  if (isFemale) {{
    addSphere(-0.125 + sway * 0.4, 1.56 + breath * 1.3, 0.15, 0.135, 0.13, 0.14, skin, 0.76);
    addSphere(0.125 + sway * 0.4, 1.56 + breath * 1.3, 0.15, 0.135, 0.13, 0.14, skin, 0.76);
    addSphere(-0.125 + sway * 0.4, 1.53 + breath * 1.3, 0.17, 0.138, 0.105, 0.14, outfit, 0.88);
    addSphere(0.125 + sway * 0.4, 1.53 + breath * 1.3, 0.17, 0.138, 0.105, 0.14, outfit, 0.88);
  }} else {{
    addSphere(-0.13 + sway * 0.4, 1.58 + breath, 0.12, 0.14, 0.11, 0.10, skin, 0.72);
    addSphere(0.13 + sway * 0.4, 1.58 + breath, 0.12, 0.14, 0.11, 0.10, skin, 0.72);
    // Кубики пресса и золотая цепь
    for (let row = 0; row < 3; row++) {{
      addSphere(-0.055, 1.24 + row * 0.09, 0.14, 0.048, 0.038, 0.04, skin, 0.65);
      addSphere(0.055, 1.24 + row * 0.09, 0.14, 0.048, 0.038, 0.04, skin, 0.65);
    }}
    addSphere(sway * 0.4, 1.72, 0.11, 0.16, 0.04, 0.12, '#fbbf24', 0.95);
  }}

  // Гладкие плечи, предплечья и кисти рук в грациозной позе
  const shX = isFemale ? 0.29 : 0.36;
  const waveArm = Math.sin(t * 3.2 * danceSpeed + phase) * 0.16;
  addLimb(-shX + sway * 0.4, 1.68, 0.0, -shX - 0.14, 1.38 + waveArm * 0.4, 0.08, 0.075, 0.062, skin, 0.55);
  addLimb(-shX - 0.14, 1.38 + waveArm * 0.4, 0.08, -0.24 + hipShift, 1.12, 0.12, 0.06, 0.05, skin, 0.55);
  addLimb(shX + sway * 0.4, 1.68, 0.0, shX + 0.16, 1.48 - waveArm, 0.12, 0.075, 0.062, skin, 0.55);
  addLimb(shX + 0.16, 1.48 - waveArm, 0.12, shX + 0.06, 1.82 - waveArm, 0.18, 0.06, 0.05, skin, 0.55);

  // Шея, скульптурная голова, пышная причёска, глаза и пухлые губы
  addLimb(sway * 0.4, 1.74, 0.0, sway * 0.5, 1.92, 0.0, 0.075, 0.07, skin, 0.55);
  const hx = sway * 0.55, hy = 2.04 + breath * 0.5, hz = 0.02;
  addSphere(hx, hy, hz, 0.175, 0.205, 0.185, skin, 0.62, 'head');

  // Пышные локоны причёски
  addSphere(hx, hy + 0.08, hz - 0.04, 0.205, 0.19, 0.20, hair, 0.78);
  if (isFemale) {{
    addSphere(hx - 0.16, hy - 0.06, hz - 0.02, 0.095, 0.24, 0.12, hair, 0.75);
    addSphere(hx + 0.16, hy - 0.06, hz - 0.02, 0.095, 0.24, 0.12, hair, 0.75);
  }}
  if (charDef.ears) {{
    addLimb(hx - 0.11, hy + 0.18, hz, hx - 0.16, hy + 0.48, hz + 0.04, 0.048, 0.032, '#f472b6', 0.85);
    addLimb(hx + 0.11, hy + 0.18, hz, hx + 0.16, hy + 0.48, hz + 0.04, 0.048, 0.032, '#f472b6', 0.85);
  }}
  if (charDef.wings) {{
    addSphere(sway * 0.4 - 0.28, 1.62, -0.18, 0.24, 0.34, 0.08, '#f8fafc', 0.92);
    addSphere(sway * 0.4 + 0.28, 1.62, -0.18, 0.24, 0.34, 0.08, '#f8fafc', 0.92);
  }}

  // Детали лица (выразительные глаза с бликами и пухлые губы)
  addSphere(hx - 0.062, hy + 0.02, hz + 0.165, 0.032, 0.024, 0.02, '#ffffff', 0.95);
  addSphere(hx + 0.062, hy + 0.02, hz + 0.165, 0.032, 0.024, 0.02, '#ffffff', 0.95);
  addSphere(hx - 0.062, hy + 0.02, hz + 0.182, 0.016, 0.016, 0.01, '#1e1b4b', 0.98);
  addSphere(hx + 0.062, hy + 0.02, hz + 0.182, 0.016, 0.016, 0.01, '#1e1b4b', 0.98);
  addSphere(hx, hy - 0.075, hz + 0.175, 0.048, 0.022, 0.025, '#e11d48', 0.92);

  const labelP = project3D(bx, by + 2.58, bz);
  if (labelP) {{
    prims.push({{
      kind: 'label',
      depth: labelP.depth - 0.4,
      sx: labelP.sx,
      sy: labelP.sy,
      name: charDef.name,
      color: outfit
    }});
  }}
  return prims;
}}

function render() {{
  tTick += 0.016;
  const w = STATE.weather || {{}};
  if (STATE.camera && STATE.camera.orbit) {{
    yaw += 0.0035;
  }}

  // Студийный неоновый фон будуара с мягким свечением и зеркальным полом
  const bgGrad = ctx.createLinearGradient(0, 0, 0, canvas.height);
  if (w.id === 'sunset') {{
    bgGrad.addColorStop(0.0, '#2e1035');
    bgGrad.addColorStop(0.52, '#7c2d52');
    bgGrad.addColorStop(1.0, '#140c24');
  }} else {{
    bgGrad.addColorStop(0.0, '#090616');
    bgGrad.addColorStop(0.52, '#1e113a');
    bgGrad.addColorStop(1.0, '#0b0719');
  }}
  ctx.fillStyle = bgGrad;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  // Мягкие неоновые прожекторы сцены
  const spot1 = ctx.createRadialGradient(canvas.width * 0.28, canvas.height * 0.22, 10, canvas.width * 0.28, canvas.height * 0.42, canvas.width * 0.45);
  spot1.addColorStop(0, 'rgba(244, 114, 182, 0.22)');
  spot1.addColorStop(1, 'rgba(244, 114, 182, 0)');
  ctx.fillStyle = spot1;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const spot2 = ctx.createRadialGradient(canvas.width * 0.72, canvas.height * 0.25, 10, canvas.width * 0.72, canvas.height * 0.45, canvas.width * 0.45);
  spot2.addColorStop(0, 'rgba(56, 189, 248, 0.18)');
  spot2.addColorStop(1, 'rgba(56, 189, 248, 0)');
  ctx.fillStyle = spot2;
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  const prims = [];

  // 1. Круглый подиум кабаре, бурлящее круглое VIP-джакузи и бархатная кровать-сердце (гладкие 3D-формы!)
  const stageP = project3D(0, -0.08, 0);
  if (stageP) {{
    prims.push({{
      kind: 'ellipsoid',
      depth: stageP.depth + 4.0,
      sx: stageP.sx,
      sy: stageP.sy,
      rx: stageP.scale * 4.8,
      ry: stageP.scale * 1.35,
      rot: 0,
      col: '#1e1b4b',
      sheen: 0.45
    }});
  }}
  const tubP = project3D(2.9, 0.25, -1.8);
  if (tubP) {{
    prims.push({{ kind: 'ellipsoid', depth: tubP.depth + 0.5, sx: tubP.sx, sy: tubP.sy, rx: tubP.scale * 1.45, ry: tubP.scale * 0.52, rot: 0, col: '#fbbf24', sheen: 0.85 }});
    prims.push({{ kind: 'ellipsoid', depth: tubP.depth + 0.4, sx: tubP.sx, sy: tubP.sy - tubP.scale * 0.08, rx: tubP.scale * 1.25, ry: tubP.scale * 0.40, rot: 0, col: '#38bdf8', sheen: 0.95 }});
  }}
  const bedP = project3D(-2.9, 0.28, -1.8);
  if (bedP) {{
    prims.push({{ kind: 'ellipsoid', depth: bedP.depth + 0.5, sx: bedP.sx - bedP.scale * 0.42, sy: bedP.sy, rx: bedP.scale * 0.95, ry: bedP.scale * 0.48, rot: -0.25, col: '#e11d48', sheen: 0.78 }});
    prims.push({{ kind: 'ellipsoid', depth: bedP.depth + 0.5, sx: bedP.sx + bedP.scale * 0.42, sy: bedP.sy, rx: bedP.scale * 0.95, ry: bedP.scale * 0.48, rot: 0.25, col: '#e11d48', sheen: 0.78 }});
  }}

  // 2. Четыре гладких скульптурных 3D-персонажа на переднем плане
  const roster = [
    {{ name: 'Леди Вельвет (3D Кабаре-Дива)', x: -1.85, y: 0.0, z: 0.6, skin: '#f6c2a4', outfit: '#e11d48', hair: '#fbbf24', gender: 'female', corset: true, ears: true, phase: 0.0 }},
    {{ name: 'Мачо Адонис (3D Пляжный Атлет)', x: -0.55, y: 0.0, z: 0.85, skin: '#c87d52', outfit: '#0284c7', hair: '#27272a', gender: 'male', corset: false, ears: false, phase: 1.4 }},
    {{ name: 'Кибер-Сирена Неон (3D VIP)', x: 0.75, y: 0.0, z: 0.8, skin: '#f4b895', outfit: '#d946ef', hair: '#38bdf8', gender: 'female', corset: true, ears: false, phase: 2.7 }},
    {{ name: 'Купидон-Вышибала VIP', x: 2.05, y: 0.0, z: 0.45, skin: '#f4b895', outfit: '#f43f5e', hair: '#fde047', gender: 'female', corset: false, ears: false, wings: true, phase: 4.1 }}
  ];

  for (const ch of roster) {{
    prims.push(...buildSmoothCharacterPrimitives(ch, tTick));
  }}

  prims.sort((a, b) => b.depth - a.depth);

  for (const pr of prims) {{
    if (pr.kind === 'ellipsoid') {{
      drawSmooth3DEllipsoid(pr.sx, pr.sy, pr.rx, pr.ry, pr.rot || 0, pr.col, pr.sheen);
    }} else if (pr.kind === 'capsule') {{
      drawSmooth3DCapsule(pr.p1, pr.p2, pr.r1, pr.r2, pr.col, pr.sheen);
    }} else if (pr.kind === 'label') {{
      const tag = `♥ ${{pr.name}}`;
      ctx.font = 'bold 12px Inter, sans-serif';
      const tw = ctx.measureText(tag).width + 16;
      ctx.fillStyle = 'rgba(15, 12, 32, 0.86)';
      ctx.beginPath();
      ctx.roundRect(pr.sx - tw / 2, pr.sy - 18, tw, 22, 8);
      ctx.fill();
      ctx.strokeStyle = pr.color || '#f472b6';
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.fillStyle = '#fff';
      ctx.fillText(tag, pr.sx - tw / 2 + 8, pr.sy - 3);
    }}
  }}

  requestAnimationFrame(render);
}}

async function sendDirectorPrompt(promptText) {{
  if (!promptText.trim()) return;
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
    }}
  }} catch (_e) {{}}
  updateHud();
  playChord();
  if (STATE.soundscape && STATE.soundscape.dialogue_tts) speakLine(STATE.soundscape.dialogue_tts);
}}

document.getElementById('director-send').addEventListener('click', () => {{
  const inp = document.getElementById('director-input');
  sendDirectorPrompt(inp.value);
  inp.value = '';
}});
document.getElementById('director-input').addEventListener('keydown', (e) => {{
  if (e.key === 'Enter') document.getElementById('director-send').click();
}});
document.getElementById('pose-toggle').addEventListener('click', () => {{
  danceSpeed = danceSpeed > 1.5 ? 1.0 : 2.35;
  playChord();
}});
document.querySelectorAll('.chip').forEach((btn) => {{
  btn.addEventListener('click', () => sendDirectorPrompt(btn.dataset.cmd));
}});
document.getElementById('audio-toggle').addEventListener('click', (e) => {{
  if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  audioEnabled = !audioEnabled;
  e.target.textContent = audioEnabled ? 'Звук: ВКЛ' : 'Вкл. звук';
  if (audioEnabled) playChord();
}});

let dragging = false, lastX = 0, lastY = 0;
canvas.addEventListener('mousedown', (e) => {{ dragging = true; lastX = e.clientX; lastY = e.clientY; }});
window.addEventListener('mouseup', () => {{ dragging = false; }});
window.addEventListener('mousemove', (e) => {{
  if (!dragging) return;
  yaw -= (e.clientX - lastX) * 0.006;
  pitch = Math.max(-0.85, Math.min(0.45, pitch - (e.clientY - lastY) * 0.005));
  lastX = e.clientX; lastY = e.clientY;
}});
canvas.addEventListener('wheel', (e) => {{
  camDist = Math.max(4.2, Math.min(13.5, camDist + e.deltaY * 0.004));
}});

updateHud();
render();
</script>
</body>
</html>"""


def _hex_to_rgb(hex_str: str, brighten: float = 1.0) -> tuple[int, int, int]:
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return (244, 184, 149)
    r = min(255, max(0, int(int(h[0:2], 16) * brighten)))
    g = min(255, max(0, int(int(h[2:4], 16) * brighten)))
    b = min(255, max(0, int(int(h[4:6], 16) * brighten)))
    return (r, g, b)


def render_voxel_scene_screenshot_png(
    state: dict[str, Any],
    out_path: str | Path,
    *,
    wave_label: str = "BossBlocks 3D Studio Render",
    agent_summary: str = "",
    camera_yaw: float = 0.14,
    camera_pitch: float = -0.14,
) -> Path:
    """Рендерит 1280x720 3D-сцену с бесшовными анатомическими 3D-моделями (Continuous Tapered Capsules + Smooth Skin SSS + PBR, 0 квадратиков и 0 колец!)."""
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    target = Path(out_path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    W, H = 1280, 720
    weather = state.get("weather") or {}
    w_id = str(weather.get("id") or "night")

    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    ny_scr = yy / float(H)
    nx_scr = xx / float(W)

    # 1. Кинематографический фон VIP-будуара с неоновым объёмным светом и зеркальным полом
    if w_id == "sunset":
        top_c = np.array([44.0, 12.0, 54.0], dtype=np.float32)
        mid_c = np.array([132.0, 38.0, 82.0], dtype=np.float32)
        bot_c = np.array([18.0, 10.0, 34.0], dtype=np.float32)
    else:
        top_c = np.array([10.0, 7.0, 24.0], dtype=np.float32)
        mid_c = np.array([38.0, 16.0, 68.0], dtype=np.float32)
        bot_c = np.array([12.0, 8.0, 26.0], dtype=np.float32)

    horizon = 0.68
    above = ny_scr <= horizon
    t_above = np.clip(ny_scr / horizon, 0.0, 1.0)[..., None]
    t_below = np.clip((ny_scr - horizon) / (1.0 - horizon), 0.0, 1.0)[..., None]
    img_buf = np.where(
        above[..., None],
        top_c * (1.0 - t_above) + mid_c * t_above,
        mid_c * 0.48 * (1.0 - t_below) + bot_c * t_below,
    )

    # Неоновые студийные софтбоксы (розовый, голубой и золотой контровой свет)
    d_pink = (nx_scr - 0.24) ** 2 + (ny_scr - 0.42) ** 2
    d_cyan = (nx_scr - 0.76) ** 2 + (ny_scr - 0.42) ** 2
    d_gold = (nx_scr - 0.50) ** 2 + (ny_scr - 0.34) ** 2
    img_buf += np.exp(-d_pink * 10.0)[..., None] * np.array([110.0, 26.0, 78.0], dtype=np.float32)
    img_buf += np.exp(-d_cyan * 10.0)[..., None] * np.array([22.0, 84.0, 120.0], dtype=np.float32)
    img_buf += np.exp(-d_gold * 12.0)[..., None] * np.array([78.0, 52.0, 20.0], dtype=np.float32)

    # Гладкий круглый подиум кабаре на полу с двойным неоновым кольцом
    stage_d = ((nx_scr - 0.50) / 0.45) ** 2 + ((ny_scr - 0.84) / 0.12) ** 2
    on_stage = stage_d <= 1.0
    stage_rim = (stage_d >= 0.90) & (stage_d <= 1.02)
    img_buf[on_stage] = img_buf[on_stage] * 0.42 + np.array([32.0, 20.0, 58.0], dtype=np.float32)
    img_buf[stage_rim] = np.array([244.0, 114.0, 182.0], dtype=np.float32)

    # Камера откалибрована так, чтобы все 4 персонажа в полный рост (от туфелек до макушки/ушек) идеально помещались под HUD
    yaw_shift = (camera_yaw - 0.35) * 0.35
    cam_pos = np.array([yaw_shift, 1.32, 6.35], dtype=np.float32)
    cam_target = np.array([0.0, 1.18, 0.0], dtype=np.float32)
    fwd = cam_target - cam_pos
    fwd /= np.linalg.norm(fwd)
    right = np.array([-fwd[2], 0.0, fwd[0]], dtype=np.float32)
    right /= np.linalg.norm(right)
    up = np.cross(right, fwd)
    fov = 1040.0

    z_buffer = np.full((H, W), 1e9, dtype=np.float32)

    L_key = np.array([0.36, 0.52, 0.77], dtype=np.float32)
    L_key /= np.linalg.norm(L_key)
    V_dir = np.array([0.0, 0.06, 0.998], dtype=np.float32)
    H_vec = L_key + V_dir
    H_vec /= np.linalg.norm(H_vec)

    def project_3d(x: float, y: float, z: float) -> tuple[float, float, float, float]:
        v = np.array([x, y, z], dtype=np.float32) - cam_pos
        depth = max(0.4, float(np.dot(v, fwd)))
        px = float(np.dot(v, right))
        py = float(np.dot(v, up))
        sx = W * 0.5 + (px / depth) * fov
        sy = H * 0.59 - (py / depth) * fov
        return sx, sy, depth, fov / depth

    def shade_surface(
        nx: np.ndarray,
        ny: np.ndarray,
        nz: np.ndarray,
        rgb: tuple[int, int, int],
        material: str,
    ) -> np.ndarray:
        ndotl = np.maximum(0.0, nx * L_key[0] + ny * L_key[1] + nz * L_key[2])
        wrap = np.clip((nx * L_key[0] + ny * L_key[1] + nz * L_key[2] + 0.48) / 1.48, 0.0, 1.0)
        ndoth = np.maximum(0.0, nx * H_vec[0] + ny * H_vec[1] + nz * H_vec[2])
        fresnel = np.power(np.clip(1.0 - nz, 0.0, 1.0), 2.8)

        base_c = np.array(rgb, dtype=np.float32)[None, None, :]
        if material == "skin":
            sss_tint = np.array([255.0, 112.0, 92.0], dtype=np.float32)[None, None, :]
            rim_tint = np.array([244.0, 114.0, 182.0], dtype=np.float32)[None, None, :]
            spec = np.power(ndoth, 26.0) * 52.0
            return (
                base_c * (0.32 + 0.56 * wrap[..., None] + 0.20 * ndotl[..., None])
                + sss_tint * (0.15 * np.clip(1.0 - ndotl, 0.0, 1.0)[..., None])
                + spec[..., None]
                + rim_tint * (fresnel[..., None] * 0.25)
            )
        if material == "satin":
            rim_tint = np.array([255.0, 185.0, 225.0], dtype=np.float32)[None, None, :]
            spec = np.power(ndoth, 40.0) * 128.0
            return (
                base_c * (0.26 + 0.76 * ndotl[..., None])
                + spec[..., None]
                + rim_tint * (fresnel[..., None] * 0.38)
            )
        if material == "gold":
            spec = np.power(ndoth, 60.0) * 190.0
            return base_c * (0.34 + 0.82 * ndotl[..., None]) + spec[..., None]
        if material == "water":
            spec = np.power(ndoth, 75.0) * 205.0
            return base_c * (0.48 + 0.62 * wrap[..., None]) + spec[..., None]
        spec = np.power(ndoth, 20.0) * 42.0
        return base_c * (0.34 + 0.66 * ndotl[..., None]) + spec[..., None]

    def render_smooth_ellipsoid_3d(
        cx_3d: float,
        cy_3d: float,
        cz_3d: float,
        rx_3d: float,
        ry_3d: float,
        rz_3d: float,
        rgb: tuple[int, int, int],
        *,
        material: str = "skin",
        rot_z: float = 0.0,
        z_bias: float = 0.0,
    ) -> None:
        sx, sy, depth, scale = project_3d(cx_3d, cy_3d, cz_3d)
        srx = max(1.5, rx_3d * scale)
        sry = max(1.5, ry_3d * scale)

        pad = int(max(srx, sry) * 1.15) + 2
        x0, x1 = max(0, int(sx) - pad), min(W, int(sx) + pad + 1)
        y0, y1 = max(0, int(sy) - pad), min(H, int(sy) + pad + 1)
        if x0 >= x1 or y0 >= y1:
            return

        dx = xx[y0:y1, x0:x1] - sx
        dy = -(yy[y0:y1, x0:x1] - sy)
        if abs(rot_z) > 1e-4:
            c_r, s_r = math.cos(rot_z), math.sin(rot_z)
            rdx = dx * c_r + dy * s_r
            rdy = -dx * s_r + dy * c_r
        else:
            rdx, rdy = dx, dy

        u = rdx / srx
        w_coord = rdy / sry
        r2 = u * u + w_coord * w_coord
        mask = r2 <= 1.0
        if not np.any(mask):
            return

        nz = np.sqrt(np.maximum(0.0, 1.0 - r2))
        nx, ny = u, w_coord
        if abs(rot_z) > 1e-4:
            c_r, s_r = math.cos(rot_z), math.sin(rot_z)
            nx, ny = nx * c_r - ny * s_r, nx * s_r + ny * c_r

        n_len = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-6
        nx, ny, nz = nx / n_len, ny / n_len, nz / n_len

        pix_depth = depth - nz * (rz_3d * 0.75) + z_bias
        vis = mask & (pix_depth < z_buffer[y0:y1, x0:x1])
        if not np.any(vis):
            return

        z_buffer[y0:y1, x0:x1][vis] = pix_depth[vis]
        shaded = shade_surface(nx, ny, nz, rgb, material)
        img_buf[y0:y1, x0:x1][vis] = np.clip(shaded[vis], 0.0, 255.0)

    def render_continuous_capsule_3d(
        p1: tuple[float, float, float],
        p2: tuple[float, float, float],
        r1_3d: float,
        r2_3d: float,
        rgb: tuple[int, int, int],
        *,
        material: str = "skin",
        z_bias: float = 0.0,
    ) -> None:
        """Рендерит идеально гладкую бесшовную коническую 3D-капсулу без единого кольца/ступеньки."""
        sx1, sy1, d1, sc1 = project_3d(*p1)
        sx2, sy2, d2, sc2 = project_3d(*p2)
        sr1 = max(1.5, r1_3d * sc1)
        sr2 = max(1.5, r2_3d * sc2)

        pad = int(max(sr1, sr2) * 1.25) + 3
        x0 = max(0, int(min(sx1, sx2)) - pad)
        x1 = min(W, int(max(sx1, sx2)) + pad + 1)
        y0 = max(0, int(min(sy1, sy2)) - pad)
        y1 = min(H, int(max(sy1, sy2)) + pad + 1)
        if x0 >= x1 or y0 >= y1:
            return

        px = xx[y0:y1, x0:x1]
        py = yy[y0:y1, x0:x1]
        vx, vy = sx2 - sx1, sy2 - sy1
        seg_len2 = vx * vx + vy * vy + 1e-6
        t = np.clip(((px - sx1) * vx + (py - sy1) * vy) / seg_len2, 0.0, 1.0)

        cx = sx1 + t * vx
        cy = sy1 + t * vy
        rad = sr1 * (1.0 - t) + sr2 * t
        depth = d1 * (1.0 - t) + d2 * t
        r3d = r1_3d * (1.0 - t) + r2_3d * t

        dx = px - cx
        dy = -(py - cy)
        dist2 = dx * dx + dy * dy
        r2_norm = dist2 / (rad * rad + 1e-6)
        mask = r2_norm <= 1.0
        if not np.any(mask):
            return

        nz = np.sqrt(np.maximum(0.0, 1.0 - r2_norm))
        nx = dx / (rad + 1e-6)
        ny = dy / (rad + 1e-6)
        n_len = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-6
        nx, ny, nz = nx / n_len, ny / n_len, nz / n_len

        pix_depth = depth - nz * (r3d * 0.75) + z_bias
        vis = mask & (pix_depth < z_buffer[y0:y1, x0:x1])
        if not np.any(vis):
            return

        z_buffer[y0:y1, x0:x1][vis] = pix_depth[vis]
        shaded = shade_surface(nx, ny, nz, rgb, material)
        img_buf[y0:y1, x0:x1][vis] = np.clip(shaded[vis], 0.0, 255.0)

    # 2. Гладкая мебель VIP-будуара на заднем плане (Бархатная кровать-сердце, Круглое золотое джакузи и Пилон)
    render_smooth_ellipsoid_3d(-2.75, 0.24, -1.45, 0.95, 0.25, 0.85, (190, 18, 60), material="satin", rot_z=-0.22)
    render_smooth_ellipsoid_3d(-1.95, 0.24, -1.45, 0.95, 0.25, 0.85, (190, 18, 60), material="satin", rot_z=0.22)
    render_smooth_ellipsoid_3d(-2.55, 0.65, -1.85, 0.55, 0.45, 0.22, (225, 29, 72), material="satin", rot_z=-0.26)
    render_smooth_ellipsoid_3d(-2.10, 0.65, -1.85, 0.55, 0.45, 0.22, (225, 29, 72), material="satin", rot_z=0.26)

    render_smooth_ellipsoid_3d(2.45, 0.26, -1.45, 1.28, 0.32, 1.15, (251, 191, 36), material="gold")
    render_smooth_ellipsoid_3d(2.45, 0.38, -1.38, 1.08, 0.18, 0.96, (56, 189, 248), material="water")

    # Идеально гладкий золотой пилон кабаре (без колец!)
    render_continuous_capsule_3d((0.0, -0.1, -1.75), (0.0, 2.85, -1.75), 0.055, 0.055, (251, 191, 36), material="gold")

    # 3. Объёмный 3D SDF-скульптор персонажей с полиномиальным Smooth-Minimum (smin) — как в ZBrush/Blender, 0 швов на суставах!
    def smin(a: np.ndarray, b: np.ndarray, k: float = 0.055) -> np.ndarray:
        h = np.maximum(k - np.abs(a - b), 0.0) / k
        return np.minimum(a, b) - h * h * k * 0.25

    def sd_capsule_grid(
        px: np.ndarray, py: np.ndarray, pz: np.ndarray,
        ax: float, ay: float, az: float,
        bx_p: float, by_p: float, bz_p: float,
        r1: float, r2: float,
    ) -> np.ndarray:
        pax, pay, paz = px - ax, py - ay, pz - az
        bax, bay, baz = bx_p - ax, by_p - ay, bz_p - az
        b2 = bax * bax + bay * bay + baz * baz + 1e-8
        h = np.clip((pax * bax + pay * bay + paz * baz) / b2, 0.0, 1.0)
        r = r1 * (1.0 - h) + r2 * h
        dx = pax - bax * h
        dy = pay - bay * h
        dz = paz - baz * h
        return np.sqrt(dx * dx + dy * dy + dz * dz + 1e-8) - r

    def sd_ellipsoid_grid(
        px: np.ndarray, py: np.ndarray, pz: np.ndarray,
        cx: float, cy: float, cz: float,
        rx: float, ry: float, rz: float,
    ) -> np.ndarray:
        qx = (px - cx) / rx
        qy = (py - cy) / ry
        qz = (pz - cz) / rz
        k0 = np.sqrt(qx * qx + qy * qy + qz * qz + 1e-8)
        k1 = np.sqrt((qx / rx) ** 2 + (qy / ry) ** 2 + (qz / rz) ** 2 + 1e-8)
        return k0 * (k0 - 1.0) / (k1 + 1e-6)

    def render_sculpted_character(
        bx: float,
        by: float,
        bz: float,
        *,
        skin_rgb: tuple[int, int, int],
        outfit_rgb: tuple[int, int, int],
        hair_rgb: tuple[int, int, int],
        eye_rgb: tuple[int, int, int] = (56, 189, 248),
        is_female: bool = True,
        corset: bool = True,
        bunny_ears: bool = False,
        angel_wings: bool = False,
        hip_tilt: float = 0.05,
        arm_wave: float = 0.16,
    ) -> None:
        # Крылья Купидона за спиной (рендерятся позади тела)
        if angel_wings:
            render_smooth_ellipsoid_3d(bx - 0.28, by + 1.56, bz - 0.16, 0.23, 0.32, 0.08, (248, 250, 252), material="satin", rot_z=0.32)
            render_smooth_ellipsoid_3d(bx + 0.28, by + 1.56, bz - 0.16, 0.23, 0.32, 0.08, (248, 250, 252), material="satin", rot_z=-0.32)

        # Задняя масса пышных волос
        if is_female:
            render_smooth_ellipsoid_3d(bx, by + 1.88, bz - 0.10, 0.23, 0.36, 0.14, hair_rgb, material="satin")

        # Определяем экранный бокс персонажа
        sx_top, sy_top, d_center, scale = project_3d(bx, by + 2.48, bz)
        _, sy_bot, _, _ = project_3d(bx, by - 0.05, bz)
        half_w = int(0.66 * scale) + 6
        x0 = max(0, int(sx_top) - half_w)
        x1 = min(W, int(sx_top) + half_w)
        y0 = max(0, int(sy_top) - 6)
        y1 = min(H, int(sy_bot) + 6)
        if x0 >= x1 or y0 >= y1:
            return

        # Локальные координаты (X, Y) каждого пикселя в плоскости персонажа и 40 слоёв по глубине Z
        sx_c, sy_c, _, _ = project_3d(bx, by, bz)
        lx_1d = (np.arange(x0, x1, dtype=np.float32) - sx_c) / scale
        ly_1d = (sy_c - np.arange(y0, y1, dtype=np.float32)) / scale
        NZ = 40
        lz_1d = np.linspace(0.30, -0.26, NZ, dtype=np.float32)  # от переднего края (+0.30) к заднему (-0.26)

        px = lx_1d[None, :, None]
        py = ly_1d[:, None, None]
        pz = lz_1d[None, None, :]

        # 1. Ноги (ступни, икры, колени, бёдра) — плавно сливаются через smin
        d_calf_l = sd_capsule_grid(px, py, pz, -0.155, 0.06, 0.0, -0.165 + hip_tilt * 0.3, 0.54, 0.02, 0.052, 0.078)
        d_calf_r = sd_capsule_grid(px, py, pz, 0.155, 0.06, 0.0, 0.165 + hip_tilt * 0.3, 0.54, 0.02, 0.052, 0.078)
        thigh_r = 0.138 if is_female else 0.128
        d_thigh_l = sd_capsule_grid(px, py, pz, -0.165 + hip_tilt * 0.3, 0.54, 0.02, -0.135 + hip_tilt, 1.02, 0.0, 0.078, thigh_r)
        d_thigh_r = sd_capsule_grid(px, py, pz, 0.165 + hip_tilt * 0.3, 0.54, 0.02, 0.135 + hip_tilt, 1.02, 0.0, 0.078, thigh_r)
        d_legs = np.minimum(smin(d_calf_l, d_thigh_l, 0.045), smin(d_calf_r, d_thigh_r, 0.045))

        # 2. Таз, округлые бёдра, ягодицы, талия и грудная клетка (единый бесшовный силуэт песочных часов / атлета)
        hip_w = 0.245 if is_female else 0.195
        d_pelvis = sd_ellipsoid_grid(px, py, pz, hip_tilt, 1.03, -0.01, hip_w, 0.165, 0.165)
        d_waist = sd_capsule_grid(
            px, py, pz,
            hip_tilt * 0.7, 1.06, 0.0,
            0.0, 1.50, 0.0,
            0.165 if is_female else 0.185,
            0.132 if is_female else 0.195,
        )
        chest_w = 0.195 if is_female else 0.255
        d_ribcage = sd_ellipsoid_grid(px, py, pz, 0.0, 1.52, 0.0, chest_w, 0.195, 0.145)
        d_shoulders = sd_capsule_grid(px, py, pz, -(0.20 if is_female else 0.26), 1.63, 0.0, (0.20 if is_female else 0.26), 1.63, 0.0, 0.068, 0.068)

        d_body = smin(d_legs, d_pelvis, 0.055)
        d_body = smin(d_body, d_waist, 0.075)
        d_body = smin(d_body, d_ribcage, 0.075)
        d_body = smin(d_body, d_shoulders, 0.055)

        # 3. Анатомический рельеф груди / грудных мышц и пресса (бесшовное слияние с торсом!)
        if is_female:
            d_bust_l = sd_ellipsoid_grid(px, py, pz, -0.102, 1.49, 0.105, 0.108, 0.105, 0.115)
            d_bust_r = sd_ellipsoid_grid(px, py, pz, 0.102, 1.49, 0.105, 0.108, 0.105, 0.115)
            d_body = smin(d_body, np.minimum(d_bust_l, d_bust_r), 0.048)
        else:
            d_pec_l = sd_ellipsoid_grid(px, py, pz, -0.105, 1.54, 0.085, 0.118, 0.092, 0.085)
            d_pec_r = sd_ellipsoid_grid(px, py, pz, 0.105, 1.54, 0.085, 0.118, 0.092, 0.085)
            d_body = smin(d_body, np.minimum(d_pec_l, d_pec_r), 0.045)
            for r_i in range(3):
                ab_y = 1.21 + r_i * 0.082
                d_ab_l = sd_ellipsoid_grid(px, py, pz, -0.044, ab_y, 0.11, 0.040, 0.032, 0.038)
                d_ab_r = sd_ellipsoid_grid(px, py, pz, 0.044, ab_y, 0.11, 0.040, 0.032, 0.038)
                d_body = smin(d_body, np.minimum(d_ab_l, d_ab_r), 0.025)

        # 4. Бесшовные руки, локти, предплечья и кисти (вливаются в плечи через smin)
        sh_x = 0.225 if is_female else 0.285
        arm_r1 = 0.058 if is_female else 0.074
        arm_r2 = 0.045 if is_female else 0.056
        d_arm_l1 = sd_capsule_grid(px, py, pz, -sh_x, 1.61, 0.0, -sh_x - 0.13, 1.33, 0.04, arm_r1, arm_r2)
        d_arm_l2 = sd_capsule_grid(px, py, pz, -sh_x - 0.13, 1.33, 0.04, -0.19 + hip_tilt, 1.10, 0.10, arm_r2, 0.038)
        d_arm_r1 = sd_capsule_grid(px, py, pz, sh_x, 1.61, 0.0, sh_x + 0.14, 1.44 + arm_wave * 0.3, 0.06, arm_r1, arm_r2)
        d_arm_r2 = sd_capsule_grid(px, py, pz, sh_x + 0.14, 1.44 + arm_wave * 0.3, 0.06, sh_x + 0.05, 1.76 + arm_wave, 0.12, arm_r2, 0.038)
        d_arms = np.minimum(smin(d_arm_l1, d_arm_l2, 0.035), smin(d_arm_r1, d_arm_r2, 0.035))
        d_body = smin(d_body, d_arms, 0.045)

        # 5. Шея, голова и скульптурный носик (единый гладкий контур)
        d_neck = sd_capsule_grid(px, py, pz, 0.0, 1.64, 0.0, 0.0, 1.85, 0.01, 0.056, 0.052)
        d_head = sd_ellipsoid_grid(px, py, pz, 0.0, 1.97, 0.02, 0.145, 0.178, 0.155)
        d_nose = sd_ellipsoid_grid(px, py, pz, 0.0, 1.955, 0.165, 0.018, 0.026, 0.025)
        d_head_full = smin(d_head, d_nose, 0.022)
        d_body = smin(d_body, smin(d_neck, d_head_full, 0.04), 0.045)

        # Находим переднюю поверхность (минимальный Z в камере, где d_body <= 0)
        inside = d_body <= 0.0
        hit_mask = np.any(inside, axis=2)
        if not np.any(hit_mask):
            return

        first_k = np.argmax(inside, axis=2)
        # Линейная субвоксельная интерполяция точной координаты Z поверхности
        k_prev = np.maximum(0, first_k - 1)
        idx_y, idx_x = np.indices(first_k.shape)
        d0 = d_body[idx_y, idx_x, k_prev]
        d1 = d_body[idx_y, idx_x, first_k]
        frac = np.where(first_k > 0, np.clip(d0 / (d0 - d1 + 1e-6), 0.0, 1.0), 0.0)
        z_surf = lz_1d[k_prev] + frac * (lz_1d[first_k] - lz_1d[k_prev])

        # 3D-нормали поверхности из градиента z_surf(x, y) (абсолютно гладкие по всей фигуре!)
        dz_dy, dz_dx = np.gradient(z_surf, -(ly_1d[1] - ly_1d[0]), lx_1d[1] - lx_1d[0])
        nx = np.clip(-dz_dx, -2.5, 2.5)
        ny = np.clip(-dz_dy, -2.5, 2.5)
        nz = np.ones_like(z_surf)
        n_len = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-6
        nx, ny, nz = nx / n_len, ny / n_len, nz / n_len

        pix_depth = d_center - z_surf
        vis = hit_mask & (pix_depth < z_buffer[y0:y1, x0:x1])
        if not np.any(vis):
            return
        z_buffer[y0:y1, x0:x1][vis] = pix_depth[vis]

        # 6. Процедурный 3D-шейдер одежды и лица ПРЯМО НА ПОВЕРХНОСТИ СКУЛЬПТУРЫ (с учётом 3D SDF-маски рук)
        X_s = lx_1d[None, :]
        Y_s = ly_1d[:, None]
        not_arm = d_arms[idx_y, idx_x, first_k] > 0.012
        skin_sh = shade_surface(nx, ny, nz, skin_rgb, "skin")
        outfit_sh = shade_surface(nx, ny, nz, outfit_rgb, "satin")
        gold_sh = shade_surface(nx, ny, nz, (251, 191, 36), "gold")

        if is_female:
            # Изящный вырез сердечком (sweetheart neckline) на груди и высокий французский вырез на бёдрах
            top_edge = 1.515 - 0.055 * np.exp(-((X_s / 0.045) ** 2)) + 0.025 * np.cos((X_s / 0.11) * math.pi)
            bot_edge = 0.89 + 0.85 * np.abs(X_s - hip_tilt)
            in_torso_x = not_arm & (np.abs(X_s - hip_tilt * 0.4) < 0.245)
            is_corset_zone = in_torso_x & (Y_s >= bot_edge) & (Y_s <= top_edge)
            is_gold_trim = in_torso_x & (
                (np.abs(Y_s - top_edge) < 0.014) | (np.abs(Y_s - bot_edge) < 0.014)
            )
            is_shoes = Y_s < 0.11
            is_choker = (np.abs(Y_s - 1.73) < 0.014) & (np.abs(X_s) < 0.062)
        else:
            # Спортивные плавки Мачо Адониса с золотым поясом и тонкой золотой цепью-кулоном на шее
            top_edge = 1.07 - 0.02 * (X_s ** 2)
            bot_edge = 0.87 + 0.55 * np.abs(X_s - hip_tilt)
            in_torso_x = not_arm & (np.abs(X_s - hip_tilt * 0.4) < 0.24)
            is_corset_zone = in_torso_x & (Y_s >= bot_edge) & (Y_s <= top_edge)
            is_gold_trim = in_torso_x & (np.abs(Y_s - top_edge) < 0.016)
            is_shoes = Y_s < 0.10
            chain_y = 1.68 - 3.8 * (X_s ** 2)
            is_choker = (np.abs(Y_s - chain_y) < 0.009) & (np.abs(X_s) < 0.088)

        final_rgb = np.where(is_corset_zone[..., None] | is_shoes[..., None], outfit_sh, skin_sh)
        final_rgb = np.where(is_gold_trim[..., None] | is_choker[..., None], gold_sh, final_rgb)

        # Черты лица (миндалевидные глаза, радужка, зрачок, блик, брови, румянец и пухлые глянцевые губы)
        eye_y = 1.99
        for ex_sign in (-1.0, 1.0):
            ex_c = ex_sign * 0.052
            eye_dist = ((X_s - ex_c) / 0.028) ** 2 + ((Y_s - eye_y) / 0.018) ** 2
            iris_dist = ((X_s - ex_c) / 0.015) ** 2 + ((Y_s - eye_y) / 0.015) ** 2
            pupil_dist = ((X_s - ex_c) / 0.007) ** 2 + ((Y_s - eye_y) / 0.007) ** 2
            spec_dist = ((X_s - (ex_c - 0.005)) / 0.004) ** 2 + ((Y_s - (eye_y + 0.005)) / 0.004) ** 2
            lash_line = (eye_dist <= 1.25) & (eye_dist > 0.92) & (Y_s >= eye_y)
            brow_curve = eye_y + 0.036 - 0.18 * ((X_s - ex_c) ** 2)
            brow_mask = (np.abs(X_s - ex_c) < 0.034) & (np.abs(Y_s - brow_curve) < 0.006)

            final_rgb = np.where((eye_dist <= 1.0)[..., None], np.array([250.0, 252.0, 255.0])[None, None, :], final_rgb)
            final_rgb = np.where((iris_dist <= 1.0)[..., None], np.array(eye_rgb, dtype=np.float32)[None, None, :], final_rgb)
            final_rgb = np.where((pupil_dist <= 1.0)[..., None], np.array([15.0, 12.0, 28.0])[None, None, :], final_rgb)
            final_rgb = np.where((spec_dist <= 1.0)[..., None], np.array([255.0, 255.0, 255.0])[None, None, :], final_rgb)
            final_rgb = np.where((lash_line | brow_mask)[..., None], np.array([35.0, 22.0, 28.0])[None, None, :], final_rgb)

        # Пухлые глянцевые губы с бликом
        lip_y = 1.905
        lip_dist = (X_s / 0.036) ** 2 + ((Y_s - lip_y) / 0.015) ** 2
        lip_col = (225, 29, 72) if is_female else (185, 68, 72)
        lip_sh = shade_surface(nx, ny, nz, lip_col, "satin")
        final_rgb = np.where((lip_dist <= 1.0)[..., None], lip_sh, final_rgb)

        img_buf[y0:y1, x0:x1][vis] = np.clip(final_rgb[vis], 0.0, 255.0)

        # Верхняя и боковая укладка волос поверх лба/висков
        render_smooth_ellipsoid_3d(bx, by + 2.11, bz + 0.01, 0.165, 0.095, 0.165, hair_rgb, material="satin", z_bias=-0.04)
        if is_female:
            render_continuous_capsule_3d((bx - 0.135, by + 2.08, bz + 0.03), (bx - 0.155, by + 1.78, bz + 0.05), 0.055, 0.038, hair_rgb, material="satin", z_bias=-0.03)
            render_continuous_capsule_3d((bx + 0.135, by + 2.08, bz + 0.03), (bx + 0.155, by + 1.78, bz + 0.05), 0.055, 0.038, hair_rgb, material="satin", z_bias=-0.03)

        if bunny_ears:
            render_continuous_capsule_3d((bx - 0.075, by + 2.15, bz), (bx - 0.12, by + 2.42, bz + 0.02), 0.036, 0.022, (244, 114, 182), material="satin", z_bias=-0.05)
            render_continuous_capsule_3d((bx + 0.075, by + 2.15, bz), (bx + 0.12, by + 2.42, bz + 0.02), 0.036, 0.022, (244, 114, 182), material="satin", z_bias=-0.05)

    # Рендерим все 4 бесшовные 3D-модели на авансцене
    render_sculpted_character(
        -1.65, 0.0, 1.35,
        skin_rgb=(246, 194, 164),
        outfit_rgb=(225, 29, 72),
        hair_rgb=(251, 191, 36),
        eye_rgb=(56, 189, 248),
        is_female=True,
        corset=True,
        bunny_ears=True,
        hip_tilt=0.055,
        arm_wave=0.20,
    )
    render_sculpted_character(
        -0.55, 0.0, 1.48,
        skin_rgb=(202, 128, 84),
        outfit_rgb=(2, 132, 199),
        hair_rgb=(39, 39, 42),
        eye_rgb=(16, 185, 129),
        is_female=False,
        corset=False,
        bunny_ears=False,
        hip_tilt=-0.03,
        arm_wave=0.14,
    )
    render_sculpted_character(
        0.55, 0.0, 1.45,
        skin_rgb=(244, 184, 149),
        outfit_rgb=(217, 70, 239),
        hair_rgb=(56, 189, 248),
        eye_rgb=(217, 70, 239),
        is_female=True,
        corset=True,
        bunny_ears=False,
        hip_tilt=-0.055,
        arm_wave=0.24,
    )
    render_sculpted_character(
        1.65, 0.0, 1.25,
        skin_rgb=(248, 200, 172),
        outfit_rgb=(244, 63, 94),
        hair_rgb=(253, 224, 71),
        eye_rgb=(59, 130, 246),
        is_female=True,
        corset=True,
        angel_wings=True,
        hip_tilt=0.05,
        arm_wave=0.18,
    )

    img = Image.fromarray(np.clip(img_buf, 0, 255).astype(np.uint8), mode="RGB")
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 19)
        font_body = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 14)
        font_tag = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 13)
    except Exception:
        font_title = ImageFont.load_default()
        font_body = ImageFont.load_default()
        font_tag = ImageFont.load_default()

    # Плашки имён аккуратно внизу у подиума (не перекрывают лица и причёски!)
    labels = [
        ("♥ Леди Вельвет (3D Дива)", 260, 622, (225, 29, 72)),
        ("★ Мачо Адонис (3D Атлет)", 515, 622, (56, 189, 248)),
        ("♥ Кибер-Сирена Неон", 765, 622, (217, 70, 239)),
        ("♥ Купидон VIP", 1020, 622, (251, 191, 36)),
    ]
    for text_lbl, lx, ly, col_rgb in labels:
        tw = len(text_lbl) * 8 + 18
        draw.rounded_rectangle([lx - tw // 2, ly, lx + tw // 2, ly + 26], radius=8, fill=(15, 12, 32), outline=col_rgb, width=2)
        draw.text((lx - tw // 2 + 10, ly + 4), text_lbl, fill=(255, 255, 255), font=font_tag)

    # HUD карточка сверху (компактная, не задевает макушки)
    npcs = state.get("npcs") or []
    draw.rounded_rectangle([18, 10, 1262, 102], radius=12, fill=(11, 9, 26), outline=(244, 114, 182), width=2)
    w_title = weather.get("title_ru", "Неоновая ночь")
    c_title = (state.get("camera") or {}).get("title_ru", "Крупный 3D-план")
    hud_header = (
        f"BossBlocks 3D Smooth PBR · {wave_label} | Мир: {state.get('world_id')} | "
        f"Свет: {w_title} | Камера: {c_title} | 3D-Моделей: {max(4, len(npcs))}"
    )
    draw.text((34, 16), hud_header, fill=(244, 114, 182), font=font_title)

    dialogue_line = ((state.get("soundscape") or {}).get("dialogue_tts") or "Гладкие бесшовные 3D-модели готовы!")[:135]
    draw.text((34, 44), f"Диалог сцены: {dialogue_line}", fill=(56, 189, 248), font=font_body)

    if agent_summary:
        clean_summary = " ".join(agent_summary.strip().split())[:145]
        draw.text((34, 70), f"Ответ облачного агента в Пульт: {clean_summary}", fill=(241, 245, 249), font=font_body)

    draw.rounded_rectangle([18, 662, 1262, 708], radius=10, fill=(15, 12, 32), outline=(251, 191, 36), width=2)
    draw.text(
        (34, 675),
        "3D-Рендер: Объёмные бесшовные 3D SDF-меши (smin ZBrush-blending + Subsurface Skin + Satin Specular, 0 квадратиков!)",
        fill=(251, 191, 36),
        font=font_body,
    )

    img.save(target, format="PNG")
    return target

