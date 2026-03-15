# NavFromTrack – Maneuver Detection Algorithm

## Purpose

This document specifies the maneuver-detection algorithm for NavFromTrack. It complements the route guidance requirements document and focuses on **how to detect, classify, suppress, and format maneuvers** from a GPX-derived traveled path enriched with local OSM metadata.

The key principle remains:

**Maneuvers are derived from the traveled GPX geometry first, then enriched with OSM metadata.**

---

# 1. Inputs

The algorithm assumes the following inputs are available:

## 1.1 GPX track points

An ordered list of traveled points:

```text
P[0], P[1], ..., P[n-1]
```

Each point should have at least:

- latitude
- longitude
- cumulative distance from start

Optional but useful:

- timestamp
- speed
- heading

## 1.2 Matched OSM path metadata

For each GPX point or point interval, the matched road metadata may include:

- `way_id`
- `name`
- `ref`
- `highway`
- `junction`
- `oneway`
- access restrictions
- node / junction identifiers where available

## 1.3 Derived internal runs

The existing segmentation logic may still produce internal runs such as:

- same `way_id`
- same road name
- same roundabout membership
- same road class

These runs are **metadata segments**, not maneuvers.

---

# 2. High-Level Pipeline

```text
GPX points
  ↓
Matched path with metadata
  ↓
Internal segmentation into runs
  ↓
Candidate maneuver boundaries
  ↓
Heading measurement around boundary
  ↓
Topology / road-identity evaluation
  ↓
Suppression or emission
  ↓
Instruction synthesis
  ↓
Post-processing / de-duplication
```

---

# 3. Core Data Model

A useful internal model is:

```text
Run
- start_index
- end_index
- way_id
- name
- ref
- highway
- is_roundabout
- junction_type

ManeuverCandidate
- boundary_index
- prev_run
- next_run
- approach_heading
- departure_heading
- signed_delta
- abs_delta
- road_from
- road_to
- is_roundabout_entry
- is_roundabout_exit
- junction_degree
- has_alternative_paths
- decision_reason

Maneuver
- type
- modifier
- location
- road_from
- road_to
- instruction
- exit_number
- distance
```

---

# 4. Candidate Boundary Generation

## Rule

Every internal run boundary may become a **candidate**, but **not every candidate becomes a maneuver**.

Generate a candidate at each transition:

```text
run[i-1] -> run[i]
```

Store:

- boundary GPX index
- previous run
- next run
- road names and classes
- roundabout flags

Do **not** emit instructions yet.

---

# 5. Heading Measurement

## Goal

Measure the actual traveled direction **before and after** the candidate boundary.

## Rule

Use **distance windows**, not fixed point counts.

Recommended defaults:

- approach window: 15–25 m before boundary
- departure window: 15–25 m after boundary

## Pseudocode

```text
function heading_before(points, boundary_index, window_m):
    a = point_at_distance_before(boundary_index, window_m)
    b = point_at_boundary(boundary_index)
    return bearing(a, b)

function heading_after(points, boundary_index, window_m):
    a = point_at_boundary(boundary_index)
    b = point_at_distance_after(boundary_index, window_m)
    return bearing(a, b)
```

## Signed angle

```text
signed_delta = normalize_to_minus180_plus180(
    departure_heading - approach_heading
)
abs_delta = abs(signed_delta)
```

Interpretation:

- negative = left
- positive = right

This convention should be fixed and documented in code.

## Important

Never derive left/right from:

- OSM way ordering
- way IDs
- road names
- node ordering in OSM

Only the traveled GPX direction may determine the sign.

---

# 6. Determining the True Maneuver Point

Run boundaries are not always good maneuver locations.

A better maneuver point is the nearest GPX index where one or more of these apply:

- the path intersects a junction area
- road identity changes meaningfully
- heading transition becomes stable
- roundabout entry / exit begins

## Practical instruction

When a run boundary is detected, search in a small local neighborhood, for example:

- `boundary_index - 5` to `boundary_index + 5`

Then choose the index that best represents the actual traveled transition.

Selection criteria:

1. closest index to road transition
2. strongest stable heading change
3. nearest junction node if available

This avoids measuring bearings on the wrong tiny road fragment.

---

# 7. Maneuver Classification Thresholds

Recommended defaults:

| Classification | Absolute angle |
|---|---:|
| continue | < 15° |
| slight left/right | 15° to < 45° |
| left/right | 45° to < 120° |
| sharp left/right | 120° to < 170° |
| u-turn | ≥ 170° |

These values should be configurable.

## Pseudocode

```text
function classify_by_angle(signed_delta):
    a = abs(signed_delta)

    if a < 15:
        return ("continue", "straight")

    if a < 45:
        return ("turn", "slight_left" if signed_delta < 0 else "slight_right")

    if a < 120:
        return ("turn", "left" if signed_delta < 0 else "right")

    if a < 170:
        return ("turn", "sharp_left" if signed_delta < 0 else "sharp_right")

    return ("uturn", "uturn")
```

Angle classification alone is not enough; suppression logic must also be applied.

---

# 8. Road-Identity Evaluation

