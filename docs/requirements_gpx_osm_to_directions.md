# Requirements: GPX + OSM → Mapbox/MapLibre `DirectionsRoute` JSON (offline)

This document specifies a Python CLI tool that converts an **RPP GPX track** (authoritative geometry) plus a local **OSM XML extract** (metadata + topology) into a **Mapbox Directions API–compatible route JSON** that can be parsed on Android as a `DirectionsRoute` (Mapbox Java Services model) via `DirectionsRoute.fromJson(...)`.

## Goals

- **Preserve the original optimized track exactly** (no probabilistic map matching / HMM “cleanup”).
- Generate **turn-by-turn guidance** (steps + maneuvers) using only local files:
  - `track.gpx` (polyline)
  - `area.osm` (ways/nodes/tags)
- Produce output JSON conforming to the **Mapbox Directions API v5 route object structure** (single route), including `legs[].steps[].maneuver`, so it can be used with MapLibre/Mapbox-style navigation UIs.

## Non-goals (for this version)

- Real-time rerouting, traffic, lane guidance, voice banners, congestion, speed limits.
- Perfect “legal” routing adherence (oneway/turn restrictions). We can optionally validate, but not required.
- Global scale / planet imports. Input is a small OSM extract covering the GPX area.

---

## CLI

### Command

```bash
python gpx_osm_to_directions.py \
  --gpx /path/to/rpp_route.gpx \
  --osm /path/to/area.osm \
  --out /path/to/route.json \
  --profile car \
  --locale en \
  --geometries polyline6
```

### Arguments

Required:
- `--gpx`: input GPX (track). Use `<trk>/<trkseg>/<trkpt>` as geometry.
- `--osm`: OSM XML extract (`.osm`) containing nodes/ways for the area.
- `--out`: output JSON file.

Optional:
- `--profile`: `"car" | "bike" | "foot"` (affects default speed model + some instruction wording). Default `car`.
- `--locale`: language tag for instruction text. Default `en`. (Only used for formatting; no i18n library required in v1.)
- `--geometries`: `"polyline" | "polyline6" | "geojson"` for route & step geometry. Default `polyline6`.
- `--speed-kmh`: constant speed (used to estimate durations). Default depends on profile (e.g., car 30 km/h, bike 18, foot 5).
- `--snap-radius-m`: radius for snapping GPX points to nearby OSM segments (for names/topology only). Default 20.
- `--turn-threshold-deg`: minimum heading change (degrees) to create a turn step. Default 30.
- `--continue-threshold-deg`: maximum heading change to be a “continue”. Default 15.
- `--debug-dir`: if set, write debug artifacts (CSV/GeoJSON) for snapped points, runs, maneuvers.

Exit codes:
- `0` success
- `2` invalid inputs / parsing failure
- `3` insufficient OSM coverage (too many GPX points cannot be annotated)
- `4` internal error

---

## Input assumptions

### GPX
- Contains a single track (or multiple; we use the first by default).
- Geometry is authoritative: we do **not** alter the shape except optional simplification for display.
- Coordinates are WGS84 lat/lon.

### OSM XML
- Contains `node` and `way` elements with tags (especially `highway`, `name`, `ref`, `junction=roundabout`).
- Extract covers the GPX bounding box + small buffer.

---

## Output format: Mapbox DirectionsRoute JSON

Write a **single JSON object** that is a Mapbox “route object” as returned by Directions API v5:

Top-level fields (minimum viable):
- `distance`: number (meters)
- `duration`: number (seconds)
- `weight`: number (use duration)
- `weight_name`: string (e.g., `"routability"`)
- `geometry`: string polyline (`polyline` or `polyline6`) OR GeoJSON LineString, depending on `--geometries`
- `legs`: array with **one** leg object
- Optional: `voiceLocale` (string, e.g. `"en"`)

Leg object (minimum viable):
- `summary`: string
- `distance`, `duration`, `weight`
- `steps`: array of step objects

