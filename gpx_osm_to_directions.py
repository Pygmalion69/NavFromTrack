#!/usr/bin/env python3
"""
GPX + OSM -> Mapbox/MapLibre DirectionsRoute JSON.

Some upstream data paths can leave literal "\\xH"/"\\xHH" sequences in geometry strings,
which breaks Mapbox polyline decoding on Android. This script sanitizes both the
top-level route geometry and every step geometry by converting "\\xHH" to the
corresponding character before JSON serialization.
"""
import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import xml.etree.ElementTree as ET

EARTH_RADIUS_M = 6371000.0


@dataclass
class Way:
    way_id: int
    node_ids: List[int]
    tags: Dict[str, str]


@dataclass
class Segment:
    segment_id: int
    way_id: int
    start_node_id: int
    end_node_id: int
    start_lat: float
    start_lon: float
    end_lat: float
    end_lon: float
    start_xy: Tuple[float, float]
    end_xy: Tuple[float, float]
    tags: Dict[str, str]


@dataclass
class Match:
    idx: int
    lat: float
    lon: float
    way_id: Optional[int]
    name: str
    highway: str
    tags: Dict[str, str]
    snap_error_m: Optional[float]


@dataclass
class Run:
    start_idx: int
    end_idx: int
    way_id: Optional[int]
    name: str
    highway: str
    tags: Dict[str, str]


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lam = math.radians(lon2 - lon1)
    a = (
        math.sin(d_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * (math.sin(d_lam / 2.0) ** 2)
    )
    return 2.0 * EARTH_RADIUS_M * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    lam1 = math.radians(lon1)
    lam2 = math.radians(lon2)
    y = math.sin(lam2 - lam1) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(lam2 - lam1)
    brng = math.degrees(math.atan2(y, x))
    return int(round((brng + 360.0) % 360.0))


def smallest_signed_angle(delta: float) -> float:
    return ((delta + 180.0) % 360.0) - 180.0


def build_projector(points: List[Tuple[float, float]]) -> Tuple[float, float, float]:
    lat0 = sum(p[0] for p in points) / len(points)
    lon0 = sum(p[1] for p in points) / len(points)
    cos_lat0 = math.cos(math.radians(lat0))
    return lat0, lon0, cos_lat0


def latlon_to_xy(lat: float, lon: float, lat0: float, lon0: float, cos_lat0: float) -> Tuple[float, float]:
    x = math.radians(lon - lon0) * EARTH_RADIUS_M * cos_lat0
    y = math.radians(lat - lat0) * EARTH_RADIUS_M
    return x, y


def point_segment_distance_m(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> Tuple[float, float]:
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    seg_len2 = vx * vx + vy * vy
    if seg_len2 == 0.0:
        dx = px - ax
        dy = py - ay
        return math.hypot(dx, dy), 0.0
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / seg_len2))
    projx = ax + t * vx
    projy = ay + t * vy
    return math.hypot(px - projx, py - projy), t


def encode_polyline(coords_lonlat: List[Tuple[float, float]], precision: int) -> str:
    factor = 10 ** precision
    out = []
    prev_lat = 0
    prev_lon = 0
    for lon, lat in coords_lonlat:
        ilat = int(round(lat * factor))
        ilon = int(round(lon * factor))
        out.append(_encode_signed(ilat - prev_lat))
        out.append(_encode_signed(ilon - prev_lon))
        prev_lat = ilat
        prev_lon = ilon
    return "".join(out)


def _encode_signed(value: int) -> str:
    value = ~(value << 1) if value < 0 else (value << 1)
    chars = []
    while value >= 0x20:
        chars.append(chr((0x20 | (value & 0x1F)) + 63))
        value >>= 5
    chars.append(chr(value + 63))
    return "".join(chars)


def geometry_for(coords_latlon: List[Tuple[float, float]], geometries: str):
    lonlat = [(lon, lat) for lat, lon in coords_latlon]
    if geometries == "geojson":
        return {"type": "LineString", "coordinates": lonlat}
    if geometries == "polyline":
        return encode_polyline(lonlat, precision=5)
    return encode_polyline(lonlat, precision=6)


