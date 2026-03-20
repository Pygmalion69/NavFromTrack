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


@dataclass
class ManeuverCandidate:
    candidate_index: int
    maneuver_idx: int
    boundary_idx: int
    exit_idx: int
    road_from: str
    road_to: str
    highway_from: str
    highway_to: str
    approach_heading: int
    departure_heading: int
    signed_delta: float
    maneuver_type: str
    modifier: Optional[str]
    instruction: str
    step_name: str
    emit: bool
    reason: str
    is_roundabout: bool
    roundabout_exit: Optional[int]
    step_index: Optional[int] = None


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


def cumulative_distances(points: List[Tuple[float, float]]) -> List[float]:
    distances = [0.0]
    for i in range(1, len(points)):
        distances.append(distances[-1] + haversine_m(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]))
    return distances


def interpolate_point(points: List[Tuple[float, float]], cumulative: List[float], target_m: float) -> Tuple[float, float]:
    if target_m <= 0.0:
        return points[0]
    if target_m >= cumulative[-1]:
        return points[-1]

    hi = 1
    while hi < len(cumulative) and cumulative[hi] < target_m:
        hi += 1
    lo = max(0, hi - 1)
    if hi >= len(points):
        return points[-1]

    start_d = cumulative[lo]
    end_d = cumulative[hi]
    if end_d <= start_d:
        return points[hi]

    ratio = (target_m - start_d) / (end_d - start_d)
    lat = points[lo][0] + (points[hi][0] - points[lo][0]) * ratio
    lon = points[lo][1] + (points[hi][1] - points[lo][1]) * ratio
    return lat, lon


def heading_around_index(
    points: List[Tuple[float, float]],
    cumulative: List[float],
    center_idx: int,
    window_m: float,
    forward: bool,
) -> int:
    center_d = cumulative[center_idx]
    center_point = points[center_idx]
    if forward:
        target_point = interpolate_point(points, cumulative, min(cumulative[-1], center_d + window_m))
        return bearing_deg(center_point[0], center_point[1], target_point[0], target_point[1])
    target_point = interpolate_point(points, cumulative, max(0.0, center_d - window_m))
    return bearing_deg(target_point[0], target_point[1], center_point[0], center_point[1])


def normalized_road_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


def same_road_identity(prev_run: Run, next_run: Run) -> bool:
    if prev_run.way_id is not None and prev_run.way_id == next_run.way_id:
        return True
    prev_name = normalized_road_name(prev_run.name)
    next_name = normalized_road_name(next_run.name)
    return bool(prev_name and prev_name == next_name)


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


def ordinal(value: int) -> str:
    if 10 <= (value % 100) <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(value % 10, "th")
    return f"{value}{suffix}"


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
            return f"At the roundabout, take the {ordinal(roundabout_exit)} exit onto {next_name}"
        if roundabout_exit is not None:
            return f"At the roundabout, take the {ordinal(roundabout_exit)} exit"
        return "Enter the roundabout"
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


