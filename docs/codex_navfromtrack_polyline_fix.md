# Codex task: fix NavFromTrack polyline generation and validation for imported DirectionsRoute rendering

## Goal

Update the NavFromTrack Python script so the generated `DirectionsRoute` JSON contains valid, renderable polyline geometry for MapLibre / Mapbox Android navigation UI.

Parsing already works on Android. The current failure happens later, when the app tries to render the imported route:

- `DirectionsRoute` parsing succeeds
- route activation succeeds
- crash occurs in polyline decoding during route rendering

Observed Android crash:

```text
java.lang.StringIndexOutOfBoundsException: length=1902; index=1902
    at java.lang.String.charAt(Native Method)
    at org.maplibre.geojson.utils.PolylineUtils.decode(PolylineUtils.kt:108)
    at org.maplibre.geojson.LineString$Companion.fromPolyline(LineString.kt:202)
    at org.maplibre.geojson.LineString.fromPolyline(Unknown Source:2)
    at org.maplibre.navigation.android.navigation.ui.v5.route.FeatureProcessingTask.createRouteFeatureCollection(FeatureProcessingTask.java:74)
```

This strongly suggests the generated polyline string is malformed, truncated, or modified after encoding.

## Important context

The current NavFromTrack script:

- builds a top-level route geometry
- builds step geometries
- supports `polyline`, `polyline6`, and `geojson`
- contains post-processing functions that sanitize geometry strings:
  - `sanitize_geometry_string(...)`
  - `sanitize_route_geometries(...)`

Those sanitization functions are dangerous for encoded polyline strings because every character matters.

## Required outcome

After the fix:

1. The generated top-level `route["geometry"]` must always be a valid encoded polyline when `--geometries polyline6` or `polyline` is used.
2. Every `step["geometry"]` must also be valid.
3. The script must fail loudly instead of writing corrupted geometry.
4. The script must not mutate encoded polyline strings after encoding.
5. The Android app must be able to render the imported route without crashing.

## Required changes

## 1) Remove post-encoding geometry mutation

Find and remove or disable all logic that rewrites encoded polyline strings after encoding.

Specifically inspect and change:

- `sanitize_geometry_string(...)`
- `sanitize_route_geometries(...)`

These functions currently try to “repair” strings by decoding `\xHH`, dropping markers, or replacing characters. That is not safe for polyline strings.

### Required action

Do one of these:

- remove these functions entirely, or
- stop calling them for route and step geometry

The route geometry must be treated as opaque encoded data once created.

### Important rule

After `encode_polyline(...)` returns a string, do not transform that string at all.

## 2) Add strict polyline validation immediately after encoding

Create a validation function that decodes the produced polyline using the same precision that was used during encoding.

Implement a pure Python polyline decoder if the repo does not already contain one.

Suggested API:

```python
def decode_polyline(encoded: str, precision: int) -> list[tuple[float, float]]:
    ...
```

Then add:

```python
def assert_valid_polyline(encoded: str, precision: int, label: str) -> None:
    coords = decode_polyline(encoded, precision)
    if not coords or len(coords) < 2:
        raise ValueError(f"{label}: decoded polyline is empty or too short")
```

## 3) Validate both route geometry and step geometry

After building the route object, validate:

- `route["geometry"]`
- every `step["geometry"]` for every leg

Validation must happen before writing JSON to disk.

Suggested logic:

```python
precision = 6 if geometries == "polyline6" else 5 if geometries == "polyline" else None

if precision is not None:
    assert_valid_polyline(route["geometry"], precision, "route geometry")

    for leg_index, leg in enumerate(route["legs"]):
        for step_index, step in enumerate(leg["steps"]):
            geom = step.get("geometry")
            if geom:
                assert_valid_polyline(geom, precision, f"leg {leg_index} step {step_index} geometry")
```

If validation fails, exit with a clear error and do not write output.

## 4) Keep geometry mode consistent