def parse_gpx(gpx_path: Path) -> List[Tuple[float, float]]:
    tree = ET.parse(gpx_path)
    root = tree.getroot()
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}", 1)[0] + "}"
    trk = root.find(f"{ns}trk")
    if trk is None:
        raise ValueError("No <trk> found in GPX")
    points: List[Tuple[float, float]] = []
    for trkseg in trk.findall(f"{ns}trkseg"):
        for trkpt in trkseg.findall(f"{ns}trkpt"):
            lat = trkpt.attrib.get("lat")
            lon = trkpt.attrib.get("lon")
            if lat is not None and lon is not None:
                points.append((float(lat), float(lon)))
    if len(points) < 2:
        raise ValueError("GPX track must contain at least two points")
    return points


def parse_osm(osm_path: Path, project_ctx: Tuple[float, float, float]) -> Tuple[Dict[int, Tuple[float, float]], List[Segment]]:
    lat0, lon0, cos_lat0 = project_ctx
    tree = ET.parse(osm_path)
    root = tree.getroot()

    nodes: Dict[int, Tuple[float, float]] = {}
    ways: List[Way] = []

    for elem in root:
        if elem.tag == "node":
            node_id = int(elem.attrib["id"])
            lat = float(elem.attrib["lat"])
            lon = float(elem.attrib["lon"])
            nodes[node_id] = (lat, lon)
        elif elem.tag == "way":
            node_ids = []
            tags: Dict[str, str] = {}
            for child in elem:
                if child.tag == "nd":
                    ref = child.attrib.get("ref")
                    if ref is not None:
                        node_ids.append(int(ref))
                elif child.tag == "tag":
                    k = child.attrib.get("k")
                    v = child.attrib.get("v")
                    if k is not None and v is not None:
                        tags[k] = v
            if "highway" in tags and len(node_ids) >= 2:
                ways.append(Way(way_id=int(elem.attrib["id"]), node_ids=node_ids, tags=tags))

    segments: List[Segment] = []
    seg_id = 0
    for way in ways:
        for a, b in zip(way.node_ids, way.node_ids[1:]):
            if a not in nodes or b not in nodes:
                continue
            alat, alon = nodes[a]
            blat, blon = nodes[b]
            segments.append(
                Segment(
                    segment_id=seg_id,
                    way_id=way.way_id,
                    start_node_id=a,
                    end_node_id=b,
                    start_lat=alat,
                    start_lon=alon,
                    end_lat=blat,
                    end_lon=blon,
                    start_xy=latlon_to_xy(alat, alon, lat0, lon0, cos_lat0),
                    end_xy=latlon_to_xy(blat, blon, lat0, lon0, cos_lat0),
                    tags=way.tags,
                )
            )
            seg_id += 1
    if not segments:
        raise ValueError("No routable highway segments found in OSM")
    return nodes, segments


def build_grid_index(segments: List[Segment], cell_size_m: float) -> Dict[Tuple[int, int], List[int]]:
    grid: Dict[Tuple[int, int], List[int]] = {}
    for s in segments:
        min_x = min(s.start_xy[0], s.end_xy[0])
        max_x = max(s.start_xy[0], s.end_xy[0])
        min_y = min(s.start_xy[1], s.end_xy[1])
        max_y = max(s.start_xy[1], s.end_xy[1])
        gx0 = int(math.floor(min_x / cell_size_m))
        gx1 = int(math.floor(max_x / cell_size_m))
        gy0 = int(math.floor(min_y / cell_size_m))
        gy1 = int(math.floor(max_y / cell_size_m))
        for gx in range(gx0, gx1 + 1):
            for gy in range(gy0, gy1 + 1):
                grid.setdefault((gx, gy), []).append(s.segment_id)
    return grid


