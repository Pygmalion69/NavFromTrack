# Codex Task: Fix and Harden `gpx_osm_to_directions.py` for Mapbox DirectionsRoute Compatibility

This document defines the required modifications to `gpx_osm_to_directions.py` to ensure that the generated JSON:

- Is strictly valid JSON (no illegal escape sequences or control characters)
- Conforms structurally to Mapbox Directions API v5 route object expectations
- Is safe to parse using `DirectionsRoute.fromJson(...)`
- Produces navigation-friendly step structure (no roundabout spam, no micro-steps)

Reference schema:  
https://docs.mapbox.com/api/navigation/directions/

---

# 1️⃣ Ensure Strictly Valid JSON (No \x Escapes, No Control Characters)

## Required Changes

- Always serialize using:
  
  ```python
  serialized = json.dumps(route_json, ensure_ascii=False)
  ```

- Write using:

  ```python
  output_path.write_text(serialized, encoding="utf-8", newline="\n")
  ```

- Never print raw Python objects as output.
- Never rely on `repr()` of strings.
- Validate before writing:

  ```python
  json.loads(serialized)
  ```

## Additional Safety Check

Before writing JSON:

- Validate that every polyline geometry string:
  - Contains no control characters (`ord(ch) < 32`)
  - Does not contain `\x` sequences

If invalid characters are found:
- Raise a clear error identifying the step index.

## Acceptance Criteria

- `json.loads()` must succeed.
- `DirectionsRoute.fromJson(jsonString)` must parse without errors.

---

# 2️⃣ Fix `intersections` Structure (Bearings, Entry, In, Out)

Mapbox requires:

- `bearings` array
- `entry` array of same length
- Optional `in` and `out` indices pointing into the bearings array

## Required Logic

For non-depart / non-arrive steps:

```json
{
  "bearings": [bearing_before, bearing_after],
  "entry": [true, true],
  "in": 0,
  "out": 1
}
```

For `depart`:

```json
{
  "bearings": [bearing_after],
  "entry": [true],
  "out": 0
}
```

For `arrive`:

```json
{
  "bearings": [],
  "entry": []
}
```

## Invariants

- `len(entry) == len(bearings)`
- `in` and `out` (if present) must be valid indices

---

# 3️⃣ Fix Arrive Step Geometry

## Required Change

For the final `arrive` step:

- Remove the `geometry` field entirely
  OR
- Set it to an empty string `""`

Do NOT allow polyline artifacts such as `"??"`.

---

# 4️⃣ Collapse Roundabout Spam

Currently multiple consecutive `roundabout` steps are emitted.

## Required Behavior

Detect a continuous block of runs where:

```
tags["junction"] == "roundabout"
```

Emit:

1. One roundabout step at entry
2. One normal step for the exit

### Instruction Format

If exit count is known:

```
At roundabout, take exit {n} onto {street_name}
```

If not:

```
Enter the roundabout and continue
```

## Acceptance

No consecutive repeated `roundabout` steps.

---

# 5️⃣ Merge Micro-Steps (Continue Spam)

## Add Merge Rule

Merge step A and B when:

- Both have `maneuver.type == "continue"`
- Same `name` (or both empty)
- Combined distance < configurable threshold (default 40m)

Add CLI flag:

```
--merge-below-m 40
```

## Merge Behavior

- Sum distance/duration/weight
- Concatenate coordinates and re-encode polyline
- Preserve first maneuver

## Acceptance

Significantly fewer short “Continue …” steps.

---

# 6️⃣ Add Schema Self-Validation

Before writing JSON:

Validate for every step:

- `distance`, `duration`, `mode` exist
- `maneuver.location` exists and is `[lon, lat]`
- `maneuver.type` exists
- `len(entry) == len(bearings)`
- `in` / `out` indices (if present) valid

If validation fails:
- Raise descriptive error including step index.

---

# 7️⃣ Optional: Add `--strict-mapbox` Flag

If enabled:
- Schema violations become hard errors.
- Roundabout collapsing must occur.
- Micro-step merging must occur.

---

# Final Acceptance Checklist

✔ JSON parses with `json.loads()`  
✔ Android `DirectionsRoute.fromJson()` works  
✔ No control characters in polyline strings  
✔ No repeated roundabout spam  
✔ Reduced micro-step spam  
✔ Valid intersection objects  

---

End of Codex task document.