The Android app is rendering via polyline decoding, so NavFromTrack must emit a polyline string format that matches the route options.

Preferred output mode for this workflow:

- `polyline6`

Ensure:

- route geometry is encoded with precision 6 when `--geometries polyline6`
- step geometry is encoded with precision 6 when `--geometries polyline6`
- `routeOptions["geometries"] == "polyline6"`

Do not mix precisions between route and steps.

## 5) Add explicit routeOptions consistency checks

The script already emits `routeOptions`.

Before writing the output, verify:

```python
if route.get("routeOptions", {}).get("geometries") != geometries:
    raise ValueError("routeOptions.geometries does not match actual geometry encoding")
```

Also ensure:

- `steps` is `True`
- `coordinates` is present
- `profile` is consistent with the selected mode

## 6) Add useful debug output

When validation fails, include:

- whether it was route or step geometry
- geometry type (`polyline` / `polyline6`)
- encoded string length
- first 80 chars
- last 80 chars

Suggested helper:

```python
def polyline_debug_snippet(encoded: str) -> str:
    head = encoded[:80]
    tail = encoded[-80:] if len(encoded) > 80 else encoded
    return f"len={len(encoded)} head={head!r} tail={tail!r}"
```

Then:

```python
raise ValueError(f"Invalid route geometry: {polyline_debug_snippet(route['geometry'])}")
```

## 7) Optional but recommended: validate during construction too

Where practical, validate immediately after creating geometry strings in:

- top-level route geometry creation
- each step creation

That makes it easier to isolate which part broke first.

## 8) Do not “fix” malformed output silently

If a polyline is invalid:

- raise a clear exception
- return a non-zero exit code
- do not substitute placeholder values
- do not replace characters
- do not truncate
- do not attempt heuristic repair

Failing fast is preferred.

## 9) Add automated tests

Add at least lightweight tests for:

### Test A: encode/decode roundtrip
A small coordinate list should survive:

```python
coords = [
    (51.7879486, 6.1436697),
    (51.7879511, 6.1438125),
    (51.7881000, 6.1440000),
]
```

Test both:

- precision 5
- precision 6

### Test B: route geometry validation
Given a generated route object, validating the top-level geometry should pass.

### Test C: step geometry validation
Every step geometry in a generated sample route should pass validation.

### Test D: malformed geometry rejection
A deliberately broken polyline string should raise a clear error.

## Suggested implementation plan

### Task 1
Inspect the current script and identify everywhere geometry strings are mutated after encoding.

### Task 2
Remove geometry sanitization from the polyline path.

### Task 3
Implement a strict polyline decoder and validator.

### Task 4
Validate route and step geometry before serialization.

### Task 5
Add tests for roundtrip and malformed input.

### Task 6
Update README / docs with one short note:

- NavFromTrack now validates generated polyline geometry before writing output
- invalid geometry causes the script to fail instead of producing broken JSON

## Strong suspicion to confirm

The most suspicious current behavior is the post-processing around:

- `sanitize_geometry_string(...)`
- `sanitize_route_geometries(...)`

That logic likely corrupts otherwise valid encoded polylines.

Codex should verify whether removing that logic fixes the crash before doing anything more invasive.

## Acceptance criteria

This task is complete when all of the following are true:

1. NavFromTrack no longer mutates encoded polyline strings after encoding.
2. The script validates top-level route geometry before writing JSON.
3. The script validates step geometry before writing JSON.
4. Invalid geometry causes a clear failure instead of producing output.
5. Generated `polyline6` output has matching `routeOptions["geometries"]`.
6. Automated tests cover encode/decode roundtrip and malformed geometry rejection.
7. The Android app can render the imported route without the `PolylineUtils.decode(...)` crash.

## Final output expected from Codex

Provide:

1. the code changes
2. a short explanation of the root cause
3. whether geometry sanitization was removed or bypassed
4. how geometry is now validated
5. any assumptions about the expected geometry format (`polyline6` preferred)
