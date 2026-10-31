"""Game-development export and visual HUD showcase for BossBlock captures."""

from __future__ import annotations

import json
import math
import struct
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import geo, layers
from .common import write_json


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ["C:/Windows/Fonts/segoeuib.ttf", "C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/consolab.ttf"]
        if bold
        else ["C:/Windows/Fonts/segoeui.ttf", "C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/consola.ttf"]
    )
    for path in candidates:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _parse_glb_vertices(glb_path: Path, max_tris: int = 12000) -> tuple[np.ndarray, np.ndarray] | None:
    """Extract vertex positions and triangle indices from a binary glTF 2.0 (mesh.glb) for isometric preview."""
    if not glb_path.is_file() or glb_path.stat().st_size < 28:
        return None
    try:
        data = glb_path.read_bytes()
        magic, version, length = struct.unpack_from("<4sII", data, 0)
        if magic != b"glTF" or version != 2:
            return None
        j_len, j_type = struct.unpack_from("<II", data, 12)
        if j_type != 0x4E4F534A:
            return None
        gltf = json.loads(data[20 : 20 + j_len].decode("utf-8"))
        b_offset = 20 + j_len
        if b_offset + 8 > len(data):
            return None
        b_len, b_type = struct.unpack_from("<II", data, b_offset)
        bin_blob = data[b_offset + 8 : b_offset + 8 + b_len]

        accessors = gltf.get("accessors") or []
        buffer_views = gltf.get("bufferViews") or []
        meshes = gltf.get("meshes") or []

        all_verts = []
        all_tris = []
        v_offset = 0

        for m in meshes:
            for prim in m.get("primitives") or []:
                pos_idx = (prim.get("attributes") or {}).get("POSITION")
                ind_idx = prim.get("indices")
                if pos_idx is None:
                    continue
                acc = accessors[pos_idx]
                bv = buffer_views[acc["bufferView"]]
                start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
                count = acc["count"]
                verts = np.frombuffer(bin_blob, dtype="<f4", count=count * 3, offset=start).reshape((count, 3))
                if ind_idx is not None:
                    iacc = accessors[ind_idx]
                    ibv = buffer_views[iacc["bufferView"]]
                    istart = ibv.get("byteOffset", 0) + iacc.get("byteOffset", 0)
                    icount = iacc["count"]
                    ctype = iacc["componentType"]
                    dt = "<u2" if ctype == 5123 else "<u4" if ctype == 5125 else "<u1"
                    idx = np.frombuffer(bin_blob, dtype=dt, count=icount, offset=istart).astype(np.int32)
                    tris = idx[: (icount // 3) * 3].reshape((-1, 3)) + v_offset
                else:
                    tris = np.arange(count, dtype=np.int32)[: (count // 3) * 3].reshape((-1, 3)) + v_offset
                all_verts.append(verts)
                all_tris.append(tris)
                v_offset += count
        if not all_verts:
            return None
        v_arr = np.vstack(all_verts)
        t_arr = np.vstack(all_tris)
        if len(t_arr) > max_tris:
            step = max(1, len(t_arr) // max_tris)
            t_arr = t_arr[::step]
        return v_arr, t_arr
    except Exception:
        return None


def _render_osm_panel(osm_fc: dict[str, Any], bounds: tuple[float, float, float, float], size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", (w, h), (18, 22, 31))
    draw = ImageDraw.Draw(img, "RGBA")
    south, west, north, east = bounds
    lat_span = max(1e-6, north - south)
    lon_span = max(1e-6, east - west)
    pad = 16

    def to_xy(lon: float, lat: float) -> tuple[float, float]:
        x = pad + ((lon - west) / lon_span) * (w - 2 * pad)
        y = pad + ((north - lat) / lat_span) * (h - 2 * pad)
        return (x, y)

    features = osm_fc.get("features") or []
    # 1. Land / parks / water
    for feat in features:
        props = feat.get("properties") or {}
        kind = props.get("kind")
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if kind in ("land", "water"):
            fill = (26, 62, 46, 180) if kind == "land" else (24, 78, 138, 210)
            rings = coords if gtype == "Polygon" else [r for poly in coords for r in poly] if gtype == "MultiPolygon" else []
            for ring in rings:
                pts = [to_xy(pt[0], pt[1]) for pt in ring]
                if len(pts) >= 3:
                    draw.polygon(pts, fill=fill)

    # 2. Roads
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("kind") != "road":
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        rclass = props.get("road")
        color = (245, 194, 66, 230) if rclass == "major" else (148, 163, 184, 200) if rclass == "minor" else (90, 105, 125, 150)
        width = 4 if rclass == "major" else 2 if rclass == "minor" else 1
        lines = [coords] if gtype == "LineString" else coords if gtype == "MultiLineString" else []
        for line in lines:
            pts = [to_xy(pt[0], pt[1]) for pt in line]
            if len(pts) >= 2:
                draw.line(pts, fill=color, width=width)

    # 3. Buildings with subtle 3D extrusion offset
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("kind") != "building":
            continue
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        rings = [coords[0]] if gtype == "Polygon" and coords else [poly[0] for poly in coords if poly] if gtype == "MultiPolygon" else []
        for ring in rings:
            pts = [to_xy(pt[0], pt[1]) for pt in ring]
            if len(pts) >= 3:
                shadow_pts = [(x + 3, y + 3) for x, y in pts]
                draw.polygon(shadow_pts, fill=(8, 10, 15, 190))
                draw.polygon(pts, fill=(217, 119, 64, 215), outline=(253, 186, 116, 240))

    return img


def _render_terrain_and_mesh_panel(
    folder: Path,
    run: dict[str, Any],
    size: tuple[int, int],
) -> tuple[Image.Image, dict[str, float]]:
    w, h = size
    img = Image.new("RGB", (w, h), (16, 20, 28))
    draw = ImageDraw.Draw(img, "RGBA")
    stats = {"min_elevation_m": 0.0, "max_elevation_m": 0.0, "relief_m": 0.0}

    # Decode terrain tiles if present
    terrain_stage = next((s for s in run.get("stages", []) if s.get("mode") == "terrain"), None)
    elev_arrays = []
    if terrain_stage:
        for res in terrain_stage.get("results") or []:
            fn = res.get("filename")
            if fn and (folder / fn).is_file():
                try:
                    with layers.heights(folder / fn) as h_img:
                        arr = np.asarray(h_img, dtype=np.float32)
                        valid = arr[arr > layers.NODATA / 2]
                        if valid.size:
                            elev_arrays.append(arr)
                except Exception:
                    continue

    # Check if 3D mesh (mesh.glb) is available
    glb_data = _parse_glb_vertices(folder / "mesh.glb")

    if elev_arrays:
        mosaic = elev_arrays[0]
        valid = mosaic[(mosaic > -50.0) & (mosaic < 8900.0)]
        if not valid.size:
            valid = mosaic[mosaic > layers.NODATA / 2]
        vmin = float(np.percentile(valid, 5)) if valid.size else 0.0
        vmax = float(np.percentile(valid, 98)) if valid.size else 1.0
        stats["min_elevation_m"] = round(vmin, 1)
        stats["max_elevation_m"] = round(vmax, 1)
        stats["relief_m"] = round(max(0.0, vmax - vmin), 1)

        clipped = np.clip(mosaic, vmin, vmax)
        norm = np.clip((clipped - vmin) / max(1.0, vmax - vmin), 0.0, 1.0)
        gy, gx = np.gradient(clipped)
        slope = np.clip(0.72 + (-gx * 0.6 - gy * 0.6) * 0.18, 0.30, 1.35)

        r = np.clip((38 + 185 * norm) * slope, 0, 255).astype(np.uint8)
        g = np.clip((105 + 110 * (1.0 - np.abs(norm - 0.45))) * slope, 0, 255).astype(np.uint8)
        b = np.clip((145 - 85 * norm) * slope, 0, 255).astype(np.uint8)
        dem_img = Image.fromarray(np.dstack([r, g, b]), "RGB").resize((w // 2 if glb_data else w, h), Image.Resampling.BILINEAR)
        img.paste(dem_img, (0, 0))

    if glb_data is not None:
        verts, tris = glb_data
        # Render isometric 3D photogrammetry mesh on the right half (or full panel)
        x0 = w // 2 if elev_arrays else 0
        mw = w - x0
        draw.rectangle([x0, 0, w, h], fill=(14, 18, 26))
        # Isometric projection: x east, y up, z south
        vx, vy, vz = verts[:, 0], verts[:, 1], verts[:, 2]
        angle = math.radians(35.0)
        tilt = math.radians(30.0)
        rx = vx * math.cos(angle) - vz * math.sin(angle)
        rz = vx * math.sin(angle) + vz * math.cos(angle)
        px = rx
        py = -vy * math.cos(tilt) + rz * math.sin(tilt)

        pmin_x, pmax_x = float(np.percentile(px, 2)), float(np.percentile(px, 98))
        pmin_y, pmax_y = float(np.percentile(py, 2)), float(np.percentile(py, 98))
        span = max(1.0, pmax_x - pmin_x, pmax_y - pmin_y)
        scale = min(mw - 24, h - 36) / span
        cx = x0 + mw / 2.0
        cy = h / 2.0 + 8
        sx = cx + (px - (pmin_x + pmax_x) / 2.0) * scale
        sy = cy + (py - (pmin_y + pmax_y) / 2.0) * scale

        # Sort triangles back-to-front by rz, shade by height vy
        tri_z = rz[tris].mean(axis=1)
        tri_y = vy[tris].mean(axis=1)
        ymin, ymax = float(vy.min()), float(vy.max())
        yspan = max(1.0, ymax - ymin)
        order = np.argsort(tri_z)
        for idx in order:
            i0, i1, i2 = tris[idx]
            h_norm = max(0.0, min(1.0, (float(tri_y[idx]) - ymin) / yspan))
            col = (
                int(55 + 165 * h_norm),
                int(130 + 95 * h_norm),
                int(205 - 60 * h_norm),
                195,
            )
            pts = [(float(sx[i0]), float(sy[i0])), (float(sx[i1]), float(sy[i1])), (float(sx[i2]), float(sy[i2]))]
            draw.polygon(pts, fill=col, outline=(255, 255, 255, 28))

    return img, stats


def build_game_showcase(folder: Path, run: dict[str, Any] | None = None, title_override: str | None = None) -> dict[str, Any]:
    """Generate osm_buildings_roads.geojson, game_manifest.json, and bossblock_showcase.png."""
    folder = Path(folder).resolve()
    if run is None:
        run = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))

    polygon = [tuple(pt) for pt in run["polygon"]]
    bounds = geo.extent(polygon)
    south, west, north, east = bounds
    center_lat = round((south + north) / 2.0, 6)
    center_lon = round((west + east) / 2.0, 6)
    width_m = round(geo.distance((center_lat, west), (center_lat, east)), 1)
    height_m = round(geo.distance((south, center_lon), (north, center_lon)), 1)
    area_m2, perimeter_m = geo.measure(polygon)

    # 1. OSM GeoJSON + counts
    osm_fc: dict[str, Any] = {"type": "FeatureCollection", "features": []}
    osm_counts = {"buildings": 0, "roads": 0, "water": 0, "land": 0}
    osm_path = folder / "map.osm"
    if osm_path.is_file():
        try:
            osm_fc = layers.osm(osm_path)
            write_json(folder / "osm_buildings_roads.geojson", osm_fc)
            for feat in osm_fc.get("features") or []:
                k = (feat.get("properties") or {}).get("kind")
                if k == "building":
                    osm_counts["buildings"] += 1
                elif k == "road":
                    osm_counts["roads"] += 1
                elif k == "water":
                    osm_counts["water"] += 1
                elif k == "land":
                    osm_counts["land"] += 1
        except Exception:
            pass

    # 2. StreetView stats
    sv_stage = next((s for s in run.get("stages", []) if s.get("mode") == "streetview"), None)
    sv_saved = [
        r for r in (sv_stage.get("results") or [] if sv_stage else [])
        if r.get("status") == "saved" and r.get("filename") and (folder / r["filename"]).is_file()
    ]

    # 3. 3D Mesh stats
    mesh_stage = next((s for s in run.get("stages", []) if s.get("mode") == "mesh"), None)
    glb_path = folder / "mesh.glb"
    glb_bytes = glb_path.stat().st_size if glb_path.is_file() else 0
    mesh_nodes_saved = sum(
        1 for r in (mesh_stage.get("results") or [] if mesh_stage else [])
        if r.get("filename")
    )

    # 4. Build 1600x1000 4-panel showcase PNG
    canvas_w, canvas_h = 1600, 1000
    canvas = Image.new("RGB", (canvas_w, canvas_h), (11, 15, 23))
    draw = ImageDraw.Draw(canvas, "RGBA")

    f_title = _load_font(26, bold=True)
    f_sub = _load_font(16, bold=False)
    f_badge = _load_font(14, bold=True)
    f_panel = _load_font(17, bold=True)
    f_small = _load_font(13, bold=False)

    # Header bar
    draw.rectangle([0, 0, canvas_w, 86], fill=(17, 24, 39))
    draw.line([(0, 86), (canvas_w, 86)], fill=(56, 189, 248), width=2)
    label_title = title_override or f"BOSSBLOCK · GAME WORLD CAPTURE ({center_lat:.4f}°N, {center_lon:.4f}°E)"
    draw.text((28, 16), label_title, fill=(248, 250, 252), font=f_title)
    subtitle = (
        f"Block: {width_m:.0f} m × {height_m:.0f} m ({area_m2 / 10000:.2f} ha)  |  "
        f"OSM Buildings: {osm_counts['buildings']} · Roads: {osm_counts['roads']}  |  "
        f"Street View: {len(sv_saved)} photos  |  "
        f"3D Mesh (mesh.glb): {glb_bytes / (1024 * 1024):.2f} MB ({mesh_nodes_saved} nodes)"
    )
    draw.text((28, 52), subtitle, fill=(148, 163, 184), font=f_sub)

    panel_w, panel_h = 764, 424
    positions = [
        (24, 104, "1. HD SATELLITE ORTHO (satellite.png / .tif)"),
        (812, 104, f"2. OSM GAME VECTOR MAP ({osm_counts['buildings']} buildings, {osm_counts['roads']} roads)"),
        (24, 552, "3. TERRAIN DEM (terrain.tif) + 3D PHOTOGRAMMETRY MESH (mesh.glb)"),
        (812, 552, f"4. STREET VIEW ROADSIDE TEXTURES ({len(sv_saved)} panoramas)"),
    ]

    # Panel 1: Satellite
    p1_x, p1_y, p1_lbl = positions[0]
    p1_img = Image.new("RGB", (panel_w, panel_h - 34), (20, 26, 36))
    sat_png = folder / "satellite.png"
    if sat_png.is_file():
        try:
            with Image.open(sat_png) as s_im:
                p1_img = s_im.convert("RGB").resize((panel_w, panel_h - 34), Image.Resampling.LANCZOS)
        except Exception:
            pass
    canvas.paste(p1_img, (p1_x, p1_y + 34))

    # Panel 2: OSM Vector Map
    p2_x, p2_y, p2_lbl = positions[1]
    p2_img = _render_osm_panel(osm_fc, bounds, (panel_w, panel_h - 34))
    canvas.paste(p2_img, (p2_x, p2_y + 34))

    # Panel 3: Terrain + 3D Mesh
    p3_x, p3_y, p3_lbl = positions[2]
    p3_img, terrain_stats = _render_terrain_and_mesh_panel(folder, run, (panel_w, panel_h - 34))
    canvas.paste(p3_img, (p3_x, p3_y + 34))
    if terrain_stats["max_elevation_m"] > 0 or glb_bytes > 0:
        draw.rectangle([p3_x + 10, p3_y + panel_h - 36, p3_x + panel_w - 10, p3_y + panel_h - 8], fill=(11, 15, 23, 210))
        info_txt = (
            f"Elevation: {terrain_stats['min_elevation_m']:.1f}m .. {terrain_stats['max_elevation_m']:.1f}m "
            f"(relief {terrain_stats['relief_m']:.1f}m)  |  "
            f"glTF 2.0 mesh.glb: {glb_bytes / 1024:.0f} KB"
        )
        draw.text((p3_x + 18, p3_y + panel_h - 31), info_txt, fill=(125, 211, 252), font=f_small)

    # Panel 4: Street View Grid (up to 4 photos)
    p4_x, p4_y, p4_lbl = positions[3]
    p4_body_h = panel_h - 34
    p4_img = Image.new("RGB", (panel_w, p4_body_h), (18, 22, 31))
    p4_draw = ImageDraw.Draw(p4_img, "RGBA")
    if sv_saved:
        cell_w, cell_h = panel_w // 2, p4_body_h // 2
        idxs = np.linspace(0, len(sv_saved) - 1, min(4, len(sv_saved)), dtype=int)
        chosen = [sv_saved[int(i)] for i in idxs]
        for idx, item in enumerate(chosen):
            cx = (idx % 2) * cell_w
            cy = (idx // 2) * cell_h
            try:
                with Image.open(folder / item["filename"]) as sv_im:
                    thumb = sv_im.convert("RGB").resize((cell_w - 4, cell_h - 4), Image.Resampling.LANCZOS)
                    p4_img.paste(thumb, (cx + 2, cy + 2))
                caption = f"{item.get('path_name') or item.get('highway') or 'Street'} · {int(item.get('heading', 0))}° ({item.get('imagery_date') or 'SV'})"
                p4_draw.rectangle([cx + 4, cy + cell_h - 26, cx + cell_w - 4, cy + cell_h - 4], fill=(11, 15, 23, 205))
                p4_draw.text((cx + 10, cy + cell_h - 23), caption[:42], fill=(241, 245, 249), font=f_small)
            except Exception:
                continue
    canvas.paste(p4_img, (p4_x, p4_y + 34))

    # Draw panel headers and borders
    for px, py, plbl in positions:
        draw.rectangle([px, py, px + panel_w, py + 34], fill=(30, 41, 59))
        draw.text((px + 12, py + 7), plbl, fill=(56, 189, 248), font=f_panel)
        draw.rectangle([px, py, px + panel_w, py + panel_h], outline=(51, 65, 85), width=2)

    showcase_path = folder / "bossblock_showcase.png"
    canvas.save(showcase_path, format="PNG")

    manifest_out = {
        "schema": "bossman.bossblock.game_manifest.v1",
        "folder": str(folder),
        "title": label_title,
        "center": {"lat": center_lat, "lon": center_lon},
        "bounds": {"south": south, "west": west, "north": north, "east": east},
        "dimensions_m": {
            "width_m": width_m,
            "height_m": height_m,
            "area_m2": round(area_m2, 1),
            "perimeter_m": round(perimeter_m, 1),
        },
        "osm_stats": osm_counts,
        "terrain_stats": terrain_stats,
        "streetview_stats": {
            "saved_photos": len(sv_saved),
            "sample_files": [r["filename"] for r in sv_saved[:6]],
        },
        "mesh_stats": {
            "glb_exists": glb_path.is_file(),
            "glb_bytes": glb_bytes,
            "nodes_saved": mesh_nodes_saved,
        },
        "artifacts": {
            "showcase_png": str(showcase_path),
            "satellite_png": str(sat_png) if sat_png.is_file() else None,
            "satellite_tif": str(folder / "satellite.tif") if (folder / "satellite.tif").is_file() else None,
            "terrain_tif": str(folder / "terrain.tif") if (folder / "terrain.tif").is_file() else None,
            "map_osm": str(osm_path) if osm_path.is_file() else None,
            "osm_geojson": str(folder / "osm_buildings_roads.geojson") if (folder / "osm_buildings_roads.geojson").is_file() else None,
            "mesh_glb": str(glb_path) if glb_path.is_file() else None,
        },
    }
    write_json(folder / "game_manifest.json", manifest_out)
    return manifest_out
