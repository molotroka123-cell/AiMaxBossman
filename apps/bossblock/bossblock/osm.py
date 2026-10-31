"""Download OpenStreetMap data via fast BBox XML API (for game blocks <= 0.1 deg) or Geofabrik PBF regions."""

import hashlib
import json
from collections import defaultdict
from contextlib import nullcontext
from datetime import datetime, timezone
from itertools import pairwise
from xml.etree import ElementTree as ET

from .common import APP_AGENT, RequestError, atomic_path, write_bytes
from .geo import clip, extent, ring_contains, signed_area
from .pbf import PBF

INDEX = "https://download.geofabrik.de/index-v1.json"
POI_KEYS = ("amenity", "tourism", "shop", "leisure", "historic", "office")
BBOX_APIS = (
    "https://api.openstreetmap.org/api/0.6/map?bbox={west:.6f},{south:.6f},{east:.6f},{north:.6f}",
    "https://overpass-api.de/api/map?bbox={west:.6f},{south:.6f},{east:.6f},{north:.6f}",
    "https://overpass.kumi.systems/api/map?bbox={west:.6f},{south:.6f},{east:.6f},{north:.6f}",
)
FAST_BBOX_MAX_DEG = 0.08


def polygons(geometry):
    return [geometry["coordinates"]] if geometry["type"] == "Polygon" else geometry["coordinates"]


def covers(geometry, area):
    """Require the whole rectangle, including its interior, to lie in the region."""
    south, west, north, east = area
    shapes = polygons(geometry)
    for point in ((west, south), (west, north), (east, south), (east, north)):
        if not any(ring_contains(polygon[0], point) and not any(ring_contains(hole, point) for hole in polygon[1:])
                   for polygon in shapes):
            return False
    for polygon in shapes:
        for ring in polygon:
            for a, b in pairwise(ring):
                segment = clip((a[1], a[0]), (b[1], b[0]), area)
                if segment:
                    lat = (segment[0][0] + segment[1][0]) / 2
                    lon = (segment[0][1] + segment[1][1]) / 2
                    if south < lat < north and west < lon < east:
                        return False
    return True


def region_size(feature):
    return sum(abs(signed_area(polygon[0])) for polygon in polygons(feature["geometry"]))


def _parse_osm_xml_bytes(xml_bytes: bytes, area, *, poi: bool = False):
    """Parse an OSM XML snapshot into the exact items list returned by PBF.read()."""
    south, west, north, east = area
    root = ET.fromstring(xml_bytes)
    nodes = {}
    node_tags = {}
    ways = []
    relations = []
    keys = POI_KEYS if poi else ("highway",)

    for el in root:
        if el.tag == "node":
            nid = int(el.attrib["id"])
            lat = float(el.attrib["lat"])
            lon = float(el.attrib["lon"])
            nodes[nid] = (lat, lon)
            if poi:
                tags = {t.attrib["k"]: t.attrib["v"] for t in el.findall("tag")}
                if tags and any(k in tags for k in keys) and (south <= lat <= north and west <= lon <= east):
                    node_tags[nid] = tags
        elif el.tag == "way":
            wid = int(el.attrib["id"])
            tags = {t.attrib["k"]: t.attrib["v"] for t in el.findall("tag")}
            if any(k in tags for k in keys):
                refs = [int(nd.attrib["ref"]) for nd in el.findall("nd")]
                ways.append((wid, tags, refs))
        elif el.tag == "relation" and poi:
            rid = int(el.attrib["id"])
            tags = {t.attrib["k"]: t.attrib["v"] for t in el.findall("tag")}
            if any(k in tags for k in keys):
                members = [
                    (m.attrib.get("type", ""), int(m.attrib["ref"]))
                    for m in el.findall("member")
                    if m.attrib.get("type") in ("node", "way", "relation") and "ref" in m.attrib
                ]
                relations.append((rid, tags, members))

    items = []
    boxes = {}
    if poi:
        for nid, (lat, lon) in nodes.items():
            boxes[("node", nid)] = (lat, lon, lat, lon)
            if nid in node_tags:
                items.append(dict(type="node", id=nid, tags=node_tags[nid], center=(lat, lon)))

    for wid, tags, refs in ways:
        pts = [nodes[r] for r in refs if r in nodes]
        valid_refs = [r for r in refs if r in nodes]
        if len(pts) < 2:
            continue
        item = dict(type="way", id=wid, tags=tags, nodes=valid_refs, points=pts)
        if poi:
            lats, lons = zip(*pts)
            box = (min(lats), min(lons), max(lats), max(lons))
            boxes[("way", wid)] = box
            item["center"] = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
        items.append(item)

    if poi and relations:
        for rid, tags, members in relations:
            mboxes = [boxes[m] for m in members if m in boxes]
            center = None
            if mboxes:
                b0 = min(b[0] for b in mboxes)
                b1 = min(b[1] for b in mboxes)
                b2 = max(b[2] for b in mboxes)
                b3 = max(b[3] for b in mboxes)
                center = ((b0 + b2) / 2, (b1 + b3) / 2)
            items.append(dict(type="relation", id=rid, tags=tags, members=members, center=center))

    return items