A run boundary is more likely to be a meaningful maneuver when the user enters a new logical road.

Useful signals:

- `name` changed
- `ref` changed
- `highway` changed materially
- entering `_link`
- entering service road
- entering / exiting roundabout

## Important

A change in `way_id` alone must not create a maneuver.

## Suggested helper

```text
function meaningful_road_change(prev_run, next_run):
    if prev_run.is_roundabout != next_run.is_roundabout:
        return True

    if normalize(prev_run.name) != normalize(next_run.name):
        return True

    if normalize(prev_run.ref) != normalize(next_run.ref):
        return True

    if materially_different_highway(prev_run.highway, next_run.highway):
        return True

    return False
```

Material road-class changes include examples like:

- residential -> primary_link
- primary -> service
- service -> trunk_link

They do not include trivial segmentation of the same road type.

---

# 9. Topology and Decision Awareness

A real maneuver usually corresponds to a **decision point**.

Useful indicators:

- junction degree > 2
- multiple onward possibilities
- roundabout entry/exit
- fork / merge geometry
- entering or leaving a one-way branch

## Guidance rule

A `continue` instruction should usually be emitted only when the junction is ambiguous.

Examples where continue may be useful:

- staying on the main road at a fork
- continuing through a skewed multi-road junction
- remaining on a numbered road where several options exist

Examples where continue is not useful:

- simple degree-2 continuation
- same corridor with trivial geometry bend
- road name changes without real choice

---

# 10. Suppression Decision Tree

The key implementation rule is:

**A candidate becomes a maneuver only if it adds navigational value.**

## Decision tree

```text
if candidate is roundabout entry/exit:
    emit roundabout maneuver
else if abs_delta >= uturn_threshold:
    emit uturn
else if abs_delta < continue_threshold:
    if ambiguous junction and staying_on_route_needs_clarification:
        emit continue
    else:
        suppress
else:
    if meaningful_road_change or junction_has_choice or fork_or_merge:
        emit turn/fork/merge
    else:
        suppress
```

## Strong suppression cases

Suppress if all are true:

- `abs_delta < 30°`
- no meaningful road change
- degree-2 topology or no alternative continuation
- not roundabout
- not fork
- not merge

---

# 11. Repeated Continue Suppression

Repeated continue instructions are a separate policy problem.

## Rules

Suppress or merge consecutive continue instructions when:

- both are `continue`
- same logical road
- no meaningful junction in between
- same target road name or same ref

## Pseudocode

```text
function merge_redundant_continues(maneuvers):
    result = []
    for m in maneuvers:
        if result is empty:
            append m
            continue

        prev = result[-1]

        if prev.type == "continue" and m.type == "continue":
            if same_logical_road(prev, m):
                continue

        result.append(m)

    return result
```

Also suppress isolated continue instructions that merely repeat what the previous instruction already made obvious.

---

# 12. Roundabout Algorithm

Roundabouts must be handled as **one composite maneuver**.

## 12.1 Detection

A roundabout sequence begins when:

- current run is not roundabout
- next run is roundabout

A roundabout sequence ends when:

- current run is roundabout
- next run is not roundabout

## 12.2 Entry and exit

Track:

- entry run
- roundabout runs
- exit run

Determine:

- entry index
- exit index
- exit road name/ref

## 12.3 Exit counting

Count eligible exits passed from entry to chosen exit.

Count only branches that are:

- outward from roundabout
- drivable
- not the entry branch
- not clearly restricted / service noise if filtered out

## Pseudocode

```text
function count_roundabout_exit(roundabout_path, entry_arm, chosen_exit_arm):
    exits = 0
    for arm in roundabout_arms_in_travel_order(roundabout_path):
        if arm == entry_arm:
            continue
        if not arm.is_drivable:
            continue
        exits += 1
        if arm == chosen_exit_arm:
            return exits
    return None
```

Travel order must match the GPX traversal order through the roundabout.

## 12.4 Output

Preferred instruction:

```text
At the roundabout, take the 2nd exit onto Kalkarer Straße
```

Fallback only if exit count cannot be determined:

```text
Exit the roundabout onto Kalkarer Straße
```

Avoid:

```text
Enter the roundabout and continue
```

unless there is genuinely no reliable exit information.

## 12.5 Suppression rule

Do not emit an additional immediate left/right turn that merely duplicates the roundabout exit maneuver.

---

# 13. Target Road Selection

The target road in an instruction must be the road being entered.

## Priority

1. `name`
2. `ref`
3. `destination`
4. no road text

## Examples

Correct:

```text
Turn left onto Kalkarer Straße
Turn right onto B57
At the roundabout, take the 3rd exit onto Xantener Straße
```

Incorrect:

```text
Turn left from Meißnerstraße
Turn right from the previous road
```

---

# 14. Fork and Merge Detection

Angle alone may misclassify forks and merges.

## Fork signals

- multiple outbound choices
- shallow angle
- geometry splits into comparable alternatives

Typical outputs:

- `fork slight left`
- `fork slight right`

## Merge signals

