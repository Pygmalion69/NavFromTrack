# NavFromTrack

# GPX + OSM to Mapbox/MapLibre Directions JSON

Offline converter for creating a Mapbox Directions API-style route JSON from:
- an authoritative GPX track geometry
- a local OSM XML extract for road metadata and turn inference

## Usage

```bash
python gpx_osm_to_directions.py \
  --gpx data/rpp_route.gpx \
  --osm data/area.osm \
  --out data/route.json \
  --profile car \
  --locale en \
  --geometries polyline6
```

## Arguments

Required:
- `--gpx`: GPX file with `<trk>/<trkseg>/<trkpt>`
- `--osm`: OSM XML extract (`.osm`)
- `--out`: output route JSON path

Optional:
- `--profile`: `car|bike|foot` (default `car`)
- `--locale`: instruction locale tag (default `en`)
- `--geometries`: `polyline|polyline6|geojson` (default `polyline6`)
- `--speed-kmh`: constant speed override (defaults: car `30`, bike `18`, foot `5`)
- `--snap-radius-m`: nearest-segment matching radius (default `20`)
- `--turn-threshold-deg`: turn threshold (default `30`)
- `--continue-threshold-deg`: continue threshold (default `15`)
- `--min-run-dist-m`: smoothing threshold for short jitter runs (default `10`)
- `--debug-dir`: emit debug CSV/GeoJSON files
- `--force`: allow output when OSM match coverage is below 80%

## Exit codes

- `0`: success
- `2`: invalid input / parsing failure
- `3`: insufficient OSM coverage (<80% matched points)
- `4`: internal error

## Output format

Emits a single Mapbox route object with:
- top-level `distance`, `duration`, `weight`, `weight_name`, `geometry`, `legs`, `voiceLocale`
- one leg with `steps[*].maneuver`
- step/route geometry encoded per `--geometries`

This is intended for Android ingestion via `DirectionsRoute.fromJson(...)`.

The converter now validates generated polyline geometry before writing output.
If any route or step polyline is invalid, it fails fast with a clear error instead
of writing corrupted JSON.

It also emits navigation-ready instruction metadata for Android import:
`routeOptions.voiceInstructions=true`, `routeOptions.bannerInstructions=true`,
`routeOptions.roundaboutExits=true`, and per-step `voiceInstructions` / `bannerInstructions`.