def annotate_points(
    points: List[Tuple[float, float]],
    segments: List[Segment],
    grid: Dict[Tuple[int, int], List[int]],
    project_ctx: Tuple[float, float, float],
    snap_radius_m: float,
    cell_size_m: float,
) -> List[Match]:
    lat0, lon0, cos_lat0 = project_ctx
    segment_lookup = {s.segment_id: s for s in segments}
    k = max(1, int(math.ceil(snap_radius_m / cell_size_m)) + 1)
    matches: List[Match] = []

    for idx, (lat, lon) in enumerate(points):
        px, py = latlon_to_xy(lat, lon, lat0, lon0, cos_lat0)
        gx = int(math.floor(px / cell_size_m))
        gy = int(math.floor(py / cell_size_m))

        candidate_ids = set()
        for x in range(gx - k, gx + k + 1):
            for y in range(gy - k, gy + k + 1):
                candidate_ids.update(grid.get((x, y), []))

        best_seg: Optional[Segment] = None
        best_dist = float("inf")
        for seg_id in candidate_ids:
            seg = segment_lookup[seg_id]
            d, _ = point_segment_distance_m(px, py, seg.start_xy[0], seg.start_xy[1], seg.end_xy[0], seg.end_xy[1])
            if d < best_dist:
                best_dist = d
                best_seg = seg

        if best_seg is not None and best_dist <= snap_radius_m:
            name = best_seg.tags.get("name") or best_seg.tags.get("ref") or ""
            matches.append(
                Match(
                    idx=idx,
                    lat=lat,
                    lon=lon,
                    way_id=best_seg.way_id,
                    name=name,
                    highway=best_seg.tags.get("highway", ""),
                    tags=best_seg.tags,
                    snap_error_m=best_dist,
                )
            )
        else:
            matches.append(
                Match(
                    idx=idx,
                    lat=lat,
                    lon=lon,
                    way_id=None,
                    name="",
                    highway="",
                    tags={},
                    snap_error_m=None,
                )
            )
    return matches


def point_distance_sum(points: List[Tuple[float, float]], start_idx: int, end_idx: int) -> float:
    dist = 0.0
    for i in range(start_idx, end_idx):
        dist += haversine_m(points[i][0], points[i][1], points[i + 1][0], points[i + 1][1])
    return dist


def build_runs(matches: List[Match]) -> List[Run]:
    if not matches:
        return []
    runs: List[Run] = []
    current = Run(
        start_idx=0,
        end_idx=0,
        way_id=matches[0].way_id,
        name=matches[0].name,
        highway=matches[0].highway,
        tags=matches[0].tags,
    )

    for m in matches[1:]:
        if m.way_id != current.way_id or m.name != current.name:
            runs.append(current)
            current = Run(
                start_idx=m.idx,
                end_idx=m.idx,
                way_id=m.way_id,
                name=m.name,
                highway=m.highway,
                tags=m.tags,
            )
        else:
            current.end_idx = m.idx
    runs.append(current)
    return runs


def smooth_runs(runs: List[Run], points: List[Tuple[float, float]], min_run_dist_m: float) -> List[Run]:
    if len(runs) < 3:
        return runs

    changed = True
    smoothed = runs[:]
    while changed and len(smoothed) >= 3:
        changed = False
        i = 1
        while i < len(smoothed) - 1:
            prev_run = smoothed[i - 1]
            cur_run = smoothed[i]
            next_run = smoothed[i + 1]
            cur_dist = point_distance_sum(points, cur_run.start_idx, cur_run.end_idx)
            same_neighbors = (
                prev_run.way_id is not None
                and prev_run.way_id == next_run.way_id
                and prev_run.name == next_run.name
            )
            if cur_dist < min_run_dist_m and same_neighbors:
                merged = Run(
                    start_idx=prev_run.start_idx,
                    end_idx=next_run.end_idx,
                    way_id=prev_run.way_id,
                    name=prev_run.name,
                    highway=prev_run.highway,
                    tags=prev_run.tags,
                )
                smoothed[i - 1 : i + 2] = [merged]
                changed = True
                i = max(1, i - 1)
            else:
                i += 1
    return smoothed


def run_start_bearing(points: List[Tuple[float, float]], run: Run) -> int:
    start = run.start_idx
    end = min(run.end_idx, start + 3)
    if end == start and start > 0:
        return bearing_deg(points[start - 1][0], points[start - 1][1], points[start][0], points[start][1])
    return bearing_deg(points[start][0], points[start][1], points[end][0], points[end][1])


def run_end_bearing(points: List[Tuple[float, float]], run: Run) -> int:
    end = run.end_idx
    start = max(run.start_idx, end - 3)
    if start == end and end + 1 < len(points):
        return bearing_deg(points[end][0], points[end][1], points[end + 1][0], points[end + 1][1])
    return bearing_deg(points[start][0], points[start][1], points[end][0], points[end][1])


