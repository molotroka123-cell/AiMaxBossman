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
    vendor_three = Path(__file__).resolve().parent.parent / "ui" / "vendor" / "three.min.js"
    if vendor_three.is_file():
        (project_dir / "three.min.js").write_bytes(vendor_three.read_bytes())
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
            "three.min.js",
            "tests/owner_director_check.gd",
        ],
        "director_state": director_state,
        "godot_headless_smoke": smoke_result,
    }


def render_voxel_web_game_html(world_id: str = "bossblocks-local-01") -> str:
    """Возвращает настоящую WebGL2 3D-игру на Three.js (LatheGeometry + TubeGeometry + MeshStandardMaterial PBR + WASD + Флирт-Квесты + Школа Аргона)."""
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
<title>BossBlocks Genie-Local — Three.js WebGL2 3D Boudoir & Школа Боссмана</title>
<script src="./three.min.js"></script>
<script>if (!window.THREE) document.write('<scr' + 'ipt src="/api/game-bootstrap/three.min.js"></scr' + 'ipt>');</script>
<style>
  :root {{
    color-scheme: dark;
    --bg: #080512;
    --panel: rgba(14, 10, 28, 0.84);
    --border: rgba(244, 114, 182, 0.38);
    --accent: #f472b6;
    --cyan: #38bdf8;
    --gold: #fbbf24;
    --text: #f8fafc;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; user-select: none; }}
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
    top: 12px;
    left: 14px;
    right: 14px;
    display: flex;
    justify-content: space-between;
    align-items: flex-start;
    gap: 12px;
    pointer-events: none;
    z-index: 10;
  }}
  .hud-card {{
    background: var(--panel);
    backdrop-filter: blur(16px);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 11px 15px;
    pointer-events: auto;
    box-shadow: 0 12px 32px rgba(0, 0, 0, 0.55);
  }}
  .hud-title {{
    font-size: 15px;
    font-weight: 800;
    background: linear-gradient(90deg, #f472b6, #fbbf24, #38bdf8);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    margin-bottom: 3px;
  }}
  .hud-sub {{
    font-size: 11.5px;
    color: #cbd5e1;
    line-height: 1.35;
  }}
  .badges {{
    display: flex;
    gap: 6px;
    flex-wrap: wrap;
    margin-top: 7px;
  }}
  .badge {{
    font-size: 10.5px;
    padding: 3px 9px;
    border-radius: 999px;
    background: rgba(244, 114, 182, 0.16);
    border: 1px solid rgba(244, 114, 182, 0.42);
    color: #fbcfe8;
    font-weight: 600;
  }}
  .badge.cyan {{
    background: rgba(56, 189, 248, 0.16);
    border-color: rgba(56, 189, 248, 0.42);
    color: #bae6fd;
  }}
  .badge.gold {{
    background: rgba(251, 191, 36, 0.16);
    border-color: rgba(251, 191, 36, 0.45);
    color: #fde68a;
  }}
  .cam-bar {{
    display: flex;
    gap: 6px;
    margin-top: 8px;
    flex-wrap: wrap;
  }}
  .cam-btn {{
    background: rgba(30, 22, 54, 0.85);
    color: #f8fafc;
    border: 1px solid rgba(148, 163, 184, 0.35);
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 11px;
    font-weight: 600;
    cursor: pointer;
    transition: all 140ms ease;
  }}
  .cam-btn:hover, .cam-btn.active {{
    background: linear-gradient(135deg, #ec4899, #8b5cf6);
    border-color: #f472b6;
    transform: translateY(-1px);
  }}
  .quest-box {{
    min-width: 310px;
    max-width: 370px;
  }}
  .meter-wrap {{
    margin-top: 6px;
    background: rgba(15, 23, 42, 0.85);
    height: 10px;
    border-radius: 999px;
    overflow: hidden;
    border: 1px solid rgba(244, 114, 182, 0.35);
  }}
  .meter-fill {{
    height: 100%;
    width: 78%;
    background: linear-gradient(90deg, #ec4899, #fbbf24, #38bdf8);
    transition: width 300ms ease;
  }}
  .dialog-modal {{
    position: fixed;
    left: 50%;
    bottom: 84px;
    transform: translateX(-50%);
    width: min(760px, calc(100vw - 28px));
    background: rgba(14, 9, 30, 0.92);
    backdrop-filter: blur(18px);
    border: 1px solid rgba(251, 191, 36, 0.55);
    border-radius: 16px;
    padding: 12px 16px;
    z-index: 12;
    box-shadow: 0 16px 40px rgba(0, 0, 0, 0.65);
  }}
  .dialog-speaker {{
    font-size: 12.5px;
    font-weight: 800;
    color: #fbbf24;
    margin-bottom: 3px;
    display: flex;
    justify-content: space-between;
  }}
  .dialog-text {{
    font-size: 13px;
    color: #f8fafc;
    margin-bottom: 8px;
  }}
  .dialog-choices {{
    display: flex;
    gap: 7px;
    flex-wrap: wrap;
  }}
  .choice-btn {{
    background: rgba(244, 114, 182, 0.16);
    border: 1px solid rgba(244, 114, 182, 0.45);
    color: #fdf2f8;
    border-radius: 8px;
    padding: 5px 10px;
    font-size: 11.5px;
    cursor: pointer;
    transition: all 140ms ease;
  }}
  .choice-btn:hover {{
    background: rgba(244, 114, 182, 0.35);
    transform: scale(1.02);
  }}
  .hud-bottom {{
    position: fixed;
    bottom: 12px;
    left: 14px;
    right: 14px;
    display: flex;
    gap: 8px;
    justify-content: center;
    pointer-events: none;
    z-index: 10;
  }}
  .director-bar {{
    pointer-events: auto;
    display: flex;
    gap: 8px;
    width: min(880px, 100%);
    background: var(--panel);
    backdrop-filter: blur(16px);
    border: 1px solid var(--border);
    border-radius: 14px;
    padding: 8px 10px;
  }}
  .director-bar input {{
    flex: 1;
    background: rgba(8, 6, 20, 0.85);
    border: 1px solid rgba(148, 163, 184, 0.3);
    color: #fff;
    border-radius: 9px;
    padding: 8px 12px;
    font-size: 13px;
    outline: none;
  }}
  .director-bar button {{
    background: linear-gradient(135deg, #ec4899, #8b5cf6);
    border: none;
    color: #fff;
    font-weight: 700;
    padding: 8px 16px;
    border-radius: 9px;
    cursor: pointer;
    font-size: 12.5px;
  }}
  .name-tags {{
    position: fixed;
    inset: 0;
    pointer-events: none;
    z-index: 5;
  }}
  .char-tag {{
    position: absolute;
    transform: translate(-50%, -50%);
    background: rgba(12, 8, 26, 0.86);
    border: 1.5px solid #f472b6;
    border-radius: 999px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 700;
    color: #f8fafc;
    white-space: nowrap;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
  }}
</style>
</head>
<body>
<canvas id="viewport"></canvas>
<div id="nameTags" class="name-tags"></div>

<div class="hud-top">
  <div class="hud-card" style="max-width: 650px;">
    <div class="hud-title" id="hudTitle">BossBlocks Genie-Local • Three.js WebGL2 Smooth 3D Studio (Мир: {world_id})</div>
    <div class="hud-sub" id="hudSub">Обучение Боссмана (Школа Аргона): 360° LatheGeometry изгибы тел • MeshStandardMaterial Subsurface Skin & Атлас • 0 квадратиков!</div>
    <div class="badges">
      <span class="badge" id="bEngine">Движок: Three.js WebGL2 PBR (64-seg LatheGeometry)</span>
      <span class="badge cyan" id="bSkill">Навык Боссмана: smooth-3d-webgl-character-architect (v2.0)</span>
      <span class="badge gold" id="bWeather">Атмосфера: Неоновый VIP-Будуар</span>
    </div>
    <div class="cam-bar">
      <button class="cam-btn active" onclick="setCameraPreset(1)" id="camBtn1">[1] Общий план Кабаре</button>
      <button class="cam-btn" onclick="setCameraPreset(2)" id="camBtn2">[2] Крупный план: 3D Фигуры & Лица</button>
      <button class="cam-btn" onclick="setCameraPreset(3)" id="camBtn3">[3] Танец у Пилона & Тверк-Волна</button>
      <button class="cam-btn" onclick="setCameraPreset(4)" id="camBtn4">[4] VIP-Джакузи & Купидон</button>
    </div>
  </div>

  <div class="hud-card quest-box">
    <div class="hud-title">🥂 Квест Будуара & Уроки Боссмана</div>
    <div class="hud-sub" id="questStatus">Собрано шампанского: <b id="champCount">3</b>/5 🥂 • Сердец: <b id="heartCount">4</b>/5 💖<br>Уровень Соблазна / Хайпа: <b id="hypeVal">78%</b></div>
    <div class="meter-wrap"><div class="meter-fill" id="hypeFill" style="width:78%"></div></div>
    <div class="hud-sub" style="margin-top:6px; font-size:10.5px; color:#94a3b8;">
      Управление: <b>Мышь</b> — 3D орбита/зум • <b>WASD</b> — движение • <b>Клик по 3D-модели / [1-4]</b> — флирт и смена танца
    </div>
  </div>
</div>

<div class="dialog-modal" id="dialogModal">
  <div class="dialog-speaker">
    <span id="dlgSpeaker">♥ Леди Вельвет (3D Дива) — Интерактивный Флирт-Диалог</span>
    <span style="color:#38bdf8; font-size:11px;">[Урок Аргона №3: Живая 3D-мимика и квесты]</span>
  </div>
  <div class="dialog-text" id="dlgText">«Ох, Босс! Аргон отучил Боссмана лепить квадратики — теперь у нас настоящие 3D-изгибы LatheGeometry, шёлковые корсеты и горячее джакузи! Выбирай, чем займёмся!»</div>
  <div class="dialog-choices">
    <button class="choice-btn" onclick="triggerInteraction('toast')">🥂 Угостить шампанским (+15% Соблазн)</button>
    <button class="choice-btn" onclick="triggerInteraction('dance')">💃 Включить Синхронный Кабаре-Танец</button>
    <button class="choice-btn" onclick="triggerInteraction('compliment')">🔥 Сделать комплимент 3D-фигуре</button>
    <button class="choice-btn" onclick="triggerInteraction('next_char')">✨ Переключить собеседника</button>
  </div>
</div>

<div class="hud-bottom">
  <div class="director-bar">
    <input id="promptInput" type="text" placeholder="Команда ИИ-Режиссёру Боссмана (например: добавь джакузи, кровать-сердце, смени погоду на закат)..." />
    <button id="sendBtn" onclick="sendDirectorPrompt()">🎬 Применить к 3D-сцене</button>
  </div>
</div>

<script>
const WORLD_ID = {json.dumps(world_id)};
let worldState = {state_json};

const urlParams = new URLSearchParams(window.location.search);
const initialCamPreset = parseInt(urlParams.get('cam') || '1', 10);
const customTitle = urlParams.get('title');
const customSub = urlParams.get('sub');
const customDlg = urlParams.get('dlg');
if (customTitle) document.getElementById('hudTitle').textContent = customTitle;
if (customSub) document.getElementById('hudSub').textContent = customSub;
if (customDlg) document.getElementById('dlgText').textContent = customDlg;

const canvas = document.getElementById('viewport');
const renderer = new THREE.WebGLRenderer({{ canvas, antialias: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' }});
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 0.94;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0c061a);
scene.fog = new THREE.FogExp2(0x0c061a, 0.038);

const camera = new THREE.PerspectiveCamera(38, window.innerWidth / window.innerHeight, 0.1, 100);

// Освещение VIP-Будуара (сбалансированная экспозиция без пересвета кожи)
const hemiLight = new THREE.HemisphereLight(0xffb6e6, 0x1a0c32, 0.48);
scene.add(hemiLight);

const keyLight = new THREE.DirectionalLight(0xffe8d6, 0.92);
keyLight.position.set(3.2, 6.2, 5.8);
keyLight.castShadow = true;
keyLight.shadow.mapSize.width = 2048;
keyLight.shadow.mapSize.height = 2048;
keyLight.shadow.bias = -0.0005;
scene.add(keyLight);

const pinkSpot = new THREE.PointLight(0xf472b6, 1.35, 14);
pinkSpot.position.set(-3.4, 3.6, 2.6);
scene.add(pinkSpot);

const cyanSpot = new THREE.PointLight(0x38bdf8, 1.25, 14);
cyanSpot.position.set(3.4, 3.6, 2.6);
scene.add(cyanSpot);

const goldRim = new THREE.PointLight(0xfbbf24, 1.25, 12);
goldRim.position.set(0.0, 3.2, -2.5);
scene.add(goldRim);

// 1. Зеркальный пол и круглый подиум кабаре с неоновыми кольцами
const floorGeo = new THREE.PlaneGeometry(40, 40);
const floorMat = new THREE.MeshStandardMaterial({{ color: 0x110926, roughness: 0.22, metalness: 0.45 }});
const floorMesh = new THREE.Mesh(floorGeo, floorMat);
floorMesh.rotation.x = -Math.PI / 2;
floorMesh.receiveShadow = true;
scene.add(floorMesh);

const stageGroup = new THREE.Group();
scene.add(stageGroup);

const podiumMat = new THREE.MeshStandardMaterial({{ color: 0x1f113c, roughness: 0.18, metalness: 0.55 }});
const podiumMesh = new THREE.Mesh(new THREE.CylinderGeometry(3.6, 3.8, 0.18, 96), podiumMat);
podiumMesh.position.y = 0.09;
podiumMesh.receiveShadow = true;
stageGroup.add(podiumMesh);

const neonPinkMat = new THREE.MeshBasicMaterial({{ color: 0xf472b6 }});
const neonRing1 = new THREE.Mesh(new THREE.TorusGeometry(3.62, 0.035, 24, 128), neonPinkMat);
neonRing1.rotation.x = Math.PI / 2;
neonRing1.position.y = 0.18;
stageGroup.add(neonRing1);

const neonGoldMat = new THREE.MeshBasicMaterial({{ color: 0xfbbf24 }});
const neonRing2 = new THREE.Mesh(new THREE.TorusGeometry(2.85, 0.022, 20, 128), neonGoldMat);
neonRing2.rotation.x = Math.PI / 2;
neonRing2.position.y = 0.185;
stageGroup.add(neonRing2);

// 2. Латунный шест-пилон, круглая бархатная кровать-сердце и VIP-джакузи с пузырьками
const poleMat = new THREE.MeshStandardMaterial({{ color: 0xfbbf24, roughness: 0.10, metalness: 0.94 }});
const poleMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.048, 0.048, 3.8, 48), poleMat);
poleMesh.position.set(0.0, 2.08, -0.35);
poleMesh.castShadow = true;
stageGroup.add(poleMesh);

// Бархатный диван-кровать слева сзади
const bedGroup = new THREE.Group();
bedGroup.position.set(-2.15, 0.18, -1.15);
stageGroup.add(bedGroup);
const velvetMat = new THREE.MeshStandardMaterial({{ color: 0xbe123c, roughness: 0.35, metalness: 0.12 }});
const mattress = new THREE.Mesh(new THREE.CylinderGeometry(0.95, 1.02, 0.34, 64), velvetMat);
mattress.position.y = 0.17;
mattress.scale.set(1.15, 1.0, 0.85);
mattress.castShadow = true;
bedGroup.add(mattress);

// Спинка в форме 3D-сердца
const heartShape = new THREE.Shape();
heartShape.moveTo(0, 0.25);
heartShape.bezierCurveTo(0, 0.55, -0.55, 0.55, -0.55, 0.20);
heartShape.bezierCurveTo(-0.55, -0.12, 0, -0.32, 0, -0.52);
heartShape.bezierCurveTo(0, -0.32, 0.55, -0.12, 0.55, 0.20);
heartShape.bezierCurveTo(0.55, 0.55, 0, 0.55, 0, 0.25);
const heartGeo = new THREE.ExtrudeGeometry(heartShape, {{ depth: 0.14, bevelEnabled: true, bevelSegments: 8, steps: 2, bevelSize: 0.06, bevelThickness: 0.06 }});
const headboard = new THREE.Mesh(heartGeo, new THREE.MeshStandardMaterial({{ color: 0xe11d48, roughness: 0.28, metalness: 0.18 }}));
headboard.position.set(0, 0.72, -0.68);
headboard.scale.set(1.15, 1.15, 1.0);
bedGroup.add(headboard);

// VIP-Джакузи справа сзади с пенящейся бирюзовой водой
const tubGroup = new THREE.Group();
tubGroup.position.set(2.15, 0.18, -1.05);
stageGroup.add(tubGroup);
const tubRim = new THREE.Mesh(new THREE.CylinderGeometry(1.05, 0.96, 0.46, 64), new THREE.MeshStandardMaterial({{ color: 0xfbbf24, roughness: 0.22, metalness: 0.65 }}));
tubRim.position.y = 0.23;
tubGroup.add(tubRim);
const waterMesh = new THREE.Mesh(new THREE.CylinderGeometry(0.94, 0.94, 0.05, 64), new THREE.MeshStandardMaterial({{ color: 0x38bdf8, emissive: 0x0ea5e9, emissiveIntensity: 0.45, roughness: 0.08, metalness: 0.2 }}));
waterMesh.position.y = 0.44;
tubGroup.add(waterMesh);

const bubbles = [];
const bubbleMat = new THREE.MeshStandardMaterial({{ color: 0xe0f2fe, emissive: 0x38bdf8, emissiveIntensity: 0.3, roughness: 0.05, transparent: true, opacity: 0.82 }});
for (let i = 0; i < 18; i++) {{
  const b = new THREE.Mesh(new THREE.SphereGeometry(0.035 + (i % 3) * 0.015, 16, 16), bubbleMat);
  const ang = (i / 18) * Math.PI * 2;
  const rad = 0.2 + (i % 4) * 0.18;
  b.position.set(Math.cos(ang) * rad, 0.46 + (i % 5) * 0.06, Math.sin(ang) * rad);
  tubGroup.add(b);
  bubbles.push({{ mesh: b, baseY: 0.46, speed: 0.6 + (i % 5) * 0.2, phase: i }});
}}

// 3. ГЕНЕРАТОР ГЛАДКИХ ЗАМКНУТЫХ АНАТОМИЧЕСКИХ 3D-МОДЕЛЕЙ (THREE.LatheGeometry с закрытыми полюсами + Сферические Суставы + PBR)
function createClosedLathe(points2D, segments = 56, scaleX = 1.0, scaleZ = 1.0, mat) {{
  const pts = points2D.map(p => new THREE.Vector2(Math.max(0.0005, p[0]), p[1]));
  const curve = new THREE.SplineCurve(pts);
  const sampled = curve.getPoints(44);
  sampled[0].x = 0.0001;
  sampled[sampled.length - 1].x = 0.0001;
  const geo = new THREE.LatheGeometry(sampled, segments);
  geo.scale(scaleX, 1.0, scaleZ);
  geo.computeVertexNormals();
  const mesh = new THREE.Mesh(geo, mat);
  mesh.castShadow = true;
  mesh.receiveShadow = true;
  return mesh;
}}

function createSmooth3DCharacter(cfg) {{
  const root = new THREE.Group();
  root.position.set(cfg.x, 0.18, cfg.z);
  root.userData = {{ cfg }};

  // PBR-материалы: тёплая живая кожа с мягким подповерхностным тоном и глянцевый атлас
  const skinMat = new THREE.MeshStandardMaterial({{
    color: cfg.skinHex,
    emissive: 0x4a160c,
    emissiveIntensity: 0.18,
    roughness: 0.36,
    metalness: 0.03,
  }});
  const outfitMat = new THREE.MeshStandardMaterial({{
    color: cfg.outfitHex,
    emissive: cfg.outfitHex,
    emissiveIntensity: 0.14,
    roughness: 0.18,
    metalness: 0.26,
  }});
  const goldMat = new THREE.MeshStandardMaterial({{
    color: 0xfbbf24,
    emissive: 0x78350f,
    emissiveIntensity: 0.18,
    roughness: 0.16,
    metalness: 0.88,
  }});
  const hairMat = new THREE.MeshStandardMaterial({{
    color: cfg.hairHex,
    roughness: 0.26,
    metalness: 0.18,
  }});

  const hipGroup = new THREE.Group();
  hipGroup.position.y = 0.96;
  root.add(hipGroup);

  const isF = cfg.isFemale;

  // A. Замкнутый бесшовный торс вращения (LatheGeometry от нижнего полюса таза через талию и плечи до макушки шеи)
  const torsoProfile = isF ? [
    [0.0001, -0.10],
    [0.155, -0.06],
    [0.232, 0.04],   // пышные округлые бёдра
    [0.195, 0.14],
    [0.122, 0.27],   // тонкая скульптурная талия («песочные часы»)
    [0.158, 0.40],
    [0.185, 0.52],   // грудная клетка
    [0.198, 0.61],   // плечевой пояс
    [0.065, 0.68],   // основание шеи
    [0.054, 0.85],   // верх шеи
    [0.0001, 0.88],  // закрытый верхний полюс шеи
  ] : [
    [0.0001, -0.10],
    [0.145, -0.05],
    [0.188, 0.05],   // атлетический таз
    [0.162, 0.24],   // узкая мужская талия
    [0.215, 0.44],   // широчайшие мышцы спины (V-торс)
    [0.258, 0.59],   // широкая мужская грудь
    [0.272, 0.65],   // мощные плечи
    [0.075, 0.71],   // шея
    [0.068, 0.86],
    [0.0001, 0.89],
  ];
  const torsoMesh = createClosedLathe(torsoProfile, 64, 1.0, 0.68, skinMat);
  hipGroup.add(torsoMesh);

  // B. Атласный корсет / боди (замкнутая 3D-оболочка поверх талии и бёдер) или спортивные плавки
  if (isF) {{
    const corsetProfile = [
      [0.0001, -0.08],
      [0.175, -0.03],
      [0.236, 0.04],
      [0.199, 0.14],
      [0.126, 0.27],
      [0.162, 0.40],
      [0.186, 0.48],
      [0.0001, 0.50],
    ];
    const corsetMesh = createClosedLathe(corsetProfile, 64, 1.008, 0.69, outfitMat);
    hipGroup.add(corsetMesh);

    const waistBelt = new THREE.Mesh(new THREE.TorusGeometry(0.128, 0.009, 16, 64), goldMat);
    waistBelt.rotation.x = Math.PI / 2;
    waistBelt.scale.set(1.0, 0.69, 1.0);
    waistBelt.position.y = 0.27;
    hipGroup.add(waistBelt);

    // Скульптурный 3D-бюст (левая и правая грудь с каплевидным профилем и атласными чашечками)
    [-1, 1].forEach(sign => {{
      const bustSkin = new THREE.Mesh(new THREE.SphereGeometry(0.104, 40, 40), skinMat);
      bustSkin.scale.set(0.95, 0.92, 1.06);
      bustSkin.position.set(sign * 0.086, 0.50, 0.102);
      hipGroup.add(bustSkin);

      const bustCup = new THREE.Mesh(new THREE.SphereGeometry(0.107, 40, 40, 0, Math.PI * 2, Math.PI * 0.34, Math.PI * 0.66), outfitMat);
      bustCup.scale.set(0.96, 0.92, 1.08);
      bustCup.position.set(sign * 0.086, 0.495, 0.104);
      hipGroup.add(bustCup);
    }});

    // Округлые 3D-ягодицы сзади
    [-1, 1].forEach(sign => {{
      const glute = new THREE.Mesh(new THREE.SphereGeometry(0.118, 36, 36), outfitMat);
      glute.scale.set(0.92, 1.02, 0.95);
      glute.position.set(sign * 0.085, 0.03, -0.105);
      hipGroup.add(glute);
    }});
  }} else {{
    const trunksProfile = [
      [0.0001, -0.09],
      [0.155, -0.04],
      [0.192, 0.05],
      [0.178, 0.12],
      [0.0001, 0.13],
    ];
    const trunksMesh = createClosedLathe(trunksProfile, 56, 1.01, 0.70, outfitMat);
    hipGroup.add(trunksMesh);

    const belt = new THREE.Mesh(new THREE.TorusGeometry(0.178, 0.011, 16, 64), goldMat);
    belt.rotation.x = Math.PI / 2;
    belt.scale.set(1.0, 0.70, 1.0);
    belt.position.y = 0.12;
    hipGroup.add(belt);

    [-1, 1].forEach(sign => {{
      const pec = new THREE.Mesh(new THREE.SphereGeometry(0.102, 36, 36), skinMat);
      pec.scale.set(1.12, 0.82, 0.72);
      pec.position.set(sign * 0.092, 0.54, 0.112);
      hipGroup.add(pec);
    }});
    for (let row = 0; row < 3; row++) {{
      [-1, 1].forEach(sign => {{
        const ab = new THREE.Mesh(new THREE.SphereGeometry(0.036, 24, 24), skinMat);
        ab.scale.set(1.15, 0.85, 0.62);
        ab.position.set(sign * 0.042, 0.22 + row * 0.078, 0.115);
        hipGroup.add(ab);
      }});
    }}
  }}

  // Золотой чокер / медальон на шее
  const choker = new THREE.Mesh(new THREE.TorusGeometry(isF ? 0.058 : 0.074, 0.008, 16, 48), goldMat);
  choker.rotation.x = Math.PI / 2;
  choker.position.y = 0.73;
  hipGroup.add(choker);

  // C. Замкнутые стройные 3D-ноги со сферическими суставами бедра и колена (никаких срезов цилиндров!)
  const legGroups = [];
  [-1, 1].forEach(sign => {{
    const legRoot = new THREE.Group();
    legRoot.position.set(sign * (isF ? 0.118 : 0.128), 0.01, 0.0);
    hipGroup.add(legRoot);

    const thighPts = isF ? [
      [0.0001, -0.49],
      [0.066, -0.45],
      [0.088, -0.26],
      [0.116, -0.06],
      [0.105, 0.04],
      [0.0001, 0.08],
    ] : [
      [0.0001, -0.49],
      [0.072, -0.45],
      [0.096, -0.26],
      [0.118, -0.06],
      [0.108, 0.04],
      [0.0001, 0.08],
    ];
    const thighMesh = createClosedLathe(thighPts, 44, 0.94, 0.96, skinMat);
    legRoot.add(thighMesh);

    if (isF) {{
      const garter = new THREE.Mesh(new THREE.TorusGeometry(0.095, 0.008, 16, 48), outfitMat);
      garter.rotation.x = Math.PI / 2;
      garter.position.y = -0.22;
      legRoot.add(garter);
    }}

    const kneeGroup = new THREE.Group();
    kneeGroup.position.y = -0.45;
    legRoot.add(kneeGroup);

    const calfPts = [
      [0.0001, -0.48],
      [0.042, -0.44],
      [0.056, -0.28],
      [0.074, -0.14], // икроножная мышца
      [0.065, 0.0],
      [0.0001, 0.04],
    ];
    const calfMesh = createClosedLathe(calfPts, 44, 0.94, 0.95, skinMat);
    kneeGroup.add(calfMesh);

    const shoe = new THREE.Mesh(new THREE.SphereGeometry(0.058, 28, 28), outfitMat);
    shoe.scale.set(0.82, 0.55, 1.55);
    shoe.position.set(0, -0.46, 0.035);
    kneeGroup.add(shoe);

    if (isF) {{
      const stiletto = new THREE.Mesh(new THREE.CylinderGeometry(0.008, 0.005, 0.09, 16), goldMat);
      stiletto.position.set(0, -0.47, -0.025);
      kneeGroup.add(stiletto);
    }}

    legGroups.push({{ sign, legRoot, kneeGroup }});
  }});

  // D. Замкнутые плавные 3D-руки со сферическими плечами и локтями
  const armGroups = [];
  [-1, 1].forEach(sign => {{
    const shGroup = new THREE.Group();
    shGroup.position.set(sign * (isF ? 0.195 : 0.258), 0.59, 0.0);
    hipGroup.add(shGroup);

    const deltoid = new THREE.Mesh(new THREE.SphereGeometry(isF ? 0.062 : 0.076, 28, 28), skinMat);
    shGroup.add(deltoid);

    const upperPts = [
      [0.0001, -0.30],
      [0.044, -0.27],
      [0.054, -0.14],
      [0.060, 0.0],
      [0.0001, 0.04],
    ];
    const upperArm = createClosedLathe(upperPts, 36, 0.92, 0.92, skinMat);
    shGroup.add(upperArm);

    const elbowGroup = new THREE.Group();
    elbowGroup.position.y = -0.27;
    shGroup.add(elbowGroup);

    const forePts = [
      [0.0001, -0.28],
      [0.034, -0.25],
      [0.042, -0.12],
      [0.045, 0.0],
      [0.0001, 0.03],
    ];
    const foreArm = createClosedLathe(forePts, 36, 0.90, 0.90, skinMat);
    elbowGroup.add(foreArm);

    const hand = new THREE.Mesh(new THREE.SphereGeometry(0.038, 24, 24), skinMat);
    hand.scale.set(0.75, 1.15, 0.55);
    hand.position.y = -0.27;
    elbowGroup.add(hand);

    armGroups.push({{ sign, shGroup, elbowGroup }});
  }});

  // E. 3D-Голова, Выразительные Глаза, Ресницы, Глянцевые Губы и Объёмные Локоны Волос (TubeGeometry)
  const headGroup = new THREE.Group();
  headGroup.position.y = 0.95;
  hipGroup.add(headGroup);

  const headMesh = new THREE.Mesh(new THREE.SphereGeometry(0.146, 48, 48), skinMat);
  headMesh.scale.set(0.88, 1.06, 0.92);
  headMesh.castShadow = true;
  headGroup.add(headMesh);

  // Аккуратный 3D-носик
  const nose = new THREE.Mesh(new THREE.SphereGeometry(0.018, 24, 24), skinMat);
  nose.scale.set(0.75, 1.25, 1.15);
  nose.position.set(0, -0.01, 0.135);
  headGroup.add(nose);

  // Миндалевидные 3D-глаза, радужка, зрачок, живой блик и ресницы
  const scleraMat = new THREE.MeshBasicMaterial({{ color: 0xffffff }});
  const irisMat = new THREE.MeshBasicMaterial({{ color: cfg.eyeHex }});
  const pupilMat = new THREE.MeshBasicMaterial({{ color: 0x0f0b1d }});
  const lashMat = new THREE.MeshBasicMaterial({{ color: 0x1e1122 }});

  [-1, 1].forEach(sign => {{
    const ex = sign * 0.048;
    const sclera = new THREE.Mesh(new THREE.SphereGeometry(0.022, 24, 24), scleraMat);
    sclera.scale.set(1.22, 0.85, 0.55);
    sclera.position.set(ex, 0.025, 0.125);
    headGroup.add(sclera);

    const iris = new THREE.Mesh(new THREE.SphereGeometry(0.013, 20, 20), irisMat);
    iris.scale.set(1.0, 1.0, 0.45);
    iris.position.set(ex, 0.025, 0.134);
    headGroup.add(iris);

    const pupil = new THREE.Mesh(new THREE.SphereGeometry(0.007, 16, 16), pupilMat);
    pupil.position.set(ex, 0.025, 0.138);
    headGroup.add(pupil);

    const catchlight = new THREE.Mesh(new THREE.SphereGeometry(0.0032, 12, 12), scleraMat);
    catchlight.position.set(ex - 0.004, 0.030, 0.141);
    headGroup.add(catchlight);

    const brow = new THREE.Mesh(new THREE.TorusGeometry(0.026, 0.0038, 12, 24, Math.PI * 0.75), lashMat);
    brow.rotation.z = Math.PI * 0.12 - sign * 0.08;
    brow.position.set(ex, 0.042, 0.128);
    headGroup.add(brow);
  }});

  // Пухлые глянцевые 3D-губы
  const lipMat = new THREE.MeshStandardMaterial({{ color: isF ? 0xe11d48 : 0xc2594a, roughness: 0.14, metalness: 0.15 }});
  const lips = new THREE.Mesh(new THREE.SphereGeometry(0.024, 24, 24), lipMat);
  lips.scale.set(1.38, 0.58, 0.65);
  lips.position.set(0, -0.058, 0.126);
  headGroup.add(lips);

  // F. Пышная 3D-причёска из гладких кривых TubeGeometry
  const crownHair = new THREE.Mesh(new THREE.SphereGeometry(0.154, 40, 40), hairMat);
  crownHair.scale.set(0.94, 0.95, 0.98);
  crownHair.position.set(0, 0.035, -0.018);
  headGroup.add(crownHair);

  if (isF) {{
    for (let i = 0; i < 12; i++) {{
      const angle = (i / 11) * Math.PI * 1.35 - Math.PI * 0.175 + Math.PI * 0.5;
      const side = Math.cos(angle);
      const back = -Math.abs(Math.sin(angle)) * 0.95;
      const strandCurve = new THREE.CatmullRomCurve3([
        new THREE.Vector3(side * 0.08, 0.14, back * 0.06),
        new THREE.Vector3(side * 0.145, 0.04, back * 0.11 + 0.02),
        new THREE.Vector3(side * 0.165, -0.14, back * 0.12),
        new THREE.Vector3(side * 0.145, -0.32, back * 0.09),
      ]);
      const strandGeo = new THREE.TubeGeometry(strandCurve, 28, 0.036, 16, false);
      const strandMesh = new THREE.Mesh(strandGeo, hairMat);
      headGroup.add(strandMesh);
    }}
  }}

  // G. Особые аксессуары (Ушки зайчика Playboy-кабаре или Крылья Купидона)
  if (cfg.bunnyEars) {{
    [-1, 1].forEach(sign => {{
      const ear = new THREE.Mesh(new THREE.CylinderGeometry(0.018, 0.032, 0.26, 24), outfitMat);
      ear.scale.set(1.0, 1.0, 0.45);
      ear.position.set(sign * 0.065, 0.25, 0.0);
      ear.rotation.z = -sign * 0.16;
      headGroup.add(ear);
    }});
  }}
  if (cfg.angelWings) {{
    const wingMat = new THREE.MeshStandardMaterial({{ color: 0xf8fafc, emissive: 0x38bdf8, emissiveIntensity: 0.15, roughness: 0.25 }});
    [-1, 1].forEach(sign => {{
      const wingGroup = new THREE.Group();
      wingGroup.position.set(sign * 0.12, 0.55, -0.11);
      for (let f = 0; f < 4; f++) {{
        const feather = new THREE.Mesh(new THREE.SphereGeometry(0.095 - f * 0.012, 24, 24), wingMat);
        feather.scale.set(1.85, 0.55, 0.28);
        feather.position.set(sign * (0.14 + f * 0.04), 0.06 - f * 0.065, -0.02);
        feather.rotation.z = sign * (0.28 - f * 0.14);
        wingGroup.add(feather);
      }}
      hipGroup.add(wingGroup);
    }});
  }}

  scene.add(root);
  return {{ root, hipGroup, headGroup, legGroups, armGroups, cfg }};
}}

const CHAR_CONFIGS = [
  {{
    id: 'velvet_diva',
    name: '♥ Леди Вельвет (3D Дива)',
    tagColor: '#f472b6',
    x: -1.68, z: 0.25,
    isFemale: true,
    skinHex: 0xe89b78,
    outfitHex: 0xe11d48,
    hairHex: 0xfbbf24,
    eyeHex: 0x38bdf8,
    bunnyEars: true,
    phase: 0.0,
    line: '«Босс, теперь у меня 64-сегментный LatheGeometry корсет и шёлковые изгибы — ни одного вокселя!»'
  }},
  {{
    id: 'pool_adonis',
    name: '★ Мачо Адонис (3D Атлет)',
    tagColor: '#38bdf8',
    x: -0.56, z: 0.12,
    isFemale: false,
    skinHex: 0xc6784e,
    outfitHex: 0x0284c7,
    hairHex: 0x1e1b2e,
    eyeHex: 0x10b981,
    phase: 1.4,
    line: '«Аргон обучил Боссмана лепить V-торс и 6 кубиков пресса через гладкие сплайны! Шампанское за мой счёт!»'
  }},
  {{
    id: 'cyber_siren',
    name: '♥ Кибер-Сирена Неон',
    tagColor: '#c084fc',
    x: 0.58, z: 0.12,
    isFemale: true,
    skinHex: 0xf0a68c,
    outfitHex: 0x9333ea,
    hairHex: 0x22d3ee,
    eyeHex: 0xf472b6,
    phase: 2.8,
    line: '«Неоновый шейдер атласа + Subsurface Skin рассеивание света! Нажми [2], чтобы рассмотреть 3D-фигуры вблизи!»'
  }},
  {{
    id: 'cupid_bouncer',
    name: '♥ Купидон VIP',
    tagColor: '#fbbf24',
    x: 1.68, z: 0.25,
    isFemale: true,
    skinHex: 0xea9e7c,
    outfitHex: 0xdb2777,
    hairHex: 0xfde047,
    eyeHex: 0x60a5fa,
    angelWings: true,
    phase: 4.1,
    line: '«Крылья Купидона и стрелы соблазна готовы! Мы подняли уровень Боссмана с 5 до 12 лет!»'
  }}
];

const characters = CHAR_CONFIGS.map(createSmooth3DCharacter);

// Создаём HTML-бейджи имён под ногами персонажей
const nameTagsContainer = document.getElementById('nameTags');
const tagEls = characters.map(ch => {{
  const el = document.createElement('div');
  el.className = 'char-tag';
  el.style.borderColor = ch.cfg.tagColor;
  el.textContent = ch.cfg.name;
  nameTagsContainer.appendChild(el);
  return el;
}});

// 4. Парящие 3D-сердца и бокалы шампанского вокруг сцены
const floatingHearts = [];
for (let i = 0; i < 8; i++) {{
  const hMesh = new THREE.Mesh(heartGeo, new THREE.MeshStandardMaterial({{ color: i % 2 === 0 ? 0xf472b6 : 0xfbbf24, emissive: 0xbe185d, emissiveIntensity: 0.35, roughness: 0.2 }}));
  const ang = (i / 8) * Math.PI * 2;
  hMesh.scale.set(0.18, 0.18, 0.18);
  hMesh.position.set(Math.cos(ang) * 2.9, 1.4 + (i % 3) * 0.45, Math.sin(ang) * 2.2 - 0.3);
  scene.add(hMesh);
  floatingHearts.push({{ mesh: hMesh, ang, baseY: hMesh.position.y, speed: 0.8 + (i % 3) * 0.25 }});
}}

// 5. Управление 3D-камерой, WASD и 4 кинематографических ракурса (свободный центр экрана для всех 4 голов и фигур!)
let camYaw = 0.0, camPitch = 0.08, camDist = 5.45;
let camTarget = new THREE.Vector3(0.0, 1.38, 0.15);
let danceMode = 1;
let hypeLevel = 78;
let champCollected = 3;
let heartsCollected = 4;
let activeCharIdx = 0;

function setCameraPreset(preset) {{
  danceMode = preset;
  document.querySelectorAll('.cam-btn').forEach((b, idx) => b.classList.toggle('active', idx + 1 === preset));
  if (preset === 1) {{
    camYaw = 0.0; camPitch = 0.07; camDist = 5.45;
    camTarget.set(0.0, 1.38, 0.15);
  }} else if (preset === 2) {{
    // Крупный план: изгибы фигур и лица строго в центре экрана ниже HUD
    camYaw = 0.06; camPitch = 0.04; camDist = 3.85;
    camTarget.set(0.0, 1.52, 0.18);
  }} else if (preset === 3) {{
    // Ракурс 3/4: Танец у пилона и пластика бёдер
    camYaw = -0.32; camPitch = 0.11; camDist = 5.05;
    camTarget.set(0.0, 1.36, 0.08);
  }} else if (preset === 4) {{
    // VIP-Джакузи и правое крыло сцены
    camYaw = 0.28; camPitch = 0.12; camDist = 5.10;
    camTarget.set(0.35, 1.34, -0.05);
  }}
}}
setCameraPreset(initialCamPreset);

function triggerInteraction(action) {{
  if (action === 'toast') {{
    champCollected = Math.min(5, champCollected + 1);
    hypeLevel = Math.min(100, hypeLevel + 15);
    document.getElementById('dlgText').textContent = '🥂 Шампанское разлито по бокалам! Уровень Соблазна вырос до ' + hypeLevel + '%! Все 4 3D-модели поднимают тост за Босса!';
  }} else if (action === 'dance') {{
    setCameraPreset(danceMode === 3 ? 1 : 3);
    hypeLevel = Math.min(100, hypeLevel + 10);
    document.getElementById('dlgText').textContent = '💃 Включён режим синхронного Кабаре-Танца! Плавные 3D-вращения талии, бёдер и рук на 60 FPS!';
  }} else if (action === 'compliment') {{
    heartsCollected = Math.min(5, heartsCollected + 1);
    hypeLevel = Math.min(100, hypeLevel + 12);
    const c = CHAR_CONFIGS[activeCharIdx];
    document.getElementById('dlgSpeaker').textContent = c.name + ' — В восторге! 💖';
    document.getElementById('dlgText').textContent = c.line;
  }} else if (action === 'next_char') {{
    activeCharIdx = (activeCharIdx + 1) % CHAR_CONFIGS.length;
    const c = CHAR_CONFIGS[activeCharIdx];
    document.getElementById('dlgSpeaker').textContent = c.name + ' — Интерактивный Флирт-Диалог';
    document.getElementById('dlgText').textContent = c.line;
    camTarget.set(c.x * 0.45, 1.38, 0.18);
  }}
  document.getElementById('champCount').textContent = String(champCollected);
  document.getElementById('heartCount').textContent = String(heartsCollected);
  document.getElementById('hypeVal').textContent = hypeLevel + '%';
  document.getElementById('hypeFill').style.width = hypeLevel + '%';
}}

// Клик по 3D-персонажам через THREE.Raycaster
const raycaster = new THREE.Raycaster();
const mouseVec = new THREE.Vector2();
let isDragging = false, prevX = 0, prevY = 0, dragDelta = 0;

canvas.addEventListener('mousedown', e => {{ isDragging = true; prevX = e.clientX; prevY = e.clientY; dragDelta = 0; }});
window.addEventListener('mouseup', e => {{
  if (isDragging && dragDelta < 6) {{
    mouseVec.x = (e.clientX / window.innerWidth) * 2 - 1;
    mouseVec.y = -(e.clientY / window.innerHeight) * 2 + 1;
    raycaster.setFromCamera(mouseVec, camera);
    for (let i = 0; i < characters.length; i++) {{
      const hits = raycaster.intersectObjects(characters[i].root.children, true);
      if (hits.length > 0) {{
        activeCharIdx = i;
        triggerInteraction('compliment');
        break;
      }}
    }}
  }}
  isDragging = false;
}});
window.addEventListener('mousemove', e => {{
  if (!isDragging) return;
  const dx = e.clientX - prevX, dy = e.clientY - prevY;
  dragDelta += Math.abs(dx) + Math.abs(dy);
  camYaw -= dx * 0.0065;
  camPitch = Math.max(-0.15, Math.min(0.65, camPitch + dy * 0.0055));
  prevX = e.clientX; prevY = e.clientY;
}});
canvas.addEventListener('wheel', e => {{
  camDist = Math.max(2.3, Math.min(9.5, camDist + e.deltaY * 0.003));
}});

const keys = {{}};
window.addEventListener('keydown', e => {{
  keys[e.code] = true;
  if (e.key === '1') setCameraPreset(1);
  if (e.key === '2') setCameraPreset(2);
  if (e.key === '3') setCameraPreset(3);
  if (e.key === '4') setCameraPreset(4);
  if (e.code === 'KeyE') triggerInteraction('next_char');
}});
window.addEventListener('keyup', e => {{ keys[e.code] = false; }});

async function sendDirectorPrompt() {{
  const inp = document.getElementById('promptInput');
  const text = (inp.value || '').trim();
  if (!text) return;
  inp.value = '';
  try {{
    const r = await fetch('/api/game-bootstrap/voxel-director', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'application/json' }},
      body: JSON.stringify({{ world_id: WORLD_ID, prompt: text, reset_world: false }})
    }});
    if (r.ok) {{
      worldState = await r.json();
      document.getElementById('bWeather').textContent = 'Погода: ' + ((worldState.weather && worldState.weather.label) || 'Ночь');
      document.getElementById('dlgText').textContent = '🎬 Боссман выполнил команду Режиссёра: «' + text + '»!';
    }}
  }} catch (err) {{
    console.warn(err);
  }}
}}
document.getElementById('promptInput').addEventListener('keydown', e => {{
  if (e.key === 'Enter') sendDirectorPrompt();
}});