def escape_ssml_text(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def instruction_distance_along(step_distance_m: float) -> float:
    if step_distance_m <= 0:
        return 0.0
    return float(min(80.0, max(5.0, step_distance_m * 0.5), step_distance_m))


def build_voice_instructions(step_distance_m: float, instruction: str) -> List[Dict]:
    if not instruction:
        return []
    distance_along = instruction_distance_along(step_distance_m)
    return [
        {
            "distanceAlongGeometry": distance_along,
            "announcement": instruction,
            "ssmlAnnouncement": f"<speak>{escape_ssml_text(instruction)}</speak>",
        }
    ]


def build_banner_instructions(
    step_distance_m: float,
    instruction: str,
    step_name: str,
    maneuver_type: str,
    maneuver_modifier: Optional[str],
) -> List[Dict]:
    text = step_name or instruction or ""
    if not text:
        return []
    primary = {
        "text": text,
        "type": maneuver_type,
    }
    if maneuver_modifier:
        primary["modifier"] = maneuver_modifier
    return [
        {
            "distanceAlongGeometry": instruction_distance_along(step_distance_m),
            "primary": primary,
            "sub": None,
        }
    ]


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
    roundabout_exit: Optional[int] = None,
) -> Dict:
    coords = coords_for_range(points, start_idx, end_idx)
    distance_m = point_distance_sum(coords, 0, len(coords) - 1)
    duration_s = distance_m / (speed_kmh * 1000.0 / 3600.0)

    resolved_bearing_before = int(round(bearing_before if bearing_before is not None else (bearing_after if bearing_after is not None else 0))) % 360
    resolved_bearing_after = int(round(bearing_after if bearing_after is not None else (bearing_before if bearing_before is not None else 0))) % 360

    maneuver = {
        "type": man_type,
        "location": location,
        "instruction": instruction,
        "bearing_before": resolved_bearing_before,
        "bearing_after": resolved_bearing_after,
    }
    if modifier is not None:
        maneuver["modifier"] = modifier
    if roundabout_exit is not None:
        maneuver["exit"] = int(roundabout_exit)

    voice_instructions = [] if man_type == "arrive" else build_voice_instructions(distance_m, instruction)
    banner_instructions = [] if man_type == "arrive" else build_banner_instructions(
        step_distance_m=distance_m,
        instruction=instruction,
        step_name=name,
        maneuver_type=man_type,
        maneuver_modifier=modifier,
    )

    return {
        "distance": distance_m,
        "duration": duration_s,
        "duration_typical": duration_s,
        "weight": duration_s,
        "name": name,
        "mode": mode,
        "driving_side": "right",
        "geometry": geometry_for(coords, geometries),
        "maneuver": maneuver,
        "intersections": [intersection_for(man_type, location, bearing_before, bearing_after)],
        "voiceInstructions": voice_instructions,
        "bannerInstructions": banner_instructions,
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
            prev["duration_typical"] = float(prev.get("duration_typical", 0.0)) + float(step.get("duration_typical", 0.0))

            prev_coords = prev.get("_coords_latlon", [])
            cur_coords = step.get("_coords_latlon", [])
            if prev_coords and cur_coords and prev_coords[-1] == cur_coords[0]:
                merged_coords = prev_coords + cur_coords[1:]
            else:
                merged_coords = prev_coords + cur_coords
            prev["_coords_latlon"] = merged_coords
            prev["geometry"] = geometry_for(merged_coords, geometries)
            man = prev.get("maneuver", {})
            instruction = str(man.get("instruction", ""))
            man_type = str(man.get("type", "continue"))
            man_modifier = man.get("modifier")
            prev["voiceInstructions"] = [] if man_type == "arrive" else build_voice_instructions(float(prev["distance"]), instruction)
            prev["bannerInstructions"] = [] if man_type == "arrive" else build_banner_instructions(
                step_distance_m=float(prev["distance"]),
                instruction=instruction,
                step_name=str(prev.get("name", "")),
                maneuver_type=man_type,
                maneuver_modifier=str(man_modifier) if man_modifier is not None else None,
            )
        else:
            merged.append(step)
    return merged


def estimate_roundabout_exit_count(runs: List[Run], rb_start: int, rb_end: int) -> int:
    exit_count = 1
    seen_keys = set()
    for idx in range(rb_start, rb_end + 1):
        key = (
            runs[idx].way_id,
            normalized_road_name(runs[idx].name),
            runs[idx].start_idx,
            runs[idx].end_idx,
        )
        if key not in seen_keys:
            seen_keys.add(key)
            exit_count += 1
    return max(1, exit_count - 1)


def build_maneuver_candidates(
    points: List[Tuple[float, float]],
    runs: List[Run],
    locale: str,
    continue_threshold_deg: float,
    turn_threshold_deg: float,
    heading_window_m: float = 20.0,
) -> List[ManeuverCandidate]:
    cumulative = cumulative_distances(points)
    candidates: List[ManeuverCandidate] = []
    i = 1

    while i < len(runs):
        prev_run = runs[i - 1]
        run = runs[i]

        if run.tags.get("junction") == "roundabout":
            rb_start = i
            rb_end = i
            while rb_end + 1 < len(runs) and runs[rb_end + 1].tags.get("junction") == "roundabout":
                rb_end += 1
            next_idx = rb_end + 1
            road_to = runs[next_idx].name if next_idx < len(runs) else ""
            exit_idx = runs[next_idx].start_idx if next_idx < len(runs) else runs[rb_end].end_idx
            approach_heading = heading_around_index(points, cumulative, runs[rb_start].start_idx, heading_window_m, forward=False)
            departure_heading = heading_around_index(points, cumulative, exit_idx, heading_window_m, forward=True)
            signed_delta = smallest_signed_angle(float(departure_heading - approach_heading))
            exit_count = estimate_roundabout_exit_count(runs, rb_start, rb_end)
            candidates.append(
                ManeuverCandidate(
                    candidate_index=len(candidates),
                    maneuver_idx=runs[rb_start].start_idx,
                    boundary_idx=runs[rb_start].start_idx,
                    exit_idx=exit_idx,
                    road_from=prev_run.name,
                    road_to=road_to,
                    highway_from=prev_run.highway,
                    highway_to=runs[next_idx].highway if next_idx < len(runs) else "",
                    approach_heading=approach_heading,
                    departure_heading=departure_heading,
                    signed_delta=signed_delta,
                    maneuver_type="roundabout",
                    modifier=None,
                    instruction=format_instruction("roundabout", None, prev_run.name, road_to, locale, exit_count),
                    step_name=road_to,
                    emit=True,
                    reason="emit: composite roundabout maneuver",
                    is_roundabout=True,
                    roundabout_exit=exit_count,
                )
            )
            i = next_idx
            continue

        # Determine the maneuver index at the start of the current run
        maneuver_idx = run.start_idx

        # Compute distances along the previous and current runs
        try:
            prev_run_distance = point_distance_sum(points, prev_run.start_idx, prev_run.end_idx)
        except Exception:
            prev_run_distance = 0.0

        try:
            run_distance = point_distance_sum(points, run.start_idx, run.end_idx)
        except Exception:
            run_distance = 0.0

        # Dynamic heading windows (critical fix)
        back_window = heading_window_m
        if prev_run_distance > 0.0:
            back_window = min(heading_window_m, prev_run_distance / 2.0)

        forward_window = heading_window_m
        if run_distance > 0.0:
            forward_window = min(heading_window_m, run_distance / 2.0)

        # Compute headings
        approach_heading = heading_around_index(points, cumulative, maneuver_idx, back_window, forward=False)
        departure_heading = heading_around_index(points, cumulative, maneuver_idx, forward_window, forward=True)

        # Angle delta
        signed_delta = smallest_signed_angle(float(departure_heading - approach_heading))
        abs_delta = abs(signed_delta)
        same_road = same_road_identity(prev_run, run)
        highway_changed = bool(prev_run.highway and run.highway and prev_run.highway != run.highway)
        road_to = run.name
        emit = False
        reason = "suppress: unchanged geometry"
        maneuver_type = "continue"
        modifier: Optional[str] = "straight"

        if abs_delta >= turn_threshold_deg:
            maneuver_type, modifier = classify_turn(signed_delta, continue_threshold_deg, turn_threshold_deg)
            if same_road and not highway_changed:
                emit = False
                reason = "suppress: same-road bend"
            else:
                emit = True
                reason = "emit: significant heading change"
        elif abs_delta <= continue_threshold_deg:
            emit = False
            reason = "suppress: below continue threshold"
        elif same_road and not highway_changed:
            emit = False
            reason = "suppress: same-road non-decision"
        elif not road_to and not highway_changed:
            emit = False
            reason = "suppress: unnamed minor bend"
        else:
            emit = True
            reason = "emit: minor heading change with meaningful road change"

        instruction = format_instruction(maneuver_type, modifier, prev_run.name, road_to, locale)
        candidates.append(
            ManeuverCandidate(
                candidate_index=len(candidates),
                maneuver_idx=maneuver_idx,
                boundary_idx=maneuver_idx,
                exit_idx=maneuver_idx,
                road_from=prev_run.name,
                road_to=road_to,
                highway_from=prev_run.highway,
                highway_to=run.highway,
                approach_heading=approach_heading,
                departure_heading=departure_heading,
                signed_delta=signed_delta,
                maneuver_type=maneuver_type,
                modifier=modifier,
                instruction=instruction,
                step_name=road_to,
                emit=emit,
                reason=reason,
                is_roundabout=False,
                roundabout_exit=None,
            )
        )
        i += 1

    return candidates


def validate_polyline_string(value: str, label: str) -> None:
    for ch in value:
        if ord(ch) < 32:
            raise ValueError(f"Invalid control character in {label}")


def polyline_debug_snippet(encoded: str) -> str:
    head = encoded[:80]
    tail = encoded[-80:] if len(encoded) > 80 else encoded
    return f"len={len(encoded)} head={head!r} tail={tail!r}"


def decode_polyline(encoded: str, precision: int) -> List[Tuple[float, float]]:
    if precision < 0:
        raise ValueError("precision must be >= 0")

    factor = 10 ** precision
    index = 0
    lat = 0
    lon = 0
    coords: List[Tuple[float, float]] = []

    while index < len(encoded):
        result = 0
        shift = 0
        while True:
            if index >= len(encoded):
                raise ValueError("truncated polyline while decoding latitude")
            b = ord(encoded[index]) - 63
            if b < 0 or b > 63:
                raise ValueError(f"invalid character at index {index}")
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
            if shift > 60:
                raise ValueError("latitude varint too long")
        d_lat = ~(result >> 1) if (result & 1) else (result >> 1)
        lat += d_lat

        result = 0
        shift = 0
        while True:
            if index >= len(encoded):
                raise ValueError("truncated polyline while decoding longitude")
            b = ord(encoded[index]) - 63
            if b < 0 or b > 63:
                raise ValueError(f"invalid character at index {index}")
            index += 1
            result |= (b & 0x1F) << shift
            shift += 5
            if b < 0x20:
                break
            if shift > 60:
                raise ValueError("longitude varint too long")
        d_lon = ~(result >> 1) if (result & 1) else (result >> 1)
        lon += d_lon

        coords.append((lat / factor, lon / factor))

    return coords


def assert_valid_polyline(encoded: str, precision: int, label: str) -> None:
    if not isinstance(encoded, str) or not encoded:
        raise ValueError(f"{label}: encoded geometry must be a non-empty string")
    validate_polyline_string(encoded, label)
    try:
        coords = decode_polyline(encoded, precision)
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}; {polyline_debug_snippet(encoded)}") from exc
    if len(coords) < 2:
        raise ValueError(f"{label}: decoded polyline is too short; {polyline_debug_snippet(encoded)}")


