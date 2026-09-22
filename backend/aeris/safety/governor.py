"""Safety Governor.

Every disposition from any source (Jev, rules, planner, operator) is validated here. The
governor composes small deterministic rules (``aeris.safety.rules``) that read named
thresholds from ``SafetySettings``. A violated rule replaces the proposal with the rule's
required action and produces a ``SafetyEvent``.

State rules (``aeris.safety.rules``) govern what a drone should do now: battery, link,
altitude, separation, mission state. Plan rules (``aeris.safety.plan_rules``) validate a
waypoint plan before it is sent: coordinates, availability, altitude, geofence, restricted
regions, energy reachability.
"""

from __future__ import annotations

from dataclasses import dataclass

from aeris.config import SafetySettings
from aeris.domain.enums import Disposition, DroneStatus
from aeris.domain.models import SafetyEvent, WaypointPlan
from aeris.safety.plan_rules import PlanContext, PlanRule, PlanViolation, default_plan_rules
from aeris.safety.rules import RuleContext, SafetyRule, Violation, default_rules
from aeris.world.snapshot import DroneView, WorldSnapshot

# Proposals that only make a drone more conservative than a HOLD requirement are fine.
_CONSERVATIVE = {Disposition.RETURN_TO_BASE, Disposition.HOLD}
_INERT = {DroneStatus.LANDED, DroneStatus.UNAVAILABLE}


@dataclass(frozen=True)
class SafetyVerdict:
    allowed: bool
    action: Disposition
    violation: Violation | None = None
    event: SafetyEvent | None = None

    @property
    def overridden(self) -> bool:
        return self.event is not None


@dataclass(frozen=True)
class PlanVerdict:
    allowed: bool
    violation: PlanViolation | None = None
    event: SafetyEvent | None = None


class SafetyGovernor:
    def __init__(
        self,
        settings: SafetySettings,
        rules: list[SafetyRule] | None = None,
        plan_rules: list[PlanRule] | None = None,
    ) -> None:
        self._settings = settings
        self._rules = rules if rules is not None else default_rules()
        self._plan_rules = plan_rules if plan_rules is not None else default_plan_rules()

    def check(self, view: DroneView, snapshot: WorldSnapshot) -> Violation | None:
        """First violated rule for this drone's current state, independent of any proposal."""
        state = view.state
        if state is None:
            return Violation("telemetry_missing", "no telemetry ever received", Disposition.HOLD)
        if state.status in _INERT:
            return None
        ctx = RuleContext(view=view, state=state, snapshot=snapshot, settings=self._settings)
        for rule in self._rules:
            violation = rule.evaluate(ctx)
            if violation is not None:
                return violation
        return None

    def validate_disposition(
        self, proposal: Disposition, view: DroneView, snapshot: WorldSnapshot, *, source: str
    ) -> SafetyVerdict:
        violation = self.check(view, snapshot)
        if violation is None:
            return SafetyVerdict(allowed=True, action=proposal)
        satisfies = proposal is violation.required_action or (
            proposal in _CONSERVATIVE and violation.required_action is Disposition.HOLD
        )
        if satisfies:
            return SafetyVerdict(allowed=True, action=proposal, violation=violation)
        event = SafetyEvent(
            timestamp=snapshot.taken_at,
            mission_id=snapshot.mission.mission_id,
            drone_id=view.drone.drone_id,
            rule=violation.rule,
            proposed_action=f"{source}:{proposal}",
            safe_alternative=violation.required_action,
            reason=violation.reason,
            snapshot_hash=snapshot.snapshot_hash,
        )
        return SafetyVerdict(
            allowed=False, action=violation.required_action, violation=violation, event=event
        )

    def validate_plan(
        self, plan: WaypointPlan, view: DroneView, snapshot: WorldSnapshot, *, source: str
    ) -> PlanVerdict:
        """Block a plan that violates any plan rule. Blocked plans are never sent."""
        state = view.state
        violation: PlanViolation | None
        if state is None:
            violation = PlanViolation("telemetry_missing", "no telemetry ever received")
        else:
            ctx = PlanContext.build(plan, view, state, snapshot, self._settings)
            violation = next(
                (v for v in (rule.evaluate(ctx) for rule in self._plan_rules) if v is not None),
                None,
            )
        if violation is None:
            return PlanVerdict(allowed=True)
        event = SafetyEvent(
            timestamp=snapshot.taken_at,
            mission_id=snapshot.mission.mission_id,
            drone_id=view.drone.drone_id,
            rule=violation.rule,
            proposed_action=f"{source}:plan:{plan.zone_id or plan.task}",
            safe_alternative="plan_rejected",
            reason=violation.reason,
            snapshot_hash=snapshot.snapshot_hash,
        )
        return PlanVerdict(allowed=False, violation=violation, event=event)

    def return_battery_percent(self, view: DroneView, snapshot: WorldSnapshot) -> float:
        if view.state is None:
            return 100.0
        ctx = RuleContext(view=view, state=view.state, snapshot=snapshot, settings=self._settings)
        return ctx.return_battery_percent