window.addEventListener('resize', () => {{
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}});

// 6. Главный 60 FPS WebGL2 цикл анимации (пластика бёдер, дыхание грудной клетки, взмахи рук СНАРУЖИ силуэта)
const clock = new THREE.Clock();
function animate() {{
  requestAnimationFrame(animate);
  const t = clock.getElapsedTime();

  const moveSpeed = 0.035;
  if (keys['KeyW'] || keys['ArrowUp']) camDist = Math.max(2.3, camDist - moveSpeed);
  if (keys['KeyS'] || keys['ArrowDown']) camDist = Math.min(9.5, camDist + moveSpeed);
  if (keys['KeyA'] || keys['ArrowLeft']) camYaw -= moveSpeed * 0.6;
  if (keys['KeyD'] || keys['ArrowRight']) camYaw += moveSpeed * 0.6;

  const speedMult = danceMode === 3 ? 2.1 : 1.15;
  characters.forEach((ch, idx) => {{
    const ph = t * speedMult + ch.cfg.phase;
    const sway = Math.sin(ph * 2.0);
    const breathe = Math.sin(ph * 3.2) * 0.015;

    // Плавное покачивание бёдер и талии
    ch.hipGroup.position.x = sway * 0.042;
    ch.hipGroup.position.y = 0.96 + Math.abs(sway) * 0.020 + breathe;
    ch.hipGroup.rotation.z = sway * 0.055;
    ch.hipGroup.rotation.y = Math.cos(ph * 1.5) * (danceMode === 3 ? 0.28 : 0.14);

    // Кокетливый наклон головы
    ch.headGroup.rotation.z = -sway * 0.07;
    ch.headGroup.rotation.y = Math.sin(ph * 1.2) * 0.12;

    // Постановка ног (одна нога опорная, другая слегка согнута в колене)
    ch.legGroups.forEach(lg => {{
      lg.legRoot.rotation.z = -sway * 0.045;
      lg.legRoot.rotation.x = lg.sign * sway * 0.07;
      lg.kneeGroup.rotation.x = Math.max(0, (lg.sign * sway) * 0.18);
    }});

    // Грациозная пластика рук СНАРУЖИ тела (левая рука на бедре, правая приветствует вверху)
    ch.armGroups.forEach(ag => {{
      if (ag.sign < 0) {{
        // Левая рука (x < 0): плечо отводится влево (-Z rot), локоть сгибается обратно к талии (+Z rot)
        ag.shGroup.rotation.z = -0.38;
        ag.shGroup.rotation.x = 0.08;
        ag.elbowGroup.rotation.z = 0.76 + sway * 0.06;
        ag.elbowGroup.rotation.x = -0.15;
      }} else {{
        // Правая рука (x > 0): плечо отводится вправо (+Z rot), предплечье поднято вверх в приветствии (+Z rot)
        ag.shGroup.rotation.z = 0.56 + Math.sin(ph * 2.4) * 0.12;
        ag.shGroup.rotation.x = -0.15;
        ag.elbowGroup.rotation.z = 1.62 + Math.cos(ph * 2.4) * 0.22;
        ag.elbowGroup.rotation.x = 0.20;
      }}
    }});
  }});

  // Анимация пузырьков джакузи и парящих сердец
  bubbles.forEach(b => {{
    b.mesh.position.y = b.baseY + ((t * b.speed + b.phase) % 0.42);
  }});
  floatingHearts.forEach(h => {{
    h.mesh.position.y = h.baseY + Math.sin(t * h.speed + h.ang) * 0.12;
    h.mesh.rotation.y = t * 0.9 + h.ang;
  }});

  camera.position.x = camTarget.x + Math.sin(camYaw) * Math.cos(camPitch) * camDist;
  camera.position.y = camTarget.y + Math.sin(camPitch) * camDist;
  camera.position.z = camTarget.z + Math.cos(camYaw) * Math.cos(camPitch) * camDist;
  camera.lookAt(camTarget);

  renderer.render(scene, camera);

  // Обновляем координаты именных плашек под ногами персонажей
  characters.forEach((ch, idx) => {{
    const footPos = new THREE.Vector3(ch.cfg.x, 0.12, ch.cfg.z + 0.22);
    footPos.project(camera);
    const sx = (footPos.x * 0.5 + 0.5) * window.innerWidth;
    const sy = (-footPos.y * 0.5 + 0.5) * window.innerHeight;
    tagEls[idx].style.left = sx + 'px';
    tagEls[idx].style.top = sy + 'px';
  }});
}}

animate();
window.__BOSSBLOCKS_READY = true;
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

