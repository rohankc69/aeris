"""Rule-based provider: the deterministic policy opinion, the offline fallback, and the
evaluation baseline. Every rule reads named thresholds from ``SafetySettings``.

Unknown decision types get a conservative generic answer rather than an error, because
this provider must always be able to answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from aeris.config import SafetySettings
from aeris.decisions.base import (
    ChoiceRequest,
    ChoiceResult,
    JsonDict,
    ProbabilityRequest,
    ProbabilityResult,
    ScoreRequest,
    ScoreResult,
)
from aeris.domain.enums import Disposition, TriageDecision


def _num(state: JsonDict, key: str, default: float = 0.0) -> float:
    value = state.get(key, default)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _flag(state: JsonDict, key: str) -> bool:
    return bool(state.get(key, False))


@dataclass(frozen=True)
class RuleBasedDecisionProvider:
    safety: SafetySettings
    model: str = "rules-v1"

    @property
    def name(self) -> str:
        return "rules"

    async def choice(self, request: ChoiceRequest) -> ChoiceResult:
        handler = {
            "drone_disposition": self._disposition,
            "detection_triage": self._triage,
        }.get(request.decision_type)
        selected, reason = (
            handler(request.state) if handler else (request.choices[0], "generic: first choice")
        )
        if selected not in request.choices:
            selected, reason = (
                request.choices[0],
                f"rule answer {selected} not offered; first choice",
            )
        probabilities = {c: (1.0 if c == selected else 0.0) for c in request.choices}
        return ChoiceResult(
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            selected=selected,
            probabilities=probabilities,
            raw={"reason": reason},
        )

    async def score(self, request: ScoreRequest) -> ScoreResult:
        if request.decision_type == "zone_priority":
            value, reason = self._zone_priority(request.state)
        else:
            value, reason = 0.5, "generic: midpoint"
        span = request.max_score - request.min_score
        return ScoreResult(
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            score=request.min_score + value * span,
            raw={"reason": reason},
        )

    async def probability(self, request: ProbabilityRequest) -> ProbabilityResult:
        if request.decision_type == "human_review_gate":
            value, reason = self._human_review(request.state)
        else:
            value, reason = 0.5, "generic: midpoint"
        return ProbabilityResult(
            provider=self.name,
            model=self.model,
            latency_ms=0.0,
            probability=value,
            raw={"reason": reason},
        )

    # ------------------------------------------------------------------ rules

    def _disposition(self, s: JsonDict) -> tuple[str, str]:
        """Ordered rule table: the first predicate that holds decides."""
        battery = _num(s, "battery_percent")
        return_cost = _num(s, "estimated_return_battery_percent")
        completion = _num(s, "zone_completion")
        safety = self.safety
        approaching_return = battery <= safety.min_return_battery_percent + 10
        rules: list[tuple[bool, Disposition, str]] = [
            (
                battery <= safety.critical_battery_percent,
                Disposition.RETURN_TO_BASE,
                "critical battery",
            ),
            (
                battery <= safety.min_return_battery_percent,
                Disposition.RETURN_TO_BASE,
                "at mandatory return threshold",
            ),
            (
                battery <= return_cost + safety.return_battery_margin_percent,
                Disposition.RETURN_TO_BASE,
                "return cost plus margin reached",
            ),
            (
                _num(s, "connection_quality", 1.0) < 0.5,
                Disposition.REQUEST_HUMAN_REVIEW,
                "poor connection quality",
            ),
            (
                _flag(s, "active_detection"),
                Disposition.CONTINUE_SEARCH,
                "active detection; keep eyes on it",
            ),
            (
                approaching_return and completion < 0.5,
                Disposition.HANDOFF_ZONE,
                "battery approaching return with most of zone left",
            ),
        ]
        for holds, disposition, reason in rules:
            if holds:
                return disposition, reason
        return Disposition.CONTINUE_SEARCH, "nominal"

    @staticmethod
    def _triage(s: JsonDict) -> tuple[str, str]:
        thermal = _num(s, "thermal_confidence")
        visual = _num(s, "visual_confidence")
        observations = _num(s, "observation_count", 1)
        agreement = _flag(s, "sensor_agreement")
        movement = _flag(s, "movement_observed")
        best = max(thermal, visual)
        if agreement and best >= 0.7 and (movement or observations >= 2):
            return (
                TriageDecision.POSSIBLE_SURVIVOR,
                "two sensors agree with strong evidence; human confirmation required",
            )
        if thermal > 0 and visual > 0 and abs(thermal - visual) >= 0.4:
            return TriageDecision.HUMAN_REVIEW, "sensors disagree"
        if best >= 0.7:
            return TriageDecision.INVESTIGATE, "single strong sensor"
        if best >= 0.35:
            return TriageDecision.RECHECK, "weak evidence"
        return TriageDecision.IGNORE, "below evidence floor"

    @staticmethod
    def _zone_priority(s: JsonDict) -> tuple[float, str]:
        coverage = min(1.0, _num(s, "coverage"))
        staleness = min(1.0, _num(s, "time_since_last_search_s") / 1800.0)
        near_detection = 1.0 if _flag(s, "nearby_candidate_detection") else 0.0
        distance = _num(s, "distance_from_last_known_position_m", 2000.0)
        proximity = max(0.0, 1.0 - distance / 2000.0)
        score = 0.35 * (1 - coverage) + 0.15 * staleness + 0.3 * near_detection + 0.2 * proximity
        return min(1.0, max(0.0, score)), "weighted: uncovered, stale, near detection, near LKP"

    @staticmethod
    def _human_review(s: JsonDict) -> tuple[float, str]:
        p = 0.05
        reasons = []
        if _num(s, "model_uncertainty") >= 0.5:
            p += 0.3
            reasons.append("model uncertain")
        if _flag(s, "sensors_disagree"):
            p += 0.25
            reasons.append("sensors disagree")
        if _flag(s, "state_incomplete"):
            p += 0.2
            reasons.append("state incomplete")
        if _num(s, "connection_quality", 1.0) < 0.5:
            p += 0.2
            reasons.append("poor connectivity")
        if _flag(s, "decisions_conflict"):
            p += 0.25
            reasons.append("decisions conflict")
        if _flag(s, "significant_behavior_change"):
            p += 0.15
            reasons.append("significant change")
        if _num(s, "candidate_count") >= 2:
            p += 0.2
            reasons.append("multiple candidates")
        return min(1.0, p), ", ".join(reasons) or "no risk factors"
