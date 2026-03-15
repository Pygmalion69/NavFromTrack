# Codex task: make NavFromTrack emit navigation-ready voice and banner instructions

## Goal

Update the NavFromTrack Python script so the generated `DirectionsRoute` is not only parseable and renderable, but also accepted by the MapLibre Navigation UI when starting navigation.

Current state:

- route import works
- route rendering works
- navigation start crashes with:

```text
IllegalStateException: Using the default milestones requires the directions route to be requested with voice instructions enabled.
```

This means the imported route is missing navigation-ready instruction metadata.

## Required outcome

After this change, NavFromTrack must emit a single `DirectionsRoute` object that is accepted by the Android navigation stack for actual navigation start, not just display.

The generated JSON must include:

1. `routeOptions.voiceInstructions = true`
2. `routeOptions.bannerInstructions = true`
3. `routeOptions.roundaboutExits = true`
4. per-step `voiceInstructions`
5. per-step `bannerInstructions`

The generated instructions do not need to be perfect. They do need to be structurally valid and consistent with the existing maneuver/instruction data.

## Important context

NavFromTrack already generates:

- route geometry
- leg steps
- maneuver objects
- human-readable maneuver instruction text

So the script already has enough information to generate basic voice and banner instructions.

Use the existing step instruction text as the basis for the generated spoken and visual instructions.

## Required changes

## 1) Extend routeOptions

Ensure the top-level `routeOptions` object contains these fields:

```json
{
  "voiceInstructions": true,
  "bannerInstructions": true,
  "roundaboutExits": true
}
```

Add them alongside the already emitted fields like:

- `baseUrl`
- `user`
- `profile`
- `coordinates`
- `language`
- `geometries`
- `steps`

Example:

```python
route_options = {
    "baseUrl": "https://api.mapbox.com",
    "user": "mapbox",
    "profile": mode_for_profile(profile),
    "coordinates": [[lon, lat] for lat, lon in points],
    "language": locale,
    "geometries": geometries,
    "steps": True,
    "alternatives": False,
    "overview": "full",
    "voiceInstructions": True,
    "bannerInstructions": True,
    "roundaboutExits": True,
}
```

## 2) Add per-step voiceInstructions

Each navigable step should include a `voiceInstructions` array.

Generate at least one voice instruction per non-arrive step.

Minimum valid structure per entry:

```json
{
  "distanceAlongGeometry": 80.0,
  "announcement": "Turn right onto Kalkarer Straße",
  "ssmlAnnouncement": "<speak>Turn right onto Kalkarer Straße</speak>"
}
```

Implementation guidance:

- use the existing maneuver instruction text as `announcement`
- wrap the same text in a simple SSML envelope for `ssmlAnnouncement`
- choose a sensible `distanceAlongGeometry`
- clamp it so it does not exceed the step distance

Recommended helper:

```python
def build_voice_instructions(step_distance_m: float, instruction: str) -> list[dict]:
    if not instruction:
        return []

    distance_along = min(80.0, max(5.0, step_distance_m * 0.5))
    return [{
        "distanceAlongGeometry": float(distance_along),
        "announcement": instruction,
        "ssmlAnnouncement": f"<speak>{instruction}</speak>",
    }]
```

For very short steps, ensure `distanceAlongGeometry` remains positive and not larger than the step distance.

## 3) Add per-step bannerInstructions

Each navigable step should include a `bannerInstructions` array.

Use a minimal valid structure.

Recommended structure:

```json
[
  {
    "distanceAlongGeometry": 80.0,
    "primary": {
      "text": "Kalkarer Straße",
      "type": "turn",
      "modifier": "right"
    },
    "sub": null
  }
]
```

Implementation guidance:

- `distanceAlongGeometry` can match the same value used for voice instructions
- `primary.text` should prefer:
  1. step name if present
  2. otherwise the maneuver instruction text
- `primary.type` should come from `maneuver.type`
- `primary.modifier` should come from `maneuver.modifier` when present
- `sub` may be omitted or set to `None`

Recommended helper:

```python
def build_banner_instructions(
    step_distance_m: float,
    instruction: str,
    step_name: str,
    maneuver_type: str,
    maneuver_modifier: str | None,
) -> list[dict]:
    text = step_name or instruction or ""
    if not text:
        return []

    distance_along = min(80.0, max(5.0, step_distance_m * 0.5))

    primary = {
        "text": text,
        "type": maneuver_type,
    }
    if maneuver_modifier:
        primary["modifier"] = maneuver_modifier

    return [{
        "distanceAlongGeometry": float(distance_along),
        "primary": primary,
    }]
```

