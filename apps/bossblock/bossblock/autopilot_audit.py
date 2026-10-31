"""Automated 5-layer quality and game-engine readiness audit for BossBlock captures.

Verifies all 5 Aleph Game-Dev capture layers (3D glTF 2.0 mesh, HD satellite ortho,
OSM buildings/roads GeoJSON, Terrain DEM, Street View facade textures) and builds a
cryptographic readiness receipt (`world_readiness_receipt.json`) suitable for
Bossman Autopilot, Blender/UE5/Unity/Godot pipelines, and the GTA: Dnipro 3D Mini App.
"""
from __future__ import annotations

import hashlib
import json
import struct
import time
from pathlib import Path
from typing import Any


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _inspect_glb_header(path: Path) -> dict[str, Any]:
    """Validate binary glTF 2.0 header and extract scene/node/mesh counts without external deps."""
    if not path.is_file():
        return {"present": False, "valid_glb": False, "bytes": 0}
    size = path.stat().st_size
    if size < 20:
        return {"present": True, "valid_glb": False, "bytes": size, "reason": "file smaller than 20-byte GLB header"}
    with path.open("rb") as f:
        header = f.read(20)
        magic, version, total_len, chunk_len, chunk_type = struct.unpack("<4sIIII", header)
        if magic != b"glTF" or version != 2:
            return {"present": True, "valid_glb": False, "bytes": size, "reason": f"invalid magic/version: {magic!r} v{version}"}
        if chunk_type != 0x4E4F534A:
            return {"present": True, "valid_glb": False, "bytes": size, "reason": f"first chunk is not JSON: 0x{chunk_type:08x}"}
        if size < total_len or size < 20 + chunk_len:
            return {
                "present": True,
                "valid_glb": False,
                "bytes": size,
                "reason": f"truncated GLB: size={size} < declared={max(total_len, 20 + chunk_len)}",
            }
        json_bytes = f.read(min(chunk_len, 16 * 1024 * 1024))
    try:
        gltf = json.loads(json_bytes.decode("utf-8", errors="replace").rstrip("\x00 "))
    except Exception as exc:
        return {"present": True, "valid_glb": False, "bytes": size, "reason": f"corrupt JSON chunk: {exc}"}
    return {
        "present": True,
        "valid_glb": True,
        "bytes": size,
        "sha256": _sha256_file(path),
        "version": version,
        "declared_length": total_len,
        "nodes_count": len(gltf.get("nodes") or []),
        "meshes_count": len(gltf.get("meshes") or []),
        "materials_count": len(gltf.get("materials") or []),
        "textures_count": len(gltf.get("textures") or []),
    }