Step object (minimum viable):
- `distance`, `duration`, `weight`
- `name`: street name or empty string
- `mode`: `"driving" | "cycling" | "walking"`
- `driving_side`: `"right"` (hardcode for DE/NL; configurable later)
- `geometry`: geometry for this step (same encoding as route geometry)
- `maneuver`: maneuver object
- Recommended (but can be minimal in v1): `intersections`: array with at least one intersection entry

Maneuver object (minimum viable):
- `type`: `"depart" | "turn" | "continue" | "arrive" | "roundabout"`
- `modifier`: `"left" | "right" | "straight" | "slight left" | "slight right" | "sharp left" | "sharp right" | "uturn"`
- `location`: `[lon, lat]`
- `bearing_before`: integer 0..360 (omit for `depart` if unknown; otherwise include)
- `bearing_after`: integer 0..360
- `instruction`: string
- For roundabouts: `exit` (integer) if determinable

Geometry encoding:
- If `--geometries polyline6`: encode as Google polyline with 1e-6 precision.
- If `--geometries polyline`: encode with 1e-5 precision.
- If `--geometries geojson`: use `{"type":"LineString","coordinates":[[lon,lat],...]}`.

---

## Algorithm requirements

### 1) Parse GPX track
- Extract ordered list `P = [(lat, lon), ...]` from `trkpt`.
- Compute total route distance (sum of haversine between consecutive points).
- Optional: simplify for display (Douglas–Peucker) but keep:
  - A) full points for turn detection, or
  - B) preserve critical points (turns) when simplifying.

### 2) Parse OSM and build an annotation graph
- Parse nodes: `node_id → (lat, lon)`.
- Parse ways where `highway` tag exists.
- For each way:
  - store `way_id`, ordered `node_ids`, tags (`name`, `ref`, `highway`, `junction`, `oneway`, `service`, etc.).
- Explode into directed/undirected segments for spatial search:
  - segment geometry (start, end; optionally densify with intermediate way nodes).
  - associated `way_id` and tags.
- Build a spatial index of segments:
  - **Recommended**: grid hash (tile size 50m–100m) for zero-dependency.
  - Alternative: STRtree (Shapely) if allowed.

### 3) Annotate each GPX point with nearest OSM way (no global path optimization)
Purpose: assign road names and detect intersections; **must not change geometry**.

For each GPX point `p`:
- Query nearby segments within `snap-radius-m`.
- Compute closest distance to each candidate segment (project point onto segment polyline).
- Choose nearest segment; record:
  - `matched_way_id`, `matched_name`, `matched_highway`, `proj_point`, `snap_error_m`.
- If no candidate found, mark as `unmatched`.
- Acceptance criterion: at least **80%** of points matched; otherwise exit code 3 (but still allow `--force` later).

### 4) Create “runs” (continuous portions on same way/name)
Compress consecutive points into runs:
- Start a new run when `matched_way_id` changes OR name changes.
- Each run stores:
  - `start_idx`, `end_idx`
  - `way_id`, `name`, `highway`, tags
  - `distance_m` along GPX points
  - representative bearings at start/end (for turn calc)

Edge cases:
- Short oscillations (A-B-A within a few points) due to snapping noise:
  - apply a smoothing rule: if a run has distance < `min_run_dist_m` (e.g. 10m), merge it with neighbors when names/ways indicate snapping jitter.

### 5) Detect maneuvers between runs
For boundary between run `i` and run `i+1`:
- Compute `bearing_before` from last N meters (e.g. last 3–5 points) of run i.
- Compute `bearing_after` from first N meters of run i+1.
- `delta = smallest_signed_angle(bearing_after - bearing_before)`.

Determine maneuver:
- If i==0: `depart` with instruction “Depart” (optionally “Head <dir>”).
- If i is last: `arrive` with instruction “Arrive at destination”.
- Otherwise:
  - If |delta| <= continue_threshold: type `continue`, modifier `straight`.
  - Else type `turn` with modifier based on delta magnitude:
    - 15–45: `slight left/right`
    - 45–135: `left/right`
    - >135: `uturn` (or `sharp left/right` depending on sign and threshold)