def classify_turn(delta: float, continue_threshold_deg: float, turn_threshold_deg: float) -> Tuple[str, str]:
    abs_delta = abs(delta)
    if abs_delta <= continue_threshold_deg:
        return "continue", "straight"
    if abs_delta < turn_threshold_deg:
        return "continue", "straight"
    if abs_delta > 165:
        return "turn", "uturn"
    if delta > 0:
        if abs_delta <= 45:
            return "turn", "slight right"
        if abs_delta <= 135:
            return "turn", "right"
        return "turn", "sharp right"
    if abs_delta <= 45:
        return "turn", "slight left"
    if abs_delta <= 135:
        return "turn", "left"
    return "turn", "sharp left"


def format_instruction(
    maneuver_type: str,
    modifier: Optional[str],
    current_name: str,
    next_name: str,
    locale: str,
    roundabout_exit: Optional[int] = None,
) -> str:
    _ = locale
    if maneuver_type == "depart":
        if next_name:
            return f"Depart onto {next_name}"
        return "Depart"
    if maneuver_type == "arrive":
        return "Arrive at destination"
    if maneuver_type == "roundabout":
        if roundabout_exit is not None and next_name:
            return f"At roundabout, take exit {roundabout_exit} onto {next_name}"
        if roundabout_exit is not None:
            return f"At roundabout, take exit {roundabout_exit}"
        return "Enter the roundabout and continue"
    if maneuver_type == "continue":
        if next_name:
            return f"Continue on {next_name}"
        return "Continue"

    direction = modifier or "straight"
    if next_name and next_name != current_name:
        return f"Turn {direction} onto {next_name}"
    if next_name:
        return f"Turn {direction} to stay on {next_name}"
    return f"Turn {direction}"


def mode_for_profile(profile: str) -> str:
    return {"car": "driving", "bike": "cycling", "foot": "walking"}[profile]


def default_speed(profile: str) -> float:
    return {"car": 30.0, "bike": 18.0, "foot": 5.0}[profile]


def coords_for_range(points: List[Tuple[float, float]], start_idx: int, end_idx: int) -> List[Tuple[float, float]]:
    coords = points[start_idx : end_idx + 1]
    if len(coords) >= 2:
        return coords
    if start_idx > 0:
        return [points[start_idx - 1], points[start_idx]]
    if end_idx + 1 < len(points):
        return [points[end_idx], points[end_idx + 1]]
    return [points[0], points[-1]]


def intersection_for(
    man_type: str,
    location: List[float],
    bearing_before: Optional[int],
    bearing_after: Optional[int],
) -> Dict:
    if man_type == "depart":
        bearings = [int(bearing_after)] if bearing_after is not None else []
        inter = {"location": location, "bearings": bearings, "entry": [True] * len(bearings)}
        if bearings:
            inter["out"] = 0
        return inter
    if man_type == "arrive":
        return {"location": location, "bearings": [], "entry": []}

    bearings: List[int] = []
    if bearing_before is not None:
        bearings.append(int(bearing_before))
    if bearing_after is not None:
        bearings.append(int(bearing_after))
    inter = {"location": location, "bearings": bearings, "entry": [True] * len(bearings)}
    if len(bearings) >= 2:
        inter["in"] = 0
        inter["out"] = 1
    elif len(bearings) == 1:
        inter["out"] = 0
    return inter


def build_step(
    points: List[Tuple[float, float]],
    start_idx: int,
    end_idx: int,
    mode: str,
    speed_kmh: float,
    geometries: str,
    man_type: str,
    modifier: Optional[str],
    bearing_before: Optional[int],
    bearing_after: Optional[int],
    location: List[float],
    instruction: str,
    name: str,
) -> Dict:
    coords = coords_for_range(points, start_idx, end_idx)
    distance_m = point_distance_sum(coords, 0, len(coords) - 1)
    duration_s = distance_m / (speed_kmh * 1000.0 / 3600.0)

    maneuver = {
        "type": man_type,
        "location": location,
        "instruction": instruction,
    }
    if modifier is not None:
        maneuver["modifier"] = modifier
    if bearing_before is not None:
        maneuver["bearing_before"] = int(bearing_before)
    if bearing_after is not None:
        maneuver["bearing_after"] = int(bearing_after)

    return {
        "distance": distance_m,
        "duration": duration_s,
        "weight": duration_s,
        "name": name,
        "mode": mode,
        "driving_side": "right",
        "geometry": geometry_for(coords, geometries),
        "maneuver": maneuver,
        "intersections": [intersection_for(man_type, location, bearing_before, bearing_after)],
        "_coords_latlon": coords,
    }