def polyline_precision_for(geometries: str) -> Optional[int]:
    if geometries == "polyline6":
        return 6
    if geometries == "polyline":
        return 5
    return None


def validate_route_polylines(route: Dict, geometries: str) -> None:
    precision = polyline_precision_for(geometries)
    if precision is None:
        return

    route_geometry = route.get("geometry")
    if not isinstance(route_geometry, str):
        raise ValueError("route geometry must be a polyline string")
    assert_valid_polyline(route_geometry, precision, "route geometry")

    legs = route.get("legs", [])
    for leg_index, leg in enumerate(legs):
        steps = leg.get("steps", [])
        for step_index, step in enumerate(steps):
            geom = step.get("geometry")
            if not isinstance(geom, str):
                raise ValueError(f"leg {leg_index} step {step_index} geometry must be a polyline string")
            assert_valid_polyline(geom, precision, f"leg {leg_index} step {step_index} geometry")


def validate_route_options_consistency(route: Dict, profile: str, geometries: str) -> None:
    route_options = route.get("routeOptions")
    if not isinstance(route_options, dict):
        raise ValueError("routeOptions is missing or not an object")

    if route_options.get("geometries") != geometries:
        raise ValueError("routeOptions.geometries does not match actual geometry encoding")

    if route_options.get("steps") is not True:
        raise ValueError("routeOptions.steps must be true")
    if route_options.get("voiceInstructions") is not True:
        raise ValueError("routeOptions.voiceInstructions must be true")
    if route_options.get("bannerInstructions") is not True:
        raise ValueError("routeOptions.bannerInstructions must be true")
    if route_options.get("roundaboutExits") is not True:
        raise ValueError("routeOptions.roundaboutExits must be true")

    coordinates = route_options.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise ValueError("routeOptions.coordinates must be a list with at least 2 points")

    expected_profile = mode_for_profile(profile)
    if route_options.get("profile") != expected_profile:
        raise ValueError(f"routeOptions.profile must be {expected_profile!r}")


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


