# Safety

This document describes how AERIS keeps a fleet safe when the AI is wrong, the link drops,
or the operator wants to stop. It is the specification the Safety Governor (Phase 3) is built
against, and the tests in `backend/tests/` for `safety/` trace back to it.

## Scope statement

AERIS is a civilian search-and-rescue research project. It contains no weapons, weapon
targeting, fire-control, harmful payload release, autonomous attack behavior, combat engagement
logic, person-targeting for offensive purposes, military target classification, or autonomous
decisions to harm people. Person detection exists solely to locate people who may need rescue.
Contributions adding any such functionality are rejected. This restriction is repeated in
`README.md`, `CONTRIBUTING.md`, `CLAUDE.md`, and `docs/architecture.md` on purpose.

## Authority model

```
proposal  = decision_engine.propose(snapshot)      # Jev, rules, planner, or operator
validated = safety_governor.validate(proposal, snapshot)
execute(validated.proposal if validated.allowed else validated.safe_alternative)
```

- The Safety Governor is the last component before a fleet adapter and it is deterministic.
- Every proposal from every source passes through it. There is no bypass path, including for
  operator commands (an operator cannot, for example, send a drone outside the geofence).
- Every block or replacement produces a `SafetyEvent` with the rule, the original proposal,
  the substituted action, and the snapshot hash.
- Jev can only make a drone *more* conservative (an earlier return, a hold, a review request).
  It can never relax a hard limit.

## Rules

All thresholds are named fields in `backend/aeris/config.py` (`SafetySettings`). Rules are
small classes in `backend/aeris/safety/rules.py` (state rules) and
`backend/aeris/safety/plan_rules.py` (plan rules), composed by `SafetyGovernor`.

### State rules: what must a drone do now?

Evaluated every tick for every drone, and against every AI or policy proposal. The first
violated rule wins.

| Rule | Fires when | Required action |
|---|---|---|
| `critical_battery` | battery ≤ `critical_battery_percent` | RETURN_TO_BASE |
| `mandatory_return_battery` | battery ≤ `min_return_battery_percent` | RETURN_TO_BASE |
| `return_margin` | battery ≤ estimated return cost + `return_battery_margin_percent` | RETURN_TO_BASE |
| `link_lost` | link state LOST | RETURN_TO_BASE (preconfigured lost-link behaviour) |
| `max_altitude` | reported altitude > `max_altitude_m` | RETURN_TO_BASE |
| `mission_not_active` | mission paused/aborted while a drone is searching | HOLD |
| `minimum_separation` | two airborne searching drones closer than `min_separation_m`, outside `separation_exempt_radius_m` of base | HOLD (the later drone id) |
| `telemetry_missing` | no telemetry ever received | HOLD |

A safety HOLD is released automatically once the rule has stayed clear for a few consecutive
ticks (hysteresis), so a separation hold does not flap at the threshold. Landed and unavailable
drones are not evaluated.

### Plan rules: may this waypoint plan be sent?

Evaluated before any plan reaches a fleet adapter. A rejected plan is never sent; the zone
stays open and the (drone, zone) pair is not retried.

| Rule | Rejects when |
|---|---|
| `invalid_coordinates` | any waypoint is non-finite |
| `unavailable_drone` | the drone is not available for assignment (status or link) |
| `max_altitude` | any waypoint above `max_altitude_m` |
| `geofence` | any waypoint outside the search polygon buffered by `geofence_buffer_m` |
| `restricted_region` | any waypoint inside, or the flight path (from the drone's current position) crossing, a restricted region |
| `unreachable_plan` | transit + path + return energy × `plan_energy_safety_factor` exceeds battery above the return floor |

Planning keeps clear of restricted regions before the governor ever sees a plan: the grid
partitioner subtracts each region buffered by `restricted_clearance_m` and splits affected
cells along the region's edges so every zone stays convex, and transit legs are routed around
regions with a bounded corner detour (`aeris/planning/routing.py`). Return-to-base flies a
direct line, as an autopilot RTL would; it is not obstacle-routed in the MVP.

### Operator emergency stop

`POST /missions/{id}/estop` holds every drone immediately, pauses the mission, and freezes the
control loop: nothing is assigned, decided, or sent until `POST /missions/{id}/estop/clear`.
Clearing does not resume the mission; the operator resumes explicitly. Both actions are
recorded as `OperatorAction`s.

## Link states and lost-link behavior

Link state is derived from telemetry age, never assumed:

| Telemetry age | State | Effect |
|---|---|---|
| ≤ `link_degraded_after_s` | `CONNECTED` | normal |
| ≤ `link_stale_after_s` | `DEGRADED` | keeps its current zone; receives no new assignments or AI decisions |
| ≤ `link_lost_after_s` | `STALE` | its zone is released (`PARTIAL`) for reassignment |
| > `link_lost_after_s` | `LOST` | `link_lost` rule: return-to-base is commanded and audited; if the command cannot be delivered the drone is marked UNAVAILABLE and its own autopilot failsafe governs it |

Lost-link behavior is deterministic and configured, never decided by Jev. In the real-vehicle
case PX4's own failsafe governs the aircraft; AERIS's job is to stop relying on that drone and
redistribute its work.

## Human authority

- Any candidate survivor requires an `OperatorAction` to confirm. AI output can escalate,
  never conclude.
- Operators can pause, resume, abort, hold, or return any drone or the whole mission at any
  time, subject only to the safety rules above.
- The human-review gate raises the probability of asking for help when model uncertainty is
  high, sensors disagree, state is incomplete, connectivity is poor, decisions conflict, or
  several candidates exist. Review requests never block safety behavior.

## Simulation-first

`AERIS_FLEET_PROVIDER=fake` is the default. The PX4 adapter drives PX4 SITL in simulation. A
real-aircraft adapter is not part of the MVP; if one is ever added it must require an explicit
configuration switch, its own safety review, and cannot be selected by default.

## Testing obligations

Any change to safety rules, battery or return logic, link handling, geofence, or reassignment
must ship with tests. See the unit and integration test lists in `CONTRIBUTING.md`.