def merge_continue_steps(steps: List[Dict], geometries: str, merge_below_m: float) -> List[Dict]:
    if merge_below_m <= 0:
        return steps

    merged: List[Dict] = []
    for step in steps:
        if not merged:
            merged.append(step)
            continue
        prev = merged[-1]
        prev_man = prev.get("maneuver", {})
        cur_man = step.get("maneuver", {})
        same_name = (prev.get("name") or "") == (step.get("name") or "")
        both_continue = prev_man.get("type") == "continue" and cur_man.get("type") == "continue"
        if both_continue and same_name and (float(prev["distance"]) + float(step["distance"]) < merge_below_m):
            prev["distance"] = float(prev["distance"]) + float(step["distance"])
            prev["duration"] = float(prev["duration"]) + float(step["duration"])
            prev["weight"] = float(prev["weight"]) + float(step["weight"])

            prev_coords = prev.get("_coords_latlon", [])
            cur_coords = step.get("_coords_latlon", [])
            if prev_coords and cur_coords and prev_coords[-1] == cur_coords[0]:
                merged_coords = prev_coords + cur_coords[1:]
            else:
                merged_coords = prev_coords + cur_coords
            prev["_coords_latlon"] = merged_coords
            prev["geometry"] = geometry_for(merged_coords, geometries)
        else:
            merged.append(step)
    return merged


def validate_polyline_string(value: str, label: str) -> None:
    for ch in value:
        if ord(ch) < 32:
            raise ValueError(f"Invalid control character in {label}")


def validate_no_invalid_hex_escape(serialized_json: str) -> None:
    i = 0
    n = len(serialized_json)
    while i < n:
        if serialized_json[i] != "\\":
            i += 1
            continue
        j = i
        while j < n and serialized_json[j] == "\\":
            j += 1
        if j < n and serialized_json[j] == "x":
            slash_count = j - i
            if slash_count % 2 == 1:
                raise ValueError("Invalid \\x escape in serialized JSON")
        i = j + 1


def sanitize_geometry_string(value: str) -> str:
    def replace_hex(match: re.Match) -> str:
        decoded = chr(int(match.group(1), 16))
        return decoded if 63 <= ord(decoded) <= 126 else "?"

    # Decode canonical \xHH where it yields printable polyline characters.
    sanitized = re.sub(r"\\x([0-9a-fA-F]{2})", replace_hex, value)
    # Handle malformed \xH by dropping the marker and keeping the nibble as text.
    sanitized = re.sub(r"\\x([0-9a-fA-F])(?![0-9a-fA-F])", lambda m: m.group(1), sanitized)
    # Remove any remaining literal \x marker sequences.
    sanitized = sanitized.replace("\\x", "x")
    return sanitized


def sanitize_route_geometries(route: Dict) -> None:
    top_geom = route.get("geometry")
    if isinstance(top_geom, str):
        route["geometry"] = sanitize_geometry_string(top_geom)

    steps = route.get("legs", [{}])[0].get("steps", [])
    for step in steps:
        geom = step.get("geometry")
        if isinstance(geom, str):
            step["geometry"] = sanitize_geometry_string(geom)


def validate_route_schema(route: Dict) -> List[str]:
    errors: List[str] = []
    valid_maneuver_types = {"depart", "turn", "continue", "arrive", "roundabout"}
    steps = route.get("legs", [{}])[0].get("steps", [])
    for idx, step in enumerate(steps):
        for field in ("distance", "duration", "mode"):
            if field not in step:
                errors.append(f"step {idx}: missing {field}")
        maneuver = step.get("maneuver")
        if not isinstance(maneuver, dict):
            errors.append(f"step {idx}: missing maneuver")
            continue
        if "type" not in maneuver:
            errors.append(f"step {idx}: maneuver missing type")
        elif maneuver["type"] not in valid_maneuver_types:
            errors.append(f"step {idx}: unsupported maneuver.type {maneuver['type']}")
        location = maneuver.get("location")
        if not (
            isinstance(location, list)
            and len(location) == 2
            and all(isinstance(v, (int, float)) for v in location)
        ):
            errors.append(f"step {idx}: maneuver.location must be [lon, lat]")
        intersections = step.get("intersections", [])
        for inter_idx, inter in enumerate(intersections):
            bearings = inter.get("bearings", [])
            entry = inter.get("entry", [])
            if len(entry) != len(bearings):
                errors.append(f"step {idx} intersection {inter_idx}: len(entry) != len(bearings)")
            if not all(isinstance(b, int) and 0 <= b <= 359 for b in bearings):
                errors.append(f"step {idx} intersection {inter_idx}: bearings must be ints in [0..359]")
            in_idx = inter.get("in")
            out_idx = inter.get("out")
            if in_idx is not None and (not isinstance(in_idx, int) or in_idx < 0 or in_idx >= len(bearings)):
                errors.append(f"step {idx} intersection {inter_idx}: invalid in index")
            if out_idx is not None and (not isinstance(out_idx, int) or out_idx < 0 or out_idx >= len(bearings)):
                errors.append(f"step {idx} intersection {inter_idx}: invalid out index")
    return errors