def audit_capture_folder(folder: Path, *, write_receipt: bool = True) -> dict[str, Any]:
    """Audit a BossBlock capture folder or 3D Mini App asset directory and return a readiness receipt."""
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"Capture folder does not exist: {folder}")

    gm_path = folder / "game_manifest.json"
    manifest_path = folder / "manifest.json"
    geojson_path = folder / "osm_buildings_roads.geojson"
    sat_png_path = folder / "satellite.png"
    sat_tif_path = folder / "satellite.tif"
    terrain_path = folder / "terrain.tif"
    mesh_path = folder / "mesh.glb"
    showcase_path = folder / "bossblock_showcase.png"

    gm: dict[str, Any] = {}
    if gm_path.is_file():
        try:
            gm = json.loads(gm_path.read_text(encoding="utf-8"))
        except Exception:
            gm = {}

    # 1. Layer: 3D Photogrammetry Mesh (mesh.glb)
    mesh_info = _inspect_glb_header(mesh_path)
    mesh_info["octree_nodes_saved"] = int((gm.get("mesh_stats") or {}).get("nodes_saved") or 0)

    # 2. Layer: Satellite Ortho
    sat_info: dict[str, Any] = {
        "present": sat_png_path.is_file() or sat_tif_path.is_file(),
        "png_present": sat_png_path.is_file(),
        "tif_present": sat_tif_path.is_file(),
        "png_bytes": sat_png_path.stat().st_size if sat_png_path.is_file() else 0,
        "png_sha256": _sha256_file(sat_png_path) if sat_png_path.is_file() else "",
    }

    # 3. Layer: OSM 3D Buildings & Roads GeoJSON
    buildings = 0
    roads = 0
    landuse = 0
    collision_proxies: list[dict[str, Any]] = []
    if geojson_path.is_file():
        try:
            gj = json.loads(geojson_path.read_text(encoding="utf-8"))
            for feat in gj.get("features") or []:
                props = feat.get("properties") or {}
                layer = props.get("layer")
                if layer == "building":
                    buildings += 1
                    geom = feat.get("geometry") or {}
                    coords = (geom.get("coordinates") or [[]])[0]
                    if coords and len(collision_proxies) < 32:
                        lats = [float(c[1]) for c in coords if len(c) >= 2]
                        lons = [float(c[0]) for c in coords if len(c) >= 2]
                        if lats and lons:
                            collision_proxies.append({
                                "id": props.get("id") or f"bldg-{buildings}",
                                "name": props.get("name") or "",
                                "height_m": float(props.get("height_m") or 12.0),
                                "bbox": [min(lats), min(lons), max(lats), max(lons)],
                            })
                elif layer == "road":
                    roads += 1
                elif layer == "landuse":
                    landuse += 1
        except Exception:
            pass
    osm_info = {
        "present": geojson_path.is_file() or (folder / "map.osm").is_file(),
        "geojson_present": geojson_path.is_file(),
        "geojson_bytes": geojson_path.stat().st_size if geojson_path.is_file() else 0,
        "geojson_sha256": _sha256_file(geojson_path) if geojson_path.is_file() else "",
        "buildings": buildings or int((gm.get("osm_stats") or {}).get("buildings") or 0),
        "roads": roads or int((gm.get("osm_stats") or {}).get("roads") or 0),
        "landuse": landuse or int((gm.get("osm_stats") or {}).get("landuse") or 0),
        "collision_proxies_sampled": len(collision_proxies),
    }

    # 4. Layer: Terrain DEM
    terrain_stats = gm.get("terrain_stats") or {}
    terrain_info = {
        "present": terrain_path.is_file() or bool(terrain_stats.get("present")),
        "tif_file_present": terrain_path.is_file(),
        "bytes": terrain_path.stat().st_size if terrain_path.is_file() else 0,
        "min_elevation_m": terrain_stats.get("min_elevation_m"),
        "max_elevation_m": terrain_stats.get("max_elevation_m"),
        "relief_m": terrain_stats.get("relief_m"),
    }

    # 5. Layer: Street View Facade Textures
    sv_dir = folder / "streetview" / "photos"
    sv_files = sorted(sv_dir.glob("*.jpg")) + sorted(sv_dir.glob("*.png")) if sv_dir.is_dir() else []
    sv_meta_path = folder / "streetview" / "photos.geojson"
    if not sv_meta_path.is_file():
        sv_meta_path = folder / "streetview" / "metadata.geojson"
    sv_info = {
        "present": len(sv_files) > 0,
        "photos_count": len(sv_files),
        "total_bytes": sum(p.stat().st_size for p in sv_files),
        "metadata_present": sv_meta_path.is_file(),
        "sample_files": [p.name for p in sv_files[:5]],
    }

    layers_map = {
        "mesh_3d": bool(mesh_info.get("valid_glb")),
        "satellite_ortho": bool(sat_info.get("present")),
        "osm_buildings_roads": bool(osm_info.get("present") and (osm_info["buildings"] > 0 or osm_info["roads"] > 0)),
        "terrain_dem": bool(terrain_info.get("present")),
        "streetview_textures": bool(sv_info.get("present")),
    }
    ready_layers_count = sum(1 for v in layers_map.values() if v)
    core_3d_ready = bool(
        layers_map["mesh_3d"] and layers_map["satellite_ortho"] and layers_map["osm_buildings_roads"]
    )
    verdict = "READY_5_OF_5" if ready_layers_count == 5 else ("READY_CORE_3D" if core_3d_ready else "INCOMPLETE")

    receipt: dict[str, Any] = {
        "schema": "bossman.bossblock.world_readiness.v1",
        "folder": str(folder),
        "title": gm.get("title") or folder.name,
        "audited_at": time.time(),
        "verdict": verdict,
        "ready_layers_count": ready_layers_count,
        "total_layers": 5,
        "core_3d_ready": core_3d_ready,
        "layers_status": layers_map,
        "bounds": gm.get("bounds") or {},
        "dimensions_m": gm.get("dimensions_m") or {},
        "layers": {
            "mesh": mesh_info,
            "satellite": sat_info,
            "osm": osm_info,
            "terrain": terrain_info,
            "streetview": sv_info,
        },
        "showcase_present": showcase_path.is_file(),
        "manifest_present": manifest_path.is_file() or gm_path.is_file(),
        "collision_proxies_preview": collision_proxies[:8],
    }
    digest_payload = json.dumps(
        {k: v for k, v in receipt.items() if k != "audited_at"},
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")
    receipt["receipt_sha256"] = hashlib.sha256(digest_payload).hexdigest()

    if write_receipt:
        out_file = folder / "world_readiness_receipt.json"
        out_file.write_text(json.dumps(receipt, indent=2, ensure_ascii=False), encoding="utf-8")
    return receipt
