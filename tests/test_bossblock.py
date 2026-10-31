"""Unit and live-artifact tests for BossBlock (Game World Capture · Aleph Engine)."""
from __future__ import annotations

import json
from pathlib import Path

from bossblock import capture, game_export, geo, osm


def test_bossblock_settings_and_fast_bbox():
    opts = capture.settings({"include": ["satellite", "osm", "terrain", "mesh"], "mesh_level": 19})
    assert opts["include"] == ["satellite", "osm", "terrain", "mesh"]
    assert opts["mesh_level"] == 19

    # Dnipro center bbox (~236m x 222m) qualifies for fast OSM BBox XML API
    dnipro_bbox = (48.4638, 35.0448, 48.4658, 35.0480)
    src = osm.Source.__new__(osm.Source)
    assert src._can_use_fast_bbox(dnipro_bbox) is True

    # Country-wide bbox falls back to Geofabrik PBF
    ukraine_bbox = (44.0, 22.0, 52.5, 40.2)
    assert src._can_use_fast_bbox(ukraine_bbox) is False


def test_bossblock_parse_osm_xml_bytes():
    sample_xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<osm version="0.6">
  <node id="101" lat="48.4645" lon="35.0460"/>
  <node id="102" lat="48.4650" lon="35.0470"/>
  <node id="103" lat="48.4648" lon="35.0465">
    <tag k="amenity" v="cafe"/>
    <tag k="name" v="Dnipro Cafe"/>
  </node>
  <way id="201">
    <nd ref="101"/>
    <nd ref="102"/>
    <tag k="highway" v="primary"/>
    <tag k="name" v="Yavornytskoho Ave"/>
  </way>
</osm>"""
    area = (48.4638, 35.0448, 48.4658, 35.0480)
    roads = osm._parse_osm_xml_bytes(sample_xml, area, poi=False)
    assert len(roads) == 1
    assert roads[0]["id"] == 201
    assert roads[0]["tags"]["name"] == "Yavornytskoho Ave"
    assert len(roads[0]["points"]) == 2

    pois = osm._parse_osm_xml_bytes(sample_xml, area, poi=True)
    assert any(p["type"] == "node" and p["tags"].get("name") == "Dnipro Cafe" for p in pois)


def test_bossblock_game_showcase_on_synthetic_folder(tmp_path: Path):
    from PIL import Image

    poly = geo.corners((48.4638, 35.0448, 48.4658, 35.0480))
    run = {
        "format": "aleph-python",
        "version": 6,
        "polygon": [list(p) for p in poly],
        "options": capture.settings({"include": ["satellite", "osm"]}),
        "started_at": "2026-10-31T14:39:45Z",
        "state": "complete",
        "stages": [
            {"mode": "satellite", "grid": {"count": 1, "zoom": 18, "width": 256, "height": 256}, "results": []},
            {"mode": "osm", "results": [{"filename": "map.osm"}]},
        ],
    }
    (tmp_path / "manifest.json").write_text(json.dumps(run), encoding="utf-8")
    Image.new("RGB", (512, 512), (40, 60, 80)).save(tmp_path / "satellite.png", format="PNG")
    (tmp_path / "map.osm").write_bytes(
        b'<?xml version="1.0" encoding="UTF-8"?><osm version="0.6">'
        b'<node id="1" lat="48.4640" lon="35.0450"/>'
        b'<node id="2" lat="48.4640" lon="35.0460"/>'
        b'<node id="3" lat="48.4650" lon="35.0460"/>'
        b'<node id="4" lat="48.4650" lon="35.0450"/>'
        b'<way id="10"><nd ref="1"/><nd ref="2"/><nd ref="3"/><nd ref="4"/><nd ref="1"/>'
        b'<tag k="building" v="yes"/><tag k="name" v="TsUM Dnipro"/></way></osm>'
    )

    gm = game_export.build_game_showcase(tmp_path, run, title_override="TEST DNIPRO BLOCK")
    assert gm["schema"] == "bossman.bossblock.game_manifest.v1"
    assert gm["osm_stats"]["buildings"] == 1
    assert (tmp_path / "bossblock_showcase.png").is_file()
    assert (tmp_path / "osm_buildings_roads.geojson").is_file()
    assert (tmp_path / "game_manifest.json").is_file()


def test_bossblock_gta_dnipro_miniapp_and_skill():
    repo_root = Path(__file__).resolve().parent.parent
    skill_md = repo_root / ".agents" / "skills" / "bossblock-gta-miniapp" / "SKILL.md"
    assert skill_md.is_file(), "Bossman skill bossblock-gta-miniapp/SKILL.md must exist"
    skill_text = skill_md.read_text(encoding="utf-8")
    assert "name: bossblock-gta-miniapp" in skill_text
    assert "/m/gta.html" in skill_text

    ui_dir = repo_root / "apps" / "bossman-mobile" / "bossmobile" / "ui"
    for fname in ("gta.html", "gta.css", "gta.js"):
        fpath = ui_dir / fname
        assert fpath.is_file(), f"Missing Mini App file: {fname}"
    gta_html = (ui_dir / "gta.html").read_text(encoding="utf-8")
    assert "style=" not in gta_html, "gta.html must not contain inline style attributes (strict CSP)"
    assert "GTA: Днепр 3D" in gta_html

    dnipro_dir = ui_dir / "dnipro_assets"
    for asset in ("game_manifest.json", "osm_buildings_roads.geojson", "satellite.png", "mesh.glb"):
        assert (dnipro_dir / asset).is_file(), f"Missing Dnipro asset: {asset}"