def write_debug_artifacts(
    debug_dir: Path,
    matches: List[Match],
    runs: List[Run],
    maneuvers: List[Dict],
    points: List[Tuple[float, float]],
) -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)

    with (debug_dir / "snapped_points.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["idx", "lat", "lon", "way_id", "name", "snap_error_m"])
        for m in matches:
            writer.writerow([m.idx, m.lat, m.lon, m.way_id, m.name, m.snap_error_m])

    with (debug_dir / "runs.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["run_index", "start_idx", "end_idx", "way_id", "name", "dist_m"])
        for i, run in enumerate(runs):
            writer.writerow([i, run.start_idx, run.end_idx, run.way_id, run.name, point_distance_sum(points, run.start_idx, run.end_idx)])

    with (debug_dir / "maneuvers.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["step_index", "lat", "lon", "type", "modifier", "instruction", "bearing_before", "bearing_after"])
        for m in maneuvers:
            writer.writerow(
                [
                    m.get("step_index"),
                    m["location"][1],
                    m["location"][0],
                    m["type"],
                    m.get("modifier", ""),
                    m.get("instruction", ""),
                    m.get("bearing_before", ""),
                    m.get("bearing_after", ""),
                ]
            )

    route_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": "route"},
                "geometry": {
                    "type": "LineString",
                    "coordinates": [[lon, lat] for lat, lon in points],
                },
            }
        ],
    }
    (debug_dir / "route.geojson").write_text(json.dumps(route_geojson, indent=2), encoding="utf-8")

    maneuvers_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "type": m["type"],
                    "modifier": m.get("modifier"),
                    "instruction": m.get("instruction", ""),
                },
                "geometry": {"type": "Point", "coordinates": m["location"]},
            }
            for m in maneuvers
        ],
    }
    (debug_dir / "maneuvers.geojson").write_text(json.dumps(maneuvers_geojson, indent=2), encoding="utf-8")