class Source:
    def __init__(self, client):
        self.client = client
        self.directory = client.cache_dir / "geofabrik"
        self.bbox_dir = client.cache_dir / "osm_bbox"
        self.index = None
        self.regions = {}
        self.bbox_cache = {}

    def _can_use_fast_bbox(self, area) -> bool:
        south, west, north, east = area
        return (north - south) <= FAST_BBOX_MAX_DEG and (east - west) <= FAST_BBOX_MAX_DEG

    def _fetch_fast_bbox(self, area, progress):
        south, west, north, east = area
        # Round slightly outward so nearby calls for the same block reuse one cached XML
        pad = 0.0005
        s, w, n, e = round(south - pad, 4), round(west - pad, 4), round(north + pad, 4), round(east + pad, 4)
        key = f"{s:.4f}_{w:.4f}_{n:.4f}_{e:.4f}"
        if key in self.bbox_cache and not self.client.refresh:
            return self.bbox_cache[key]
        self.bbox_dir.mkdir(parents=True, exist_ok=True)
        cache_file = self.bbox_dir / f"bbox_{hashlib.sha256(key.encode()).hexdigest()[:16]}.osm"
        if cache_file.is_file() and not self.client.refresh:
            xml_bytes = cache_file.read_bytes()
            meta = dict(
                source_url=f"https://api.openstreetmap.org/api/0.6/map?bbox={w},{s},{e},{n}",
                region="osm-bbox-api",
                osm_data_at=datetime.fromtimestamp(cache_file.stat().st_mtime, timezone.utc).isoformat(timespec="seconds"),
            )
            self.bbox_cache[key] = (xml_bytes, meta)
            return xml_bytes, meta

        progress("Downloading OSM block XML")
        last_err = None
        for tpl in BBOX_APIS:
            addr = tpl.format(south=s, west=w, north=n, east=e)
            try:
                xml_bytes = self.client.get(addr, timeout=45, user_agent=APP_AGENT)
                if b"<osm" in xml_bytes[:512]:
                    write_bytes(cache_file, xml_bytes)
                    meta = dict(
                        source_url=addr,
                        region="osm-bbox-api",
                        osm_data_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    )
                    self.bbox_cache[key] = (xml_bytes, meta)
                    return xml_bytes, meta
            except Exception as exc:
                last_err = exc
                continue
        raise OSError(f"OSM BBox API failed: {last_err}")

    def region(self, area, progress):
        if self.index is None:
            path = self.directory / "index.json"
            refresh = self.client.refresh or not path.is_file()
            if refresh:
                progress("Finding Geofabrik region")
                data = self.client.get(INDEX, user_agent=APP_AGENT)
            else:
                data = path.read_bytes()
            index = json.loads(data)
            if not isinstance(index.get("features"), list):
                raise OSError("Geofabrik returned an invalid region index.")
            if refresh:
                write_bytes(path, data)
            self.index = sorted(index["features"], key=region_size)
        feature = next((f for f in self.index if f["properties"].get("urls", {}).get("pbf")
                        and covers(f["geometry"], area)), None)
        if feature is None:
            raise OSError("No Geofabrik region covers this rectangle. Select a smaller area.")
        props = feature["properties"]
        url = props["urls"]["pbf"]
        if url not in self.regions:
            path = self.directory / (hashlib.sha256(url.encode()).hexdigest()[:16] + ".osm.pbf")
            refresh = self.client.refresh or not path.is_file()
            with (atomic_path(path) if refresh else nullcontext(path)) as local:
                if refresh:
                    label = f"Downloading Geofabrik {props['name']}"
                    progress(label, 0, None, "bytes")
                    self.client.get(url, destination=local, timeout=180, user_agent=APP_AGENT,
                                    progress=lambda done, total: progress(label, done, total, "bytes"))
                stamp = PBF(local, self.client.check_cancel).timestamp
            self.regions[url] = path, dict(source_url=url, region=props["name"], osm_data_at=stamp or None)
        return self.regions[url]

    def export(self, polygon, output, progress):
        area = extent(polygon)
        if self._can_use_fast_bbox(area):
            xml_bytes, metadata = self._fetch_fast_bbox(area, progress)
            progress("Saving OSM map")
            write_bytes(output, xml_bytes)
            return metadata
        source, metadata = self.region(area, progress)
        progress("Extracting OSM map")
        PBF(source, self.client.check_cancel).export(polygon, output)
        return metadata

    def data(self, area, *, poi=False, progress=lambda *args: None):
        if self._can_use_fast_bbox(area):
            xml_bytes, metadata = self._fetch_fast_bbox(area, progress)
            progress("Reading nearby places" if poi else "Reading roads")
            return _parse_osm_xml_bytes(xml_bytes, area, poi=poi), metadata
        source, metadata = self.region(area, progress)
        progress("Reading nearby places" if poi else "Reading roads")
        pbf = PBF(source, self.client.check_cancel)
        local = pbf.select(area)
        selected = (set(), set(), set())
        keys = POI_KEYS if poi else ("highway",)
        if poi:
            for node in pbf.nodes(local[0], "objects"):
                if any(key in node["tags"] for key in keys):
                    selected[0].add(node["id"])
            for relation in pbf.relations(local[2]):
                if any(key in relation["tags"] for key in keys):
                    selected[2].add(relation["id"])
        for way in pbf.ways(local[1]):
            if any(key in way["tags"] for key in keys):
                selected[1].add(way["id"])
                selected[0].update(way["nodes"])
        if selected[2]:
            pbf.complete(selected)
        return pbf.read(selected, centers=poi), metadata

    def street(self, place, progress=lambda *args: None):
        identity = int(place["id"].split("/")[1])
        lat, lon = place["lat"], place["lon"]
        area = (lat - 1e-6, lon - 1e-6, lat + 1e-6, lon + 1e-6)
        while True:
            source, _ = self.region(area, progress)
            progress("Reading named street")
            pbf = PBF(source, self.client.check_cancel)
            seed = next(pbf.ways({identity}), None)
            tags = {} if seed is None else seed["tags"]
            if not tags.get("highway") or not tags.get("name"):
                raise RequestError("place_not_found", "The selected street is absent or unnamed in the Geofabrik snapshot.")
            name = tags["name"]
            ways = {way["id"]: way for way in pbf.ways(name=name)}
            by_node = defaultdict(list)
            for way in ways.values():
                for node in way["nodes"]:
                    by_node[node].append(way["id"])
            connected, pending = set(), [identity]
            while pending:
                current = pending.pop()
                if current in connected:
                    continue
                connected.add(current)
                for node in ways[current]["nodes"]:
                    pending.extend(by_node.pop(node, ()))
            nodes = {node for identity in connected for node in ways[identity]["nodes"]}
            selected = pbf.read((nodes, connected, set()))
            expanded = extent([area[:2], area[2:]] + [p for w in selected for p in w["points"]])
            next_source, _ = self.region(expanded, progress)
            if next_source == source:
                return name, selected
            area = expanded