- the traveled path joins another larger road
- lane / ramp style behavior
- entering from `_link`

Typical outputs:

- `merge left`
- `merge right`

These can often be inferred from:

- road class transitions
- `_link` roads
- junction topology
- angle range roughly 10°–45°

---

# 15. Pseudo-Code: End-to-End Maneuver Detection

```text
function detect_maneuvers(points, runs):
    candidates = []

    for each boundary between runs:
        candidate = build_candidate(boundary)
        candidate.boundary_index = refine_boundary_index(points, candidate)
        candidate.approach_heading = heading_before(points, candidate.boundary_index, 20)
        candidate.departure_heading = heading_after(points, candidate.boundary_index, 20)
        candidate.signed_delta = normalize(
            candidate.departure_heading - candidate.approach_heading
        )
        candidate.abs_delta = abs(candidate.signed_delta)
        candidate.has_meaningful_road_change = meaningful_road_change(
            candidate.prev_run, candidate.next_run
        )
        candidate.topology = inspect_topology(candidate)
        candidates.append(candidate)

    maneuvers = []

    i = 0
    while i < len(candidates):
        c = candidates[i]

        if begins_roundabout_sequence(c):
            m, next_index = build_roundabout_maneuver(candidates, i, points)
            maneuvers.append(m)
            i = next_index
            continue

        decision = decide_emit_or_suppress(c)

        if decision.emit:
            m = build_maneuver_from_candidate(c, decision)
            maneuvers.append(m)

        i += 1

    maneuvers = merge_redundant_continues(maneuvers)
    maneuvers = remove_duplicate_same_road_steps(maneuvers)
    maneuvers = add_depart_and_arrive(points, maneuvers)

    return maneuvers
```

---

# 16. Decision Function

```text
function decide_emit_or_suppress(c):
    if c.is_roundabout_related:
        return EMIT_ROUNDABOUT

    if c.abs_delta >= 170:
        return EMIT_UTURN

    if c.abs_delta < 15:
        if c.topology.ambiguous and c.topology.needs_continue_guidance:
            return EMIT_CONTINUE
        return SUPPRESS("trivial_straight")

    if c.abs_delta < 45:
        if c.topology.is_fork:
            return EMIT_FORK
        if c.has_meaningful_road_change or c.topology.has_choice:
            return EMIT_SLIGHT_TURN
        return SUPPRESS("minor_bend")

    if c.abs_delta < 120:
        if c.has_meaningful_road_change or c.topology.has_choice:
            return EMIT_TURN
        return SUPPRESS("non_decision_turn")

    if c.abs_delta < 170:
        return EMIT_SHARP_TURN

    return EMIT_UTURN
```

---

# 17. Debug and Validation Requirements

For every candidate, log:

- candidate id
- boundary index
- refined maneuver index
- approach heading
- departure heading
- signed delta
- abs delta
- road_from
- road_to
- roundabout flags
- topology summary
- emission decision
- suppression reason

Example:

```text
candidate=16
boundary_index=842
maneuver_index=844
approach=92.4
departure=21.0
delta=-71.4
from=Meißnerstraße
to=Kalkarer Straße
junction_degree=4
decision=EMIT_TURN_LEFT
reason=meaningful_road_change + junction_choice
```

This logging is essential for verifying that left/right matches the GPX path.

---

# 18. Test Cases

The algorithm should be validated with at least these cases:

## 18.1 Simple true turn

- clear 90° left turn
- correct target road name
- no reversed left/right

## 18.2 Slight bend, same road

- same logical road
- moderate geometry bend
- suppressed maneuver

## 18.3 Name change without real decision

- same corridor
- road name changes
- no turn emitted

## 18.4 Ambiguous straight-through junction

- staying on route requires clarification
- emit continue

## 18.5 Roundabout exit

- correct exit count
- no extra redundant post-exit turn

## 18.6 Fork

- shallow split
- classify as fork, not plain turn

## 18.7 Merge from ramp

- entering main road from link
- classify as merge

## 18.8 U-turn

- near-180° reversal
- classify as u-turn

---

# 19. Implementation Guidance for `gpx_osm_to_directions.py`

## Recommended refactor steps

1. Keep existing run building, but rename mentally to **internal segmentation**.
2. Introduce a `ManeuverCandidate` layer.
3. Replace fixed 3-point heading logic with distance-window heading logic.
4. Add boundary refinement near the local transition area.
5. Implement roundabout sequence handling as one maneuver.
6. Add a suppression decision function.
7. Add repeated-continue suppression as a dedicated post-processing step.
8. Add structured debug logging for every candidate.

## Most important coding rule

**Do not emit a maneuver solely because `way_id` changed.**

---

# 20. Summary

The maneuver-detection algorithm should follow this order of priority:

1. Determine how the GPX path was actually traveled
2. Measure stable approach and departure headings
3. Detect whether the transition is a real navigation decision
4. Enrich with OSM road names and topology
5. Suppress redundant or misleading instructions

The most important outcome is correctness:

- left must really be left
- right must really be right
- roundabouts must really identify the chosen exit
- repeated continue instructions must disappear unless they add value