def build_route_json(
    points: List[Tuple[float, float]],
    runs: List[Run],
    profile: str,
    locale: str,
    geometries: str,
    speed_kmh: float,
    continue_threshold_deg: float,
    turn_threshold_deg: float,
    merge_below_m: float,
) -> Dict:
    mode = mode_for_profile(profile)

    steps: List[Dict] = []
    maneuvers_debug: List[Dict] = []
    i = 0
    while i < len(runs):
        run = runs[i]
        if i == 0:
            bearing_after = run_start_bearing(points, run)
            location = [points[run.start_idx][1], points[run.start_idx][0]]
            step = build_step(
                points=points,
                start_idx=run.start_idx,
                end_idx=run.end_idx,
                mode=mode,
                speed_kmh=speed_kmh,
                geometries=geometries,
                man_type="depart",
                modifier=None,
                bearing_before=None,
                bearing_after=bearing_after,
                location=location,
                instruction=format_instruction("depart", None, "", run.name, locale),
                name=run.name,
            )
            steps.append(step)
            i += 1
            continue

        prev_run = runs[i - 1]
        is_rb = run.tags.get("junction") == "roundabout"
        if is_rb:
            rb_start = i
            rb_end = i
            while rb_end + 1 < len(runs) and runs[rb_end + 1].tags.get("junction") == "roundabout":
                rb_end += 1
            next_idx = rb_end + 1

            entry_run = runs[rb_start]
            entry_prev = runs[rb_start - 1]
            entry_bearing_before = run_end_bearing(points, entry_prev)
            entry_bearing_after = run_start_bearing(points, entry_run)
            entry_location = [points[entry_run.start_idx][1], points[entry_run.start_idx][0]]
            exit_count = None
            exit_name = runs[next_idx].name if next_idx < len(runs) else ""
            rb_instruction = format_instruction("roundabout", None, entry_prev.name, exit_name, locale, exit_count)
            steps.append(
                build_step(
                    points=points,
                    start_idx=entry_run.start_idx,
                    end_idx=runs[rb_end].end_idx,
                    mode=mode,
                    speed_kmh=speed_kmh,
                    geometries=geometries,
                    man_type="roundabout",
                    modifier=None,
                    bearing_before=entry_bearing_before,
                    bearing_after=entry_bearing_after,
                    location=entry_location,
                    instruction=rb_instruction,
                    name=entry_run.name,
                )
            )

            if next_idx < len(runs):
                exit_run = runs[next_idx]
                exit_bearing_before = run_end_bearing(points, runs[rb_end])
                exit_bearing_after = run_start_bearing(points, exit_run)
                delta = smallest_signed_angle(float(exit_bearing_after - exit_bearing_before))
                man_type, modifier = classify_turn(delta, continue_threshold_deg, turn_threshold_deg)
                exit_location = [points[exit_run.start_idx][1], points[exit_run.start_idx][0]]
                steps.append(
                    build_step(
                        points=points,
                        start_idx=exit_run.start_idx,
                        end_idx=exit_run.end_idx,
                        mode=mode,
                        speed_kmh=speed_kmh,
                        geometries=geometries,
                        man_type=man_type,
                        modifier=modifier,
                        bearing_before=exit_bearing_before,
                        bearing_after=exit_bearing_after,
                        location=exit_location,
                        instruction=format_instruction(man_type, modifier, runs[rb_end].name, exit_run.name, locale),
                        name=exit_run.name,
                    )
                )
                i = next_idx + 1
            else:
                i = next_idx
            continue

        bearing_before = run_end_bearing(points, prev_run)
        bearing_after = run_start_bearing(points, run)
        delta = smallest_signed_angle(float(bearing_after - bearing_before))
        man_type, modifier = classify_turn(delta, continue_threshold_deg, turn_threshold_deg)
        location = [points[run.start_idx][1], points[run.start_idx][0]]
        steps.append(
            build_step(
                points=points,
                start_idx=run.start_idx,
                end_idx=run.end_idx,
                mode=mode,
                speed_kmh=speed_kmh,
                geometries=geometries,
                man_type=man_type,
                modifier=modifier,
                bearing_before=bearing_before,
                bearing_after=bearing_after,
                location=location,
                instruction=format_instruction(man_type, modifier, prev_run.name, run.name, locale),
                name=run.name,
            )
        )
        i += 1

    steps = merge_continue_steps(steps, geometries=geometries, merge_below_m=merge_below_m)

    for idx, step in enumerate(steps):
        debug_record = dict(step["maneuver"])
        debug_record["step_index"] = idx
        maneuvers_debug.append(debug_record)

    last_point = points[-1]
    arrive_maneuver = {
        "type": "arrive",
        "location": [last_point[1], last_point[0]],
        "instruction": format_instruction("arrive", None, "", "", locale),
    }
    arrive_step = {
        "distance": 0.0,
        "duration": 0.0,
        "weight": 0.0,
        "name": "",
        "mode": mode,
        "driving_side": "right",
        "geometry": "" if geometries != "geojson" else {"type": "LineString", "coordinates": []},
        "maneuver": arrive_maneuver,
        "intersections": [intersection_for("arrive", [last_point[1], last_point[0]], None, None)],
        "_coords_latlon": [last_point],
    }
    steps.append(arrive_step)
    debug_arrive = dict(arrive_maneuver)
    debug_arrive["step_index"] = len(steps) - 1
    maneuvers_debug.append(debug_arrive)

    leg_distance = sum(step["distance"] for step in steps)
    leg_duration = sum(step["duration"] for step in steps)

    route = {
        "distance": leg_distance,
        "duration": leg_duration,
        "weight": leg_duration,
        "weight_name": "routability",
        "geometry": geometry_for(points, geometries),
        "legs": [
            {
                "summary": "RPP route",
                "distance": leg_distance,
                "duration": leg_duration,
                "weight": leg_duration,
                "steps": steps,
            }
        ],
        "voiceLocale": locale,
    }
    for step in steps:
        step.pop("_coords_latlon", None)
    return route, maneuvers_debug