- Instruction text rules:
  - If next run has a non-empty name different from current: “Turn left onto {name}”
  - If same name: “Continue on {name}” or “Turn left to stay on {name}” (optional)
  - If name empty: omit “onto …”.

Roundabouts:
- If OSM tags indicate entering a roundabout (current or next way has `junction=roundabout`), use:
  - type `roundabout` (enter) and optionally a second step `roundabout` exit.
- **v1 acceptable**: represent as a single step at entry: “At roundabout, take exit X onto Y” **if** exit count can be computed; otherwise “Enter the roundabout and continue”.

Exit number (optional v1):
- Compute by analyzing the roundabout node graph:
  - Identify the roundabout ring way and the entry node (closest to maneuver location).
  - Walk along the ring in travel direction to the exit node where route leaves ring.
  - Count distinct outgoing non-roundabout highways encountered between entry and exit.

### 6) Build steps with geometry
Each step should cover the geometry from maneuver point to the next maneuver point.

Implementation:
- For each step boundary indices in GPX (e.g., run boundary indices), slice GPX points:
  - `step_coords = P[idx_start : idx_end+1]` (ensure at least 2 points)
- Encode step geometry per `--geometries`.

### 7) Compute distance/duration per step and route
Distance:
- Use haversine sum across step coords.

Duration:
- `duration_s = distance_m / (speed_kmh * 1000/3600)`
- Route duration = sum steps.

Weight:
- Use duration (float seconds).

### 8) Emit final DirectionsRoute JSON object
- One leg with all steps.
- Route geometry is full track geometry (encoded once).
- `legs[0].summary` can be built from start/end road names or a constant like “RPP route”.

---

## Debug artifacts (recommended)

When `--debug-dir` is set, write:
- `snapped_points.csv`: idx, lat, lon, way_id, name, snap_error_m
- `runs.csv`: run_index, start_idx, end_idx, way_id, name, dist_m
- `maneuvers.csv`: step_index, lat, lon, type, modifier, instruction, bearing_before, bearing_after
- `route.geojson`: original GPX LineString
- `maneuvers.geojson`: Point features with instruction text

---

## Acceptance tests

### A) Structural validity
- The output JSON must be parseable by Mapbox Java Services:
  - `DirectionsRoute.fromJson(jsonString)` must return a non-null route.
- Must contain:
  - top-level `distance`, `duration`, `geometry`, `legs[0].steps[*].maneuver`.

### B) Fidelity
- Route `geometry` decoded must match the input GPX geometry within epsilon:
  - If polyline encoding introduces rounding, max deviation ≤ 1m typical.

### C) Maneuver sanity
- Steps count > 2 for urban routes.
- Maneuver locations are on/near the track (within 10m).
- Distances are non-negative and sum approximately to route distance (±2%).

### D) Robustness
- Works for:
  - repeated street coverage (loops)
  - U-turn-like reversals
  - dense intersection grids

---

## Implementation constraints

- Python 3.10+.
- Prefer minimal dependencies; acceptable dependencies:
  - `gpxpy` (GPX parsing) OR stdlib XML parsing
  - `lxml` optional (speed)
  - For polyline: small pure-Python encoder/decoder (no external service)
- No network calls.

---

## Deliverables

1) `gpx_osm_to_directions.py` CLI script
2) `README.md` with usage examples
3) Sample output `route.json` for the provided inputs (optional)
4) Unit tests (optional v1):
   - bearing/delta classification
   - polyline encoding correctness
   - run smoothing behavior

---

## Notes for Android integration

- The produced JSON is intended to be consumed as a `DirectionsRoute`:
  - `DirectionsRoute route = DirectionsRoute.fromJson(jsonString);`
- You can then:
  - draw the route geometry on MapLibre/Mapbox map
  - iterate `route.legs().get(0).steps()` to display instructions
