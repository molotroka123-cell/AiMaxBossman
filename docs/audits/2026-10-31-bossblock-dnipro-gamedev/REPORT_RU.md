# BossBlock (Aleph Game-Dev World Capture) — внедрение в `main` и захват центра г. Днепр, Украина (2026-10-31)

**Автор**: Аргон  
**Пакет**: [`apps/bossblock/`](../../../apps/bossblock/README.md) (`bossblock` / `python -m bossblock`)  
**Мобильный пульт (`:8840`)**: [`apps/bossman-mobile/bossmobile/api.py`](../../../apps/bossman-mobile/bossmobile/api.py), [`apps/bossman-mobile/bossmobile/ui/app.js`](../../../apps/bossman-mobile/bossmobile/ui/app.js)  
**Тесты**: [`tests/test_bossblock.py`](../../../tests/test_bossblock.py) (`3 passed`)

---

## 1. Что внедрено в `main`

1. **Пакет `apps/bossblock` (на базе MIT `belluxx/aleph`, адаптированный для Game Dev)**:
   - Захватывает реальный участок мира по координатам (`--bbox S W N E`) сразу в **5 слоях**:
     1. **3D Photogrammetry Mesh (`mesh.glb`)** — текстурированный 3D-меш Google Earth в формате binary glTF 2.0 (прямой импорт в Blender / Unreal Engine 5 / Unity / Godot).
     2. **HD Satellite Ortho (`satellite.png`, `satellite.tif`)** — геопривязанный ортофотоплан высокого разрешения (zoom 18, `~0.40 м/пикс`).
     3. **OSM Map + 3D Buildings GeoJSON (`map.osm`, `osm_buildings_roads.geojson`)** — контуры зданий с этажностью/высотой (`height_m`), граф дорог и парковые зоны.
     4. **Terrain Elevation DEM (`terrain.tif`)** — карта высот рельефа (16-бит GeoTIFF в метрах над уровнем моря).
     5. **Street View Roadside Textures (`streetview/photos/*.jpg` + `metadata.geojson`)** — панорамы и фасадные текстуры вдоль улиц.
   - **Ускорение OSM для игровых кварталов**: добавлен быстрый BBox-режим (`api.openstreetmap.org/api/0.6/map?bbox=...` с фоллбэком на Overpass API) для участков до `0.08°` (`~8×8 км`), чтобы скачивать квартал за **1.2 сек (`911 KB`)** вместо загрузки 700 МБ `.osm.pbf` всей страны.
   - **Игровой экспорт (`bossblock/game_export.py`)**: автоматически собирает `game_manifest.json`, `osm_buildings_roads.geojson` и 4-панельный HUD-рендер `bossblock_showcase.png` (Спутник + 3D-здания OSM + Изометрический 3D-меш `mesh.glb` и рельеф DEM + текстуры фасадов Street View).
2. **Интеграция в Мобильный пульт (`:8840`)**:
   - Эндпоинты `/m/api/bossblock/status`, `/m/api/bossblock/capture`, `/m/api/bossblock/captures/{id}/{artifact}`, `/m/api/bossblock/captures/{id}/send-pult`.
   - Карточка **«BossBlock — 3D Мир для Игр (Aleph)»** во вкладке **Статус**: пресеты (включая **«🇺🇦 Днепр — Центр (Европейская пл. / ЦУМ / пр. Яворницкого)»**), запуск захвата, скачивание `mesh.glb` / `GeoJSON` / `Terrain`, открытие 3D-дашборда `:8845` и отправка витрины в Telegram-пульт в 1 тап.

---

## 2. Доказательство: реальный 5-слойный захват центра г. Днепр, Украина

- **Run ID**: `aleph-20261031T143945Z-eft_2jpx`
- **Координаты (BBox)**: `48.4638°N, 35.0448°E .. 48.4658°N, 35.0480°E` (центр: `48.4648°N, 35.0464°E` — пр. Дмитрия Яворницкого, Европейская площадь, ЦУМ, Пассаж, ул. Центральная)
- **Размер игрового блока**: `235.9 м × 222.4 м` (`52 470.7 м²` / `5.25 га`)

| Слой | Статус | Метрики захвата (Днепр, центр) | Артефакты |
|---|---|---|---|
| **3D Photogrammetry Mesh** | `DONE` | `40 / 40` окто-узлов (уровень детализации `19`, окна/фасады), `187 716 байт` | `mesh.glb` |
| **Satellite Ortho** | `DONE` | `16 / 16` тайлов, zoom `18` (`0.40 м/пикс`) | `satellite.png` (`812 KB`), `satellite.tif` (`813 KB`) |
| **OSM Vector & 3D Footprints** | `DONE` | `67` зданий, `96` сегментов дорог, `23` зоны ландшафта | `map.osm` (`911 KB`), `osm_buildings_roads.geojson` (`81 KB`) |
| **Terrain DEM (Рельеф)** | `DONE` | `2 / 2` тайла, zoom `14`, высоты `57.6 м .. 105.4 м` (перепад `47.8 м`) | `terrain.tif` (`134 KB`) |
| **Street View (Текстуры улиц)** | `DONE` | `52 / 52` фото вдоль улиц (`26` точек съёмки, шаг `60 м`) | `streetview/photos/*.jpg`, `streetview/metadata.geojson` |
| **Game Dev HUD Showcase** | `DONE` | Сводная 4-панельная витрина `1600×1000` | `bossblock_showcase.png` (`941 KB`), `game_manifest.json` |