def validate_args(args: argparse.Namespace) -> None:
    if args.turn_threshold_deg <= 0:
        raise ValueError("--turn-threshold-deg must be > 0")
    if args.continue_threshold_deg < 0:
        raise ValueError("--continue-threshold-deg must be >= 0")
    if args.continue_threshold_deg > args.turn_threshold_deg:
        raise ValueError("--continue-threshold-deg must be <= --turn-threshold-deg")
    if args.speed_kmh <= 0:
        raise ValueError("--speed-kmh must be > 0")
    if args.snap_radius_m <= 0:
        raise ValueError("--snap-radius-m must be > 0")
    if args.merge_below_m < 0:
        raise ValueError("--merge-below-m must be >= 0")


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Convert GPX + OSM into Mapbox/MapLibre directions JSON")
    parser.add_argument("--gpx", required=True, type=Path)
    parser.add_argument("--osm", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--profile", choices=["car", "bike", "foot"], default="car")
    parser.add_argument("--locale", default="en")
    parser.add_argument("--geometries", choices=["polyline", "polyline6", "geojson"], default="polyline6")
    parser.add_argument("--speed-kmh", type=float, default=None)
    parser.add_argument("--snap-radius-m", type=float, default=20.0)
    parser.add_argument("--turn-threshold-deg", type=float, default=30.0)
    parser.add_argument("--continue-threshold-deg", type=float, default=15.0)
    parser.add_argument("--min-run-dist-m", type=float, default=10.0)
    parser.add_argument("--merge-below-m", type=float, default=40.0)
    parser.add_argument("--strict-mapbox", action="store_true")
    parser.add_argument("--debug-dir", type=Path)
    parser.add_argument("--force", action="store_true", help="Allow output even if OSM match coverage < 80%")

    args = parser.parse_args(list(argv) if argv is not None else None)

    try:
        if args.speed_kmh is None:
            args.speed_kmh = default_speed(args.profile)
        validate_args(args)

        points = parse_gpx(args.gpx)
        project_ctx = build_projector(points)
        _, segments = parse_osm(args.osm, project_ctx)
        cell_size_m = max(50.0, args.snap_radius_m * 2.5)
        grid = build_grid_index(segments, cell_size_m)
        matches = annotate_points(points, segments, grid, project_ctx, args.snap_radius_m, cell_size_m)

        matched_count = sum(1 for m in matches if m.way_id is not None)
        coverage = matched_count / len(matches)
        if coverage < 0.80 and not args.force:
            print(
                f"Insufficient OSM coverage: matched {matched_count}/{len(matches)} points ({coverage:.1%})",
                file=sys.stderr,
            )
            return 3

        runs = build_runs(matches)
        runs = smooth_runs(runs, points, args.min_run_dist_m)
        if not runs:
            raise ValueError("No runs could be produced from matched points")

        route_json, maneuvers_debug = build_route_json(
            points=points,
            runs=runs,
            profile=args.profile,
            locale=args.locale,
            geometries=args.geometries,
            speed_kmh=args.speed_kmh,
            continue_threshold_deg=args.continue_threshold_deg,
            turn_threshold_deg=args.turn_threshold_deg,
            merge_below_m=args.merge_below_m,
        )
        sanitize_route_geometries(route_json)

        for step_index, step in enumerate(route_json["legs"][0]["steps"]):
            geom = step.get("geometry")
            if isinstance(geom, str):
                validate_polyline_string(geom, f"step {step_index} geometry")
        top_geom = route_json.get("geometry")
        if isinstance(top_geom, str):
            validate_polyline_string(top_geom, "route geometry")

        schema_errors = validate_route_schema(route_json)
        if schema_errors:
            if args.strict_mapbox:
                raise ValueError("Mapbox schema validation failed: " + "; ".join(schema_errors))
            print("Mapbox schema warnings: " + "; ".join(schema_errors), file=sys.stderr)

        serialized = json.dumps(route_json, ensure_ascii=False)
        json.loads(serialized)
        validate_no_invalid_hex_escape(serialized)

        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", encoding="utf-8", newline="\n") as f:
            f.write(serialized)

        if args.debug_dir is not None:
            write_debug_artifacts(args.debug_dir, matches, runs, maneuvers_debug, points)

        return 0

    except (FileNotFoundError, ET.ParseError, ValueError) as exc:
        print(f"Input/parse error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Internal error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
