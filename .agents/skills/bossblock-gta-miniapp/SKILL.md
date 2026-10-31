---
name: bossblock-gta-miniapp
description: Build and update playable 3D GTA-style open-world WebGL / Three.js Mini Apps for Telegram and Bossman Mobile Pult (:8840) from BossBlock 5-layer city captures (Satellite, OSM 3D Buildings & Roads, Terrain DEM, 3D Mesh .glb, Street View Panoramas) with rigged GLB characters, custom 3D sneakers/outfits, Draco vehicles, and live Aura Text & Texture testing.
---

# BossBlock 3D GTA Mini App Skill (`bossblock-gta-miniapp`)

Use this skill whenever the user asks Bossman to build, customize, or update a playable 3D open-world / GTA-style mobile game or Telegram Mini App on top of a real city captured by **BossBlock (`apps/bossblock`)**.

## 1. Data Inputs from BossBlock Capture
Each capture folder in `%LOCALAPPDATA%\Bossman\CommandCenter\bossblock-captures\<capture_id>` (and mirrored in `apps/bossman-mobile/bossmobile/ui/dnipro_assets/`) provides:
1. `game_manifest.json` — bounding box `[south, west, north, east]`, metric dimensions `width_m × height_m`, OSM building/road counts, elevation range, Street View photo count, and `mesh.glb` stats.
2. `satellite.png` — high-resolution stitched satellite orthophoto of the city block.
3. `osm_buildings_roads.geojson` — GeoJSON `Polygon` features for real named buildings (`name`, `levels`, `height`) and `LineString` features for roads (`name`, `highway`).
4. `mesh.glb` — 3D photogrammetry / relief glTF binary mesh.
5. `streetview/photos.geojson` + `streetview/photos/*.jpg` — real street-level panoramas with exact `[lon, lat]`, `filename`, and `heading`.

## 2. Critical 3D GTA Quality Rules (Mandatory Lessons Learned)
1. **Real Rigged 3D Characters & High-Poly 3D Vehicles (Never Primitive Boxes/Cones)**:
   - Load `vendor/models/Soldier.glb` with `THREE.GLTFLoader` and animate with `THREE.AnimationMixer` (`Idle`, `Walk`, `Run` clips crossfaded via `.fadeIn(0.18)` / `.fadeOut(0.18)`).
   - Load `vendor/models/ferrari.glb` (Ferrari 458 Italia) for the player's driveable GTA supercar and `vendor/models/CesiumMilkTruck.glb` + city trams for moving AI traffic.
2. **Mixamo Bone-Space Scale for 3D Sneakers (`Обувь`) & Accessories**:
   - In Mixamo-rigged `.glb` models (`Soldier.glb`), the `Character` root node has `scale = 0.01`, meaning bone-local coordinates (`mixamorig:LeftFoot`, `mixamorig:RightFoot`) are in **centimeters**, not meters.
   - Any 3D sneaker or accessory parented to `mixamorig:LeftFoot` / `mixamorig:RightFoot` must use centimeter dimensions (e.g. `new THREE.BoxGeometry(13, 5.5, 30)`).
3. **World-Meter UV Scaling on `THREE.ExtrudeGeometry` Building Walls**:
   - `THREE.ExtrudeGeometry` generates wall UVs in **world meters** (not `0..1`).
   - Always set `texture.wrapS = texture.wrapT = THREE.RepeatWrapping` and `texture.repeat.set(0.055, 0.055)` on building facade textures (both procedural multi-story facades and projected Street View photos) so each story renders at realistic `~3.5m` height instead of repeating every `1m`.
4. **Strict CSP Compliance on `:8840` (`default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data: blob:`)**:
   - Zero inline `<script>` or `style="..."` attributes in `/m/gta.html`.
   - Set `window.createImageBitmap = undefined` before loading GLBs so `GLTFLoader` uses `THREE.TextureLoader` (`<img>`, permitted for `blob:`) rather than `ImageBitmapLoader` (`fetch(blob:...)`, blocked by `connect-src 'self'`).
   - Load `vendor/draco/draco_decoder.js` via `<script>` and use `DRACOLoader._createInProcessWorker()` so Draco-compressed models (`ferrari.glb`) decode on the main thread without spawning `blob:` WebWorkers.
5. **Unobtrusive GTA V Mobile HUD**:
   - Keep customization drawers (`#auraDrawer`, `#streetModal`) closed by default (`class="... hidden"`) so 88%+ of the mobile viewport shows the 3D city, character, and car.
   - Use `Pointer Events` (`pointerdown`, `pointermove`, `pointerup`, `pointercancel`) with `setPointerCapture` and `touch-action: none` on the canvas thumbstick.