## 4) Attach voiceInstructions and bannerInstructions inside step creation

Update the place where each step dictionary is created.

The current step object already contains:

- `distance`
- `duration`
- `weight`
- `name`
- `mode`
- `driving_side`
- `geometry`
- `maneuver`
- `intersections`

Extend it to include:

- `voiceInstructions`
- `bannerInstructions`

For example:

```python
voice_instructions = build_voice_instructions(distance_m, instruction)
banner_instructions = build_banner_instructions(
    step_distance_m=distance_m,
    instruction=instruction,
    step_name=name,
    maneuver_type=man_type,
    maneuver_modifier=modifier,
)

step = {
    "distance": distance_m,
    "duration": duration_s,
    "weight": duration_s,
    "name": name,
    "mode": mode,
    "driving_side": "right",
    "geometry": geometry_for(coords, geometries),
    "maneuver": maneuver,
    "intersections": [intersection_for(man_type, location, bearing_before, bearing_after)],
    "voiceInstructions": voice_instructions,
    "bannerInstructions": banner_instructions,
}
```

## 5) Handle arrive step sensibly

For the final `arrive` step:

- `voiceInstructions` may be empty
- `bannerInstructions` may be empty

That is acceptable.

Example:

```python
"voiceInstructions": [],
"bannerInstructions": []
```

## 6) Ensure maneuver types and modifiers remain compatible

The banner primary object uses `type` and `modifier`.

Make sure the generated values remain aligned with the existing maneuver generation logic.

Expected values should stay like:

- `depart`
- `turn`
- `continue`
- `roundabout`
- `arrive`

Modifiers like:

- `left`
- `right`
- `slight left`
- `slight right`
- `sharp left`
- `sharp right`
- `uturn`

Do not invent new values.

## 7) Add minimal validation

Extend route validation so it checks:

- `routeOptions.voiceInstructions is True`
- `routeOptions.bannerInstructions is True`
- each non-arrive step has a `voiceInstructions` array
- each non-arrive step has a `bannerInstructions` array

Example logic:

```python
if route["routeOptions"].get("voiceInstructions") is not True:
    errors.append("routeOptions.voiceInstructions must be true")

if route["routeOptions"].get("bannerInstructions") is not True:
    errors.append("routeOptions.bannerInstructions must be true")
```

For steps:

```python
maneuver_type = step.get("maneuver", {}).get("type")
if maneuver_type != "arrive":
    if "voiceInstructions" not in step:
        errors.append(f"step {idx}: missing voiceInstructions")
    if "bannerInstructions" not in step:
        errors.append(f"step {idx}: missing bannerInstructions")
```

## 8) Keep the implementation simple

This task is about producing **valid navigation-ready structure**, not perfect natural-language guidance.

Use the current instruction text as-is.

Examples:

- `"Depart onto Kalkarer Straße"`
- `"Turn right onto Kalkarer Straße"`
- `"Continue on Kalkarer Straße"`
- `"Arrive at destination"`

That is good enough for now.

## 9) Update README / docs

Add a short note that NavFromTrack now emits navigation-ready route steps with:

- voice instructions
- banner instructions
- route options enabling both

Also mention that this is intended for direct import into the Android navigation app.

## Acceptance criteria

This task is complete when all of the following are true:

1. The generated `DirectionsRoute` still parses successfully.
2. The route still renders successfully.
3. `routeOptions.voiceInstructions` is `true`.
4. `routeOptions.bannerInstructions` is `true`.
5. Non-arrive steps contain `voiceInstructions`.
6. Non-arrive steps contain `bannerInstructions`.
7. Starting navigation in the Android app no longer fails with:
   `Using the default milestones requires the directions route to be requested with voice instructions enabled.`
8. The implementation stays simple and uses the existing instruction text.

## Final output expected from Codex

Provide:

1. the code changes
2. a short explanation of how voice and banner instructions are now generated
3. one example generated step showing:
   - `maneuver`
   - `voiceInstructions`
   - `bannerInstructions`
4. any assumptions made about the MapLibre / Mapbox instruction schema
