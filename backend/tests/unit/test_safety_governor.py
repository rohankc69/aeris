from datetime import UTC, datetime

import pytest

from aeris.config import SafetySettings
from aeris.domain import (
    Disposition,
    Drone,
    DroneCapability,
    DroneState,
    DroneStatus,
    GeoPoint,
    GeoPolygon,
    LinkState,
    Mission,
    MissionStatus,
    SearchArea,
    TelemetryFrame,
)
from aeris.planning import LocalFrame
from aeris.safety import SafetyGovernor, estimate_return_battery_percent
from aeris.world.snapshot import DroneView, WorldSnapshot

T0 = datetime(2026, 1, 1, tzinfo=UTC)
BASE = GeoPoint(latitude=47.0, longitude=8.0)
FRAME = LocalFrame(BASE)


def snapshot(status: MissionStatus = MissionStatus.ACTIVE) -> WorldSnapshot:
    poly = GeoPolygon(vertices=(FRAME.to_geo(0, 0), FRAME.to_geo(500, 0), FRAME.to_geo(500, 500)))
    mission = Mission(
        name="t",
        search_area=SearchArea(polygon=poly),
        base_position=BASE,
        created_at=T0,
        status=status,
    )
    return WorldSnapshot(taken_at=T0, mission=mission, drones=(), zones=())


def view(
    battery: float = 80,
    *,
    distance_m: float = 500,
    link: LinkState = LinkState.CONNECTED,
    status: DroneStatus = DroneStatus.SEARCHING,
    no_state: bool = False,
) -> DroneView:
    drone = Drone(
        drone_id="d",
        name="d",
        capability=DroneCapability(cruise_speed_mps=10, nominal_endurance_s=1500),
    )
    if no_state:
        return DroneView(drone=drone, state=None, telemetry_age_s=None)
    frame = TelemetryFrame(
        drone_id="d",
        timestamp=T0,
        position=FRAME.to_geo(distance_m, 0, 50),
        battery_percent=battery,
        estimated_remaining_s=100,
    )
    state = DroneState.from_telemetry(frame).model_copy(
        update={"link_state": link, "status": status}
    )
    return DroneView(drone=drone, state=state, telemetry_age_s=0.0)


@pytest.fixture
def governor() -> SafetyGovernor:
    return SafetyGovernor(SafetySettings())


def test_energy_model_scales_with_distance() -> None:
    near = estimate_return_battery_percent(
        distance_to_base_m=100, cruise_speed_mps=10, nominal_endurance_s=1500
    )
    far = estimate_return_battery_percent(
        distance_to_base_m=1000, cruise_speed_mps=10, nominal_endurance_s=1500
    )
    assert 0 < near < far <= 100
    assert far == pytest.approx(100 * (1000 / 10 * 1.2) / 1500)
    assert (
        estimate_return_battery_percent(
            distance_to_base_m=1e9, cruise_speed_mps=10, nominal_endurance_s=1500
        )
        == 100
    )


def test_nominal_drone_has_no_violation(governor: SafetyGovernor) -> None:
    assert governor.check(view(80), snapshot()) is None


@pytest.mark.parametrize(
    ("battery", "rule"),
    [(10, "critical_battery"), (25, "mandatory_return_battery"), (15, "critical_battery")],
)
def test_battery_rules_require_return(governor: SafetyGovernor, battery: float, rule: str) -> None:
    violation = governor.check(view(battery), snapshot())
    assert violation is not None
    assert violation.rule == rule
    assert violation.required_action is Disposition.RETURN_TO_BASE


def test_return_margin_depends_on_distance(governor: SafetyGovernor) -> None:
    # 5 km out at 10 m/s with 1500 s endurance costs 40% (+20% factor); 30% is not enough.
    far = governor.check(view(30, distance_m=5000), snapshot())
    assert far is not None and far.rule == "return_margin"
    assert governor.check(view(30, distance_m=200), snapshot()) is None


def test_lost_link_requires_return_and_degraded_does_not(governor: SafetyGovernor) -> None:
    lost = governor.check(view(80, link=LinkState.LOST), snapshot())
    assert lost is not None and lost.rule == "link_lost"
    assert governor.check(view(80, link=LinkState.DEGRADED), snapshot()) is None


def test_paused_mission_requires_hold(governor: SafetyGovernor) -> None:
    v = governor.check(view(80), snapshot(MissionStatus.PAUSED))
    assert v is not None and v.required_action is Disposition.HOLD


def test_missing_telemetry_is_never_normal(governor: SafetyGovernor) -> None:
    v = governor.check(view(no_state=True), snapshot())
    assert v is not None and v.rule == "telemetry_missing"


def test_landed_and_unavailable_drones_are_ignored(governor: SafetyGovernor) -> None:
    assert governor.check(view(5, status=DroneStatus.LANDED), snapshot()) is None
    assert governor.check(view(5, status=DroneStatus.UNAVAILABLE), snapshot()) is None


def test_validate_allows_safe_proposals_without_event(governor: SafetyGovernor) -> None:
    verdict = governor.validate_disposition(
        Disposition.CONTINUE_SEARCH, view(80), snapshot(), source="openrouter"
    )
    assert (
        verdict.allowed and not verdict.overridden and verdict.action is Disposition.CONTINUE_SEARCH
    )


def test_validate_overrides_risky_ai_proposal_with_audit_event(governor: SafetyGovernor) -> None:
    snap = snapshot()
    verdict = governor.validate_disposition(
        Disposition.CONTINUE_SEARCH, view(20), snap, source="openrouter"
    )
    assert not verdict.allowed
    assert verdict.overridden
    assert verdict.action is Disposition.RETURN_TO_BASE
    assert verdict.event is not None
    assert verdict.event.rule == "mandatory_return_battery"
    assert verdict.event.proposed_action == "openrouter:CONTINUE_SEARCH"
    assert verdict.event.safe_alternative == "RETURN_TO_BASE"
    assert verdict.event.snapshot_hash == snap.snapshot_hash


def test_validate_accepts_proposal_that_already_satisfies_rule(governor: SafetyGovernor) -> None:
    verdict = governor.validate_disposition(
        Disposition.RETURN_TO_BASE, view(20), snapshot(), source="rules"
    )
    assert verdict.allowed and not verdict.overridden
    held = governor.validate_disposition(
        Disposition.RETURN_TO_BASE, view(80), snapshot(MissionStatus.PAUSED), source="mock"
    )
    assert held.allowed  # more conservative than HOLD is fine


def test_ai_may_return_early_but_never_relax_a_limit(governor: SafetyGovernor) -> None:
    early = governor.validate_disposition(
        Disposition.RETURN_TO_BASE, view(90), snapshot(), source="openrouter"
    )
    assert early.allowed and early.action is Disposition.RETURN_TO_BASE
    for risky in (
        Disposition.CONTINUE_SEARCH,
        Disposition.HANDOFF_ZONE,
        Disposition.HOLD,
        Disposition.REQUEST_HUMAN_REVIEW,
    ):
        verdict = governor.validate_disposition(risky, view(12), snapshot(), source="openrouter")
        assert verdict.action is Disposition.RETURN_TO_BASE
        assert verdict.overridden
