
# NavFromTrack – Route Guidance Specification (v2)

## Purpose

This document defines the requirements and implementation guidelines for generating **accurate turn‑by‑turn navigation instructions** from a **GPX track combined with local OSM data**.

The GPX track represents the **authoritative travel path**. OSM data is used only to enrich the route with **road names, classifications, and junction metadata**.

The goal is to generate instructions compatible with navigation frameworks such as **MapLibre Navigation / Mapbox DirectionsRoute**.

---

# 1. Core Design Principle

## GPX traversal is the source of truth

The ordered GPX point sequence determines:

- travel direction
- maneuver geometry
- entry and exit of junctions
- roundabout traversal

OSM data **must never override the direction implied by the GPX track**.

OSM segmentation (way_id boundaries) must not be interpreted as maneuvers.

---

# 2. Known Problems in the Current Implementation

## 2.1 Maneuvers generated at OSM segmentation boundaries

Current logic splits runs when:

- `way_id` changes
- `name` changes

These boundaries are incorrectly treated as navigation decisions.

Result:

- false turns
- excessive "continue on ..." instructions

Requirement:

Run segmentation must be **decoupled from maneuver generation**.

---

## 2.2 Incorrect left/right direction

Left/right classification is currently computed as:

```
delta = bearing_after - bearing_before
```

But the bearings are taken from short windows at run boundaries.

This may measure:

- geometry noise
- intermediate road fragments
- wrong approach segments

Requirement:

Turn direction must be computed from **stable headings before and after the maneuver point along the GPX path**.

---

## 2.3 Incomplete roundabout implementation

Current implementation:

- detects roundabout segments
- emits `"Enter the roundabout and continue"`
- exit count is always `None`

Requirement:

Roundabouts must produce instructions of the form:

```
At the roundabout, take the Nth exit onto <road>
```

---

## 2.4 Excessive continue instructions

The system currently emits:

```
Continue on <road name>
```

for most run boundaries.

These instructions add noise and should be suppressed unless they clarify routing at junctions.

---

# 3. Architecture

The algorithm must be divided into **three layers**.

```
GPX + OSM
    ↓
Matched path segmentation
    ↓
Maneuver detection
    ↓
Instruction synthesis
```

---

# 4. Layer A – Path Segmentation

Purpose:

Segment the matched GPX path into logical road runs.

Allowed segmentation criteria:

- OSM way_id
- road name
- highway class
- roundabout membership

Important:

These segments are **internal metadata only** and must not automatically produce instructions.

---

# 5. Layer B – Maneuver Detection

A maneuver candidate exists when the GPX path shows a **meaningful directional change at a junction**.

A maneuver shall be emitted only if **at least one condition holds**:

### Significant angle change

Example thresholds:

| Turn Type | Angle |
|-----------|------|
| Continue | < 15° |
| Slight | 15°–45° |
| Normal turn | 45°–135° |
| Sharp | >135° |

### Road identity change

Examples:

- primary → residential
- residential → service road
- entering a link road

### Junction decision

Topology indicates multiple possible continuations.

### Special structures

- roundabout
- fork
- merge
- u‑turn

---

# 6. Heading Measurement

Approach and departure headings must be measured from the **actual GPX trajectory**.

Use a distance window rather than fixed points.

Recommended:

```
approach heading  = bearing from point (maneuver − 20 m) → maneuver
departure heading = bearing from maneuver → point (maneuver + 20 m)
```

Advantages:

- resistant to GPX noise
- stable across varying sampling densities
- robust at junctions

---

# 7. Roundabout Handling

Roundabouts must be treated as a single maneuver.

Algorithm:

1. Detect entry into `junction=roundabout`
2. Follow GPX path through roundabout
3. Detect exit point
4. Count exits passed between entry and exit
5. Emit instruction

Example:

```
At the roundabout, take the 2nd exit onto Kalkarer Straße
```

Rules:

- entry instruction optional
- exit instruction mandatory
- do not emit additional turn immediately after exit

---

# 8. Continue Instruction Policy

Continue instructions must be suppressed unless useful.

Emit **continue** only when:

- multiple roads intersect
- the chosen continuation is not geometrically obvious
- road name remains important for orientation

Do not emit continue when:

- road name merely changes
- OSM way segmentation changes
- geometry change < 15° and junction degree = 2

---

# 9. Road Naming Rules

Instruction text must prefer the **target road**.

Correct:

```
Turn left onto Kalkarer Straße
```

Incorrect:

```
Turn left from Kalkarer Straße
```

Priority for naming:

1. `name`
2. `ref`
3. `destination`
4. fallback to generic instruction

---

# 10. Maneuver Types

Supported types:

- depart
- continue
- turn
- fork
- merge
- roundabout
- uturn
- arrive

Modifiers:

- straight
- slight_left
- slight_right
- left
- right
- sharp_left
- sharp_right

---

# 11. Redundancy Elimination

After maneuver generation:

1. Merge consecutive continue steps
2. Remove duplicate instructions on same road
3. Collapse trivial maneuvers caused by OSM segmentation

---

# 12. Debug Output Requirements

For every maneuver candidate log:

- GPX index
- approach heading
- departure heading
- delta angle
- road_from
- road_to
- classification
- suppression reason

Example:

```
candidate=42
delta=38°
from=Meißnerstraße
to=Kalkarer Straße
decision=TURN_LEFT
reason=angle_threshold
```

---

# 13. Acceptance Criteria

The system is considered correct when:

- left/right always matches GPX travel direction
- roundabouts produce correct exit numbers
- repeated continues are minimized
- OSM segmentation no longer creates false maneuvers

---

# 14. Future Extensions

Potential improvements:

- lane guidance
- traffic sign inference
- ML‑based maneuver smoothing
- intersection complexity scoring

---

# Summary

The most important rule:

**Navigation instructions must be derived from GPX maneuver geometry first, and only then enriched using OSM metadata.**

OSM segmentation must never drive instruction generation.
