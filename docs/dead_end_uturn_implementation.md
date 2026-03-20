# Dead-End Street & U-Turn Handling – Implementation Guide

## Project
NavFromTrack  
File: gpx_osm_to_directions.py

## Objective

Extend the maneuver generation logic to correctly handle dead-end streets.

### Required behavior

Given a route:

- Königsgarten (eastbound)
- → left into In Den Galleien (dead-end)
- → reach end
- → turn around
- → return to Königsgarten
- → left onto Königsgarten (westbound)

The generated instructions must be:

1. Turn left onto In Den Galleien
2. Make a U-turn
3. Turn left onto Königsgarten

## Current Problem

The current algorithm:
- relies on run boundaries + heading deltas
- uses fixed heading windows
- applies same-road suppression

This causes:
- missing entry turn
- missing U-turn
- suppression of valid maneuvers
- dead-end behavior interpreted as noise

## Key Insight

Dead-end handling requires explicit event detection, not just better thresholds.

## Implementation Plan

### 1. Introduce explicit maneuver types

Extend maneuver logic to support:
- turn
- continue
- roundabout
- arrive
- uturn (NEW internal type)

Output mapping:
{
  "type": "turn",
  "modifier": "uturn"
}

### 2. Add dead-end / reversal detection pass

After run construction:
runs = smooth_runs(...)
candidates = build_maneuver_candidates(...)
candidates = detect_dead_end_reversals(candidates, runs, points)

### 3. Detect reversal events

Geometric condition:
heading change ≈ 180° (threshold ≥ 150°)

Optional signals:
- path retraces itself
- same road name reversed
- short spur length

### 4. Create U-turn maneuver candidates

candidate = ManeuverCandidate(
    type="turn",
    modifier="uturn",
    location=point,
    source_kind="dead_end_reversal",
    forced_emit=True
)

Rules:
- ALWAYS emit
- NEVER suppressed

### 5. Preserve three maneuvers

- Entry turn
- U-turn
- Exit turn

### 6. Disable suppression for dead-end cases

if candidate.forced_emit:
    emit
elif same_road:
    suppress

### 7. Improve heading calculation

window = min(heading_window_m, run_length / 2)

### 8. Instruction generation

if modifier == "uturn":
    return "Make a U-turn"

### 9. Debug output

Add:
- source_kind
- forced_emit
- reversal_detected
- angle
- suppressed_reason

### 10. Tests

Test A: Dead-end → expect 3 maneuvers  
Test B: Short dead-end → expect 3 maneuvers  
Test C: Same-road bend → no U-turn  
Test D: True U-turn → detect

## Acceptance Criteria

- Entry turn emitted
- U-turn emitted
- Exit turn emitted
- No suppression removes them
- Works for short spurs
- No regressions
- Debug output explains decisions

## Codex Prompt

Update gpx_osm_to_directions.py to explicitly support dead-end streets.

Requirements:
1. Emit the turn onto a dead-end street.
2. Emit a U-turn maneuver at the end of the dead-end.
3. Emit the turn when leaving the dead-end and rejoining the prior road.
4. Do not suppress these maneuvers due to same-road logic.
5. Keep output Mapbox/MapLibre compatible using type="turn" and modifier="uturn".
6. Add regression tests.
7. Extend debug artifacts.

Implementation notes:
- Add dead-end / reversal detection pass
- Detect reversals from ~180° heading change
- Keep three separate maneuvers
- Avoid relying only on heading thresholds
- Preserve roundabout handling
