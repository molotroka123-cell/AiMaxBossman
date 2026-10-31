# BossBlock — 3D Мир и Геоданные для Разработки Игр (Aleph Engine)

**BossBlock (`bossblock`)** — встроенный в Bossman инструмент захвата реальных городских кварталов для игровых движков (Unreal Engine, Unity, Godot, Blender) и LLM-агентов на базе открытого ядра [Belluxx/Aleph](https://github.com/Belluxx/Aleph) (MIT).

## Что выгружает за один прогон (`capture create`)

1. **HD Satellite Ortho (`satellite.png`, `satellite.tif`)** — бесшовная сшитая спутниковая ортофотокарта (Cloud-Optimized GeoTIFF в `EPSG:3857` + полноразмерный `PNG`).
2. **OSM Карта + Векторные Здания и Дороги (`map.osm`, `osm_buildings_roads.geojson`)** — контуры зданий, дорожная сеть, парки и водоёмы (с быстрым BBox-извлечением через OpenStreetMap API / Overpass для игровых кварталов без скачивания многогигабайтных PBF-архивов страны).
3. **Рельеф высот (`terrain.tif`)** — цифровая модель рельефа (DEM в метрах, AWS Terrain Tiles).
4. **Текстурированный 3D-меш (`mesh.glb`)** — реальная 3D-фотограмметрия квартала в формате binary glTF 2.0 (координаты в метрах от центра блока: `x` восток, `y` вверх, `z` юг).
5. **Уличные панорамы (`streetview/photos/*.jpg`, `streetview/photos.geojson`)** — текстуры фасадов с привязкой к дорожному графу OSM.
6. **Игровая сводка и витрина (`game_manifest.json`, `bossblock_showcase.png`)** — сводный 4-панельный рендер всех слоёв и метрик квартала.

## Примеры CLI

```bash
# Захват центра г. Днепр, Украина (Европейская площадь / пр. Д. Яворницкого) со всеми 5 слоями:
python -m bossblock capture create --bbox 48.4638 35.0448 48.4658 35.0480 \
  --sources streetview satellite osm terrain mesh \
  --step 60 --satellite-zoom 18 --terrain-zoom 14 --mesh-level 19 \
  --yes --json -o captures

# Запуск интерактивного 3D-дашборда (карта + 3D Mesh + рельеф):
python -m bossblock dashboard -o captures --port 8845
```
