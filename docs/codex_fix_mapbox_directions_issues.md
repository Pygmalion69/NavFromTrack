# Codex task: Fix Mapbox/MapLibre `DirectionsRoute` JSON output (GPX+OSM → DirectionsRoute)

You now have:

- Script: `gpx_osm_to_directions.py`
- Output sample: `route_test.json`

The JSON **mostly** follows the Mapbox Java `DirectionsRoute` model, but there are a few issues that will break (or at least risk breaking) parsing and polyline decoding on Android.

This task is to **adjust the Python script** so it produces **strict, Mapbox-compatible JSON** that can be parsed into:

- `com.mapbox.api.directions.v5.models.DirectionsRoute` (v6.4.0)

Reference model docs:
- `DirectionsRoute` (fields: distance/duration/weight/geometry/legs/…)
- `RouteLeg`, `LegStep`, `StepManeuver`, `StepIntersection`, …

---

## 1) Critical bug: `\xHH` sequences inside `geometry` strings

### Symptom (seen in `route_test.json`)
The route `geometry` contains literal escape-like text such as:

- `... f@hC}\xAwJ` …

This is **not valid polyline content** for Mapbox’s decoder, because the string literally contains the two characters `\` and `x`, instead of the intended single byte/character.

### Root cause (likely)
Some internal polyline/byte handling produced a **Python-style “hex escape”** representation (like `\x1a`) **as literal characters**, and then the string was JSON-encoded without converting it back.

### Required fix
Before writing JSON, sanitize every polyline string:

- the top-level `route["geometry"]`
- each step `step["geometry"]`
- (optionally) any other geometry-bearing fields you add later

#### Correct sanitization behavior
Convert every occurrence of the pattern:

- `\xHH` (where `HH` is 2 hex digits)

into the single character with that byte value.

In Python terms:

- replace `r"\\x([0-9a-fA-F]{2})"` with `chr(int(group1, 16))`

Then, when you `json.dump/json.dumps`, the JSON writer will correctly escape control characters as `\u00XX` where needed.

### Acceptance tests
- After sanitization, the serialized JSON **must not contain** the substring `\x` anywhere.
- Mapbox’s polyline decoder should successfully decode the route geometry and every step geometry without throwing.

---

## 2) Make the output “drop-in parseable” as a `DirectionsRoute`

### Required output form
Produce **exactly one JSON object** representing a `DirectionsRoute` (not a `DirectionsResponse` wrapper), because the app parses it directly as a route object.

Required top-level fields:
- `distance` (meters, float)
- `duration` (seconds, float)
- `weight` (float)
- `weight_name` (string)
- `geometry` (polyline6 string)
- `legs` (list with at least one `leg`)

Optional but recommended (if you can populate consistently):
- `voiceLocale` (string, e.g. `"en"`)

---

## 3) Improve intersection data consistency (`bearings`, `entry`, `in`, `out`)

Your current output already includes `in/out` sometimes and passes a basic schema check, but make it robust:

### Requirements
For every `StepIntersection`:
- `location`: `[lon, lat]`
- `bearings`: list of ints in degrees `[0..359]`
- `entry`: list of booleans, same length as `bearings`
- `in`: index into `bearings` representing the incoming bearing (omit for the very first depart intersection if unknown)
- `out`: index into `bearings` representing the outgoing bearing (omit for arrive/end if unknown)

### Acceptance tests
- For any intersection that has `in` or `out`, `0 <= in/out < len(bearings)`.
- `len(entry) == len(bearings)` for all intersections.
- `bearings` values are ints within `[0..359]`.

---

## 4) Maneuver correctness: keep Mapbox types/modifiers sane

Your output currently uses `type` values like `depart`, `turn`, `continue`, `arrive` and sometimes `roundabout`.

### Requirements
- Ensure `maneuver.type` is one of the Mapbox-supported types (at least the ones you already use).
- When `type == "turn"`, include a `modifier` when possible (e.g. `left/right/slight left/sharp right/uturn/straight`).
- For roundabouts:
  - Prefer `type: "roundabout"` with `modifier` optional, and include `exit`/`exit_number` if you compute it.
  - If you cannot compute exit numbers reliably, keep it simple and do not invent them.

### Acceptance test
- No step has `maneuver.type` set to a value not recognized by Mapbox’s models (don’t add new exotic types without verifying).

---

## 5) Re-run and validate on the provided files

Use these local files for your test run:
- OSM: `area.osm`
- GPX: `rpp_route.gpx`

Command (example):
```bash
python3 gpx_osm_to_directions.py --osm area.osm --gpx rpp_route.gpx --out route_fixed.json
```

### Acceptance tests on output
1. `route_fixed.json` parses into `DirectionsRoute` on Android.
2. `route_fixed.json` contains **no** literal `\x` substring.
3. Route polyline decodes and renders on the map.
4. Step-by-step instructions can be shown (maneuver instruction strings exist and are non-empty for most steps).

---

## 6) Deliverables

- Updated `gpx_osm_to_directions.py`
- A short note in the script header describing:
  - why `\xHH` sanitization is necessary
  - which fields are sanitized
- A regenerated `route_fixed.json` demonstrating the fix
