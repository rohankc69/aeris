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

All thresholds are named fields in `backend/aeris/config.py` (`SafetySettings`).

| Rule | Behavior |
|---|---|
| Mandatory return battery | battery at or below `min_return_battery_percent`, or below estimated return cost plus `return_battery_margin_percent` → `RETURN_TO_BASE`, regardless of proposal |
| Critical battery | at or below `critical_battery_percent` → immediate return or land; overrides everything except an operator emergency stop |
| Maximum altitude | any waypoint above `max_altitude_m` is rejected |
| Geofence | any waypoint outside the mission search area buffer or inside a restricted region is rejected |
| Minimum separation | assignments that would put two drones within `min_separation_m` at the same time are rejected or sequenced |
| Telemetry timeout | see link states below |
| Invalid coordinates | NaN, out-of-range, or non-finite positions reject the proposal and mark the drone unavailable |
| Unavailable drone | proposals for drones that are returning, lost, failed, or grounded are rejected |
| Unreachable waypoint | plans whose estimated energy exceeds available battery minus return cost are rejected |
| Mission paused/aborted | only `HOLD`, `RETURN_TO_BASE`, and land are allowed |
| Operator emergency stop | every drone receives the preconfigured e-stop behavior immediately; nothing else executes until cleared |

## Link states and lost-link behavior

Link state is derived from telemetry age, never assumed:

| Telemetry age | State | Effect |
|---|---|---|
| ≤ `link_degraded_after_s` | `CONNECTED` | normal |
| ≤ `link_stale_after_s` | `DEGRADED` | human-review probability rises; no new assignments |
| ≤ `link_lost_after_s` | `STALE` | drone treated as unavailable; its zone becomes `PARTIAL` for reassignment |
| > `link_lost_after_s` | `LOST` | preconfigured lost-link behavior (MVP default: return to base); `DroneDisconnected` event |

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
