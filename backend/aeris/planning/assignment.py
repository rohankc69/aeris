"""Drone-to-zone assignment.

``AssignmentStrategy`` is the extension point; ``GreedyAssignmentStrategy`` is the MVP
implementation: a weighted greedy that repeatedly picks the best (drone, zone) pair. Zone
priority may come from an AI score but the assignment itself is deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from aeris.domain.geo import GeoPoint
from aeris.domain.models import Drone, DroneState, SearchZone


@dataclass(frozen=True)
class AssignmentCandidate:
    drone: Drone
    state: DroneState


@dataclass(frozen=True)
class ZoneAssignmentProposal:
    drone_id: str
    zone_id: str
    score: float
    reason: str


@dataclass(frozen=True)
class AssignmentWeights:
    """Relative importance of each factor. Distance is normalised by ``distance_scale_m``."""

    distance: float = 1.0
    battery: float = 0.6
    priority: float = 0.8
    remaining_work: float = 0.3
    distance_scale_m: float = 1000.0


class AssignmentStrategy(Protocol):
    def assign(
        self,
        candidates: list[AssignmentCandidate],
        zones: list[SearchZone],
        *,
        base_position: GeoPoint,
        weights: AssignmentWeights | None = None,
    ) -> list[ZoneAssignmentProposal]: ...


@dataclass
class GreedyAssignmentStrategy:
    """Weighted greedy: score every pair, take the best, remove both, repeat."""

    weights: AssignmentWeights = field(default_factory=AssignmentWeights)

    def assign(
        self,
        candidates: list[AssignmentCandidate],
        zones: list[SearchZone],
        *,
        base_position: GeoPoint,
        weights: AssignmentWeights | None = None,
    ) -> list[ZoneAssignmentProposal]:
        w = weights or self.weights
        available = [c for c in candidates if c.state.is_available_for_assignment]
        open_zones = [z for z in zones if z.status.needs_work]
        proposals: list[ZoneAssignmentProposal] = []

        while available and open_zones:
            best: tuple[float, AssignmentCandidate, SearchZone, str] | None = None
            for cand in available:
                for zone in open_zones:
                    score, reason = self._score(cand, zone, w)
                    if best is None or score > best[0]:
                        best = (score, cand, zone, reason)
            if best is None:
                break
            score, cand, zone, reason = best
            proposals.append(
                ZoneAssignmentProposal(
                    drone_id=cand.drone.drone_id, zone_id=zone.zone_id, score=score, reason=reason
                )
            )
            available.remove(cand)
            open_zones.remove(zone)

        return proposals

    @staticmethod
    def _score(
        cand: AssignmentCandidate, zone: SearchZone, w: AssignmentWeights
    ) -> tuple[float, str]:
        distance_m = cand.state.position.distance_to(zone.polygon.centroid)
        distance_term = -w.distance * min(distance_m / w.distance_scale_m, 3.0)
        battery_term = w.battery * (cand.state.battery_percent / 100.0)
        priority_term = w.priority * zone.priority
        work_term = -w.remaining_work * zone.remaining_fraction
        score = distance_term + battery_term + priority_term + work_term
        reason = (
            f"distance={distance_m:.0f}m battery={cand.state.battery_percent:.0f}% "
            f"priority={zone.priority:.2f} remaining={zone.remaining_fraction:.2f}"
        )
        return score, reason
