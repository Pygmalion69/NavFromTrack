
# Making NavFromTrack emit a complete `DirectionsRoute`

The `gpx_osm_to_directions.py` script currently writes a minimal route JSON
containing only a handful of fields (`distance`, `duration`, `weight`,
`weight_name`, `geometry`, `legs`, and `voiceLocale`). While this
is sufficient for basic parsing via `DirectionsRoute.fromJson(...)`, the
Mapbox/MapLibre navigation SDK also defines optional fields such as
`duration_typical` and `routeOptions` that are commonly present in
Directions API responses. Adding these fields eliminates "missing
field" warnings and more closely matches the structure returned by
the Directions API.

This guide proposes modifications to **NavFromTrack** so the script
produces a single, complete `DirectionsRoute` object with no missing
fields. It does **not** wrap the route in a `DirectionsResponse`; the
output remains a plain route dictionary.

---

## Additional top‑level properties

### 1. `duration_typical`

Typical duration for the entire route.

Since NavFromTrack currently calculates a single duration value, the
simplest implementation is:

```
route["duration_typical"] = route["duration"]
```

---

### 2. `routeOptions`

A dictionary describing how the route was generated. This mirrors what
the Mapbox Directions API normally returns.

Minimum recommended fields:

```
routeOptions = {
    "baseUrl": "https://api.mapbox.com",
    "user": "mapbox",
    "profile": mode_for_profile(profile),
    "coordinates": [[lon, lat] for lat, lon in points],
    "language": locale,
    "geometries": geometries,
    "steps": True,
    "alternatives": False,
    "overview": "full"
}
```

Then attach:

```
route["routeOptions"] = routeOptions
```

---

### 3. Ensure `weight_name` exists

Your script already sets:

```
"weight_name": "routability"
```

Ensure this field remains present.

---

## Leg‑level additions

Each leg should also include `duration_typical`.

Add after the route is constructed:

```
for leg in route["legs"]:
    leg["duration_typical"] = leg["duration"]
```

---

## Optional step‑level additions

These are optional, but if desired you can add:

```
step["duration_typical"] = step["duration"]
```

Additional optional fields sometimes present in Mapbox responses:

```
voiceInstructions: []
bannerInstructions: []
ref: ""
destinations: ""
exits: ""
```

The MapLibre parser tolerates missing fields, so these are not required.

---

## Integration point in the script

After the `route` dictionary is built inside `build_route_json`, add:

```
route["duration_typical"] = route["duration"]

route_options = {
    "baseUrl": "https://api.mapbox.com",
    "user": "mapbox",
    "profile": mode_for_profile(profile),
    "coordinates": [[lon, lat] for lat, lon in points],
    "language": locale,
    "geometries": geometries,
    "steps": True,
    "alternatives": False,
    "overview": "full"
}

route["routeOptions"] = route_options

for leg in route["legs"]:
    leg["duration_typical"] = leg["duration"]
```

---

## Resulting structure

Example output structure:

```
{
  "distance": 8804.83,
  "duration": 1056.58,
  "duration_typical": 1056.58,
  "weight": 1056.58,
  "weight_name": "routability",
  "geometry": "...polyline...",
  "legs": [
    {
      "summary": "RPP route",
      "distance": 8804.83,
      "duration": 1056.58,
      "duration_typical": 1056.58,
      "weight": 1056.58,
      "steps": [ ... ]
    }
  ],
  "routeOptions": {
    "baseUrl": "https://api.mapbox.com",
    "user": "mapbox",
    "profile": "driving",
    "coordinates": [[lon, lat], ...],
    "language": "en",
    "geometries": "polyline6",
    "steps": true,
    "alternatives": false,
    "overview": "full"
  },
  "voiceLocale": "en"
}
```

---

## Summary

To produce a complete `DirectionsRoute` object:

1. Add `duration_typical` at route level.
2. Add `duration_typical` at leg level.
3. Add a `routeOptions` object describing generation parameters.
4. Ensure `weight_name` remains present.

With these additions NavFromTrack will emit a fully populated
`DirectionsRoute` JSON object that can be parsed directly using:

```
DirectionsRoute.fromJson(...)
```
