# Simulation

Simulation is first-class in AERIS. Every scenario is a YAML file under `sim/scenarios/`, the
fake fleet needs no ROS or PX4, and a whole mission runs headless in a few seconds.

## Running

```bash
cd backend
uv run aeris sim list
uv run aeris sim run --scenario forest_search
uv run aeris sim run --scenario low_battery --quiet --output out.json
```

Through the API and dashboard the same scenarios run paced against the wall clock
(`time_scale` simulated seconds per real second) so you can watch them and act as the operator.

## Scenario file

```yaml
name: forest_search
seed: 42
base_position: { latitude: 46.97, longitude: 8.0, altitude_m: 0 }
search_polygon: { vertices: [ {latitude: ..., longitude: ...}, ... ] }
restricted_regions: [ { vertices: [...] } ]        # optional no-fly polygons
last_known_position: { latitude: ..., longitude: ... }   # optional; feeds zone priority
missing_person:                                     # optional simulated person
  position: { latitude: ..., longitude: ... }
  detection_range_m: 70
  thermal_confidence: 0.40
  visual_confidence: 0.20
  sighting_cooldown_s: 30
search_altitude_m: 60
overlap_fraction: 0.2
zone_size_m: 250
tick_s: 1.0
max_duration_s: 3600
drones:
  - drone_id: drone-01
    capability: { camera: true, thermal: true, cruise_speed_mps: 12, nominal_endurance_s: 1800, camera_footprint_width_m: 45 }
    battery_percent: 100
events:
  - { at_s: 120, type: battery_drain_multiplier, drone_id: drone-02, value: 5.0 }
  - { at_s: 240, type: detection, drone_id: drone-03, source: THERMAL, value: 0.42 }
  - { at_s: 480, type: detection, drone_id: drone-03, source: THERMAL, value: 0.86, movement: true }
  - { at_s: 540, type: operator_confirm }
```

### Event types

| type | fields | effect |
|---|---|---|
| `battery_drain_multiplier` | `drone_id`, `value` | battery drains `value`× faster from now on |
| `battery_set` | `drone_id`, `value` | battery jumps to `value` percent |
| `link_set` | `drone_id`, `value` (bool) | radio on/off; off drones stop reporting and reject commands |
| `detection` | `drone_id`, `source`, `value` (confidence), optional `position`, `movement` | a scripted sensor observation; position defaults to the missing person |
| `operator_confirm` / `operator_reject` | `note` | the operator resolves the first open candidate |

Unknown keys are rejected so typos cannot silently change a scenario.

### What is simulated, honestly

- **Flight**: point kinematics at cruise speed along waypoint plans; straight-line return to
  base; landing at base or on battery depletion. No wind, no dynamics.
- **Battery**: linear drain from nominal endurance, with per-drone multipliers from events.
- **Link**: a drone whose link is off produces no telemetry and accepts no commands, so link
  state degrades from telemetry age exactly as it would in the field.
- **Perception**: two sources. Scripted `detection` events give precise timelines. The optional
  `missing_person` block makes drones with the matching sensor emit an observation whenever
  they pass within range, at most once per cooldown. Confidences are fixed numbers, not a
  detector model.

## Bundled scenarios

| scenario | exercises |
|---|---|
| `basic_search` | baseline coverage, no disruptions |
| `low_battery` | mandatory battery return, zone reassignment |
| `lost_connection` | link loss, audited lost-link behaviour, reassignment |
| `drone_failure` | hard failure (power + radio), fleet absorbs the loss |
| `dynamic_reassignment` | battery return plus link loss with a restricted no-fly pocket and a last known position |
| `candidate_detection` | sensor hits clustered, triaged, investigated, escalated, confirmed |
| `multiple_detections` | two candidates; one rejected, one confirmed |
| `forest_search` | the full MVP demo timeline (see the file header) |

## Forest search timeline

| sim time | event | AERIS behaviour |
|---|---|---|
| T+0 | mission starts | 16 zones over 1 km², three drones assigned |
| T+2 min | drone-02 battery degrades 5× | disposition decisions see a shrinking margin |
| T+4 min | drone-03 weak thermal (0.42) | triage RECHECK/INVESTIGATE; a drone takes an investigation pass |
| ~T+6 min | drone-02 hits the return threshold | Safety Governor recalls it; its partial zone is handed off |
| T+8 min | strong thermal + visual agreement | triage POSSIBLE_SURVIVOR; candidate escalated; confirmation requested |
| T+9 min | operator confirms | `PERSON_LOCATED`; everyone returns to base |

The integration test `test_forest_search_demo_timeline` asserts this sequence.