def validate_route_schema(route: Dict) -> List[str]:
    errors: List[str] = []
    valid_maneuver_types = {"depart", "turn", "continue", "arrive", "roundabout"}
    route_options = route.get("routeOptions", {})
    if route_options.get("voiceInstructions") is not True:
        errors.append("routeOptions.voiceInstructions must be true")
    if route_options.get("bannerInstructions") is not True:
        errors.append("routeOptions.bannerInstructions must be true")
    if route_options.get("roundaboutExits") is not True:
        errors.append("routeOptions.roundaboutExits must be true")
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
        maneuver_type = maneuver.get("type")
        if maneuver_type != "arrive":
            if not isinstance(step.get("voiceInstructions"), list):
                errors.append(f"step {idx}: missing voiceInstructions")
            if not isinstance(step.get("bannerInstructions"), list):
                errors.append(f"step {idx}: missing bannerInstructions")
        for bearing_field in ("bearing_before", "bearing_after"):
            if bearing_field not in maneuver:
                errors.append(f"step {idx}: maneuver missing {bearing_field}")
                continue
            bearing_value = maneuver[bearing_field]
            if not isinstance(bearing_value, int) or not (0 <= bearing_value <= 359):
                errors.append(f"step {idx}: maneuver {bearing_field} must be int in [0..359]")
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
    candidates: List[ManeuverCandidate],
    points: List[Tuple[float, float]],
    route: Dict,
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
        writer.writerow(
            [
                "candidate_index",
                "step_index",
                "lat",
                "lon",
                "type",
                "modifier",
                "instruction",
                "heading_before",
                "heading_after",
                "delta",
                "road_from",
                "road_to",
                "emit",
                "reason",
            ]
        )
        for m in candidates:
            writer.writerow(
                [
                    m.candidate_index,
                    m.step_index if m.step_index is not None else "",
                    points[m.maneuver_idx][0],
                    points[m.maneuver_idx][1],
                    m.maneuver_type,
                    m.modifier or "",
                    m.instruction,
                    m.approach_heading,
                    m.departure_heading,
                    round(m.signed_delta, 3),
                    m.road_from,
                    m.road_to,
                    str(m.emit).lower(),
                    m.reason,
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
                    "type": m.maneuver_type,
                    "modifier": m.modifier,
                    "instruction": m.instruction,
                    "road_from": m.road_from,
                    "road_to": m.road_to,
                    "emit": m.emit,
                    "reason": m.reason,
                },
                "geometry": {"type": "Point", "coordinates": [points[m.maneuver_idx][1], points[m.maneuver_idx][0]]},
            }
            for m in candidates
        ],
    }
    (debug_dir / "maneuvers.geojson").write_text(json.dumps(maneuvers_geojson, indent=2), encoding="utf-8")

    instruction_features = []
    for step_index, step in enumerate(route.get("legs", [{}])[0].get("steps", [])):
        maneuver = step.get("maneuver", {})
        location = maneuver.get("location")
        if not (isinstance(location, list) and len(location) == 2):
            continue
        instruction_features.append(
            {
                "type": "Feature",
                "properties": {
                    "step_index": step_index,
                    "type": maneuver.get("type"),
                    "modifier": maneuver.get("modifier"),
                    "instruction": maneuver.get("instruction", ""),
                    "name": step.get("name", ""),
                    "distance": step.get("distance", 0.0),
                    "duration": step.get("duration", 0.0),
                    "bearing_before": maneuver.get("bearing_before"),
                    "bearing_after": maneuver.get("bearing_after"),
                    "exit": maneuver.get("exit"),
                },
                "geometry": {"type": "Point", "coordinates": location},
            }
        )
    instructions_geojson = {"type": "FeatureCollection", "features": instruction_features}
    (debug_dir / "instructions.geojson").write_text(json.dumps(instructions_geojson, indent=2), encoding="utf-8")


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
) -> Tuple[Dict, List[ManeuverCandidate]]:
    mode = mode_for_profile(profile)
    candidates = build_maneuver_candidates(
        points=points,
        runs=runs,
        locale=locale,
        continue_threshold_deg=continue_threshold_deg,
        turn_threshold_deg=turn_threshold_deg,
    )
    emitted_candidates = [candidate for candidate in candidates if candidate.emit]

    cumulative = cumulative_distances(points)
    depart_bearing_after = heading_around_index(points, cumulative, 0, 20.0, forward=True)
    events: List[Tuple[str, Optional[ManeuverCandidate], int]] = [("depart", None, 0)]
    events.extend(("candidate", candidate, candidate.maneuver_idx) for candidate in emitted_candidates)

    steps: List[Dict] = []
    for idx, (event_type, candidate, start_idx) in enumerate(events):
        next_start_idx = events[idx + 1][2] if idx + 1 < len(events) else len(points) - 1
        end_idx = max(start_idx, next_start_idx)

        if event_type == "depart":
            name = runs[0].name
            instruction = format_instruction("depart", None, "", name, locale)
            step = build_step(
                points=points,
                start_idx=start_idx,
                end_idx=end_idx,
                mode=mode,
                speed_kmh=speed_kmh,
                geometries=geometries,
                man_type="depart",
                modifier=None,
                bearing_before=None,
                bearing_after=depart_bearing_after,
                location=[points[0][1], points[0][0]],
                instruction=instruction,
                name=name,
            )
        else:
            assert candidate is not None
            candidate.step_index = len(steps)
            step = build_step(
                points=points,
                start_idx=start_idx,
                end_idx=end_idx,
                mode=mode,
                speed_kmh=speed_kmh,
                geometries=geometries,
                man_type=candidate.maneuver_type,
                modifier=candidate.modifier,
                bearing_before=candidate.approach_heading,
                bearing_after=candidate.departure_heading,
                location=[points[start_idx][1], points[start_idx][0]],
                instruction=candidate.instruction,
                name=candidate.step_name,
                roundabout_exit=candidate.roundabout_exit,
            )
        steps.append(step)

    steps = merge_continue_steps(steps, geometries=geometries, merge_below_m=merge_below_m)

    last_point = points[-1]
    if len(points) >= 2:
        arrive_bearing_before = bearing_deg(points[-2][0], points[-2][1], last_point[0], last_point[1])
    else:
        arrive_bearing_before = 0

    arrive_maneuver = {
        "type": "arrive",
        "location": [last_point[1], last_point[0]],
        "instruction": format_instruction("arrive", None, "", "", locale),
        "bearing_before": int(arrive_bearing_before),
        "bearing_after": int(arrive_bearing_before),
    }
    arrive_step = {
        "distance": 0.0,
        "duration": 0.0,
        "duration_typical": 0.0,
        "weight": 0.0,
        "name": "",
        "mode": mode,
        "driving_side": "right",
        "geometry": geometry_for(points[-2:] if len(points) >= 2 else [last_point, last_point], geometries),
        "maneuver": arrive_maneuver,
        "intersections": [intersection_for("arrive", [last_point[1], last_point[0]], None, None)],
        "voiceInstructions": [],
        "bannerInstructions": [],
        "_coords_latlon": points[-2:] if len(points) >= 2 else [last_point, last_point],
    }
    steps.append(arrive_step)

    leg_distance = sum(step["distance"] for step in steps)
    leg_duration = sum(step["duration"] for step in steps)

    route = {
        "distance": leg_distance,
        "duration": leg_duration,
        "duration_typical": leg_duration,
        "weight": leg_duration,
        "weight_name": "routability",
        "geometry": geometry_for(points, geometries),
        "legs": [
            {
                "summary": "RPP route",
                "distance": leg_distance,
                "duration": leg_duration,
                "duration_typical": leg_duration,
                "weight": leg_duration,
                "steps": steps,
            }
        ],
        "routeOptions": {
            "baseUrl": "https://api.mapbox.com",
            "user": "mapbox",
            "profile": mode,
            "coordinates": [[lon, lat] for lat, lon in points],
            "language": locale,
            "geometries": geometries,
            "steps": True,
            "voiceInstructions": True,
            "bannerInstructions": True,
            "roundaboutExits": True,
            "alternatives": False,
            "overview": "full",
        },
        "voiceLocale": locale,
    }
    for step in steps:
        step.pop("_coords_latlon", None)
    return route, candidates


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

        route_json, candidate_debug = build_route_json(
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
        validate_route_options_consistency(route_json, profile=args.profile, geometries=args.geometries)
        validate_route_polylines(route_json, geometries=args.geometries)

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
            write_debug_artifacts(args.debug_dir, matches, runs, candidate_debug, points, route_json)

        return 0

    except (FileNotFoundError, ET.ParseError, ValueError) as exc:
        print(f"Input/parse error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Internal error: {exc}", file=sys.stderr)
        return 4


if __name__ == "__main__":
    sys.exit(main())
