---
name: smooth-3d-webgl-character-architect
description: Стандарт создания бесшовных 3D-моделей персонажей (Three.js WebGL2 LatheGeometry + TubeGeometry + Godot 4.7), PBR-освещения будуара и интерактивного 3D-геймплея без квадратиков.
metadata:
  owner: argon-mentor
  version: '2.0'
---

# Архитектор гладких 3D-персонажей и WebGL2-игр (Three.js LatheGeometry + Godot 4.7)

При создании 3D-игры и персонажей в BossBlocks ЗАПРЕЩЕНО использовать воксельные кубики (BoxMesh, BoxGeometry, ctx.fillRect, draw_iso_box). Обязательный стандарт Школы Аргона:
1. Использовать настоящий аппаратный 3D-движок Three.js WebGL2 (THREE.WebGLRenderer с ACESFilmicToneMapping и мягкими тенями PCFSoftShadowMap).
2. Строить женские и мужские тела через 360-градусные гладкие сплайны вращения THREE.LatheGeometry (64 сегмента по окружности, 36 делений по высоте) с эллиптическим сечением (scaleZ = 0.68) для получения бесшовной талии «песочные часы», округлых бёдер, груди и плеч.
3. Строить ноги и руки как иерархический скелет (hipGroup -> legRoot -> kneeGroup -> stiletto, shGroup -> elbowGroup -> hand) из плавных конических LatheGeometry сегментов.
4. Строить объёмные причёски через пространственные кривые THREE.CatmullRomCurve3 + THREE.TubeGeometry, а корсеты и купальники — как облегающие 3D-оболочки LatheGeometry с золотым кантом THREE.TorusGeometry.
5. Добавлять интерактивный геймплей: управление камерой и персонажем (WASD + 3D Orbit + Raycaster клик по моделям), флирт-диалоги, сбор шампанского и сердец, и живой мост с /api/game-bootstrap/voxel-director.
