"""Human-readable one-line renderings of mission events for the CLI.

Works on plain dicts so the same formatter serves the in-process bus (``aeris sim run
--events``) and the WebSocket stream (``aeris watch``).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

Data = Mapping[str, object]
Formatter = Callable[[Data], str]

HIDDEN = {"TelemetryReceived", "ZoneCoverageUpdated", "DecisionRequested"}


def clock(elapsed_s: float) -> str:
    m, s = divmod(int(elapsed_s), 60)
    return f"T+{m:02d}:{s:02d}"


def _num(d: Data, key: str) -> float:
    try:
        return float(d.get(key, 0) or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _short(d: Data, key: str) -> str:
    return str(d.get(key, ""))[:8]


def _assign(d: Data) -> str:
    resume = _num(d, "start_fraction")
    tail = f" (resume at {resume:.0%})" if resume > 0 else ""
    return f"ASSIGN    {d.get('drone_id')} -> zone {d.get('zone_id')}{tail}"


def _handoff(d: Data) -> str:
    rem = _num(d, "remaining_fraction")
    return (
        f"HANDOFF   zone {d.get('zone_id')} released by {d.get('previous_drone_id')} "
        f"({rem:.0%} left): {d.get('reason')}"
    )


def _link(d: Data) -> str:
    age = _num(d, "telemetry_age_s")
    return (
        f"LINK      {d.get('drone_id')} {d.get('previous')} -> {d.get('current')} ({age:.0f}s old)"
    )


def _decision(d: Data) -> str:
    override = " | SAFETY OVERRIDE" if d.get("safety_override") else ""
    same = d.get("selected_value") == d.get("final_action")
    arrow = "" if same else f" -> {d.get('final_action')}"
    who = d.get("drone_id") or "mission"
    return (
        f"DECISION  {who} {d.get('decision_type')} [{d.get('provider')}] "
        f"{d.get('selected_value')}{arrow}{override}"
    )


def _safety(d: Data) -> str:
    return (
        f"SAFETY    {d.get('drone_id')} rule={d.get('rule')} "
        f"proposed={d.get('proposed_action')} -> {d.get('safe_alternative')}"
    )


def _detect_new(d: Data) -> str:
    return (
        f"DETECT    {d.get('drone_id')} new detection {_short(d, 'detection_id')} "
        f"conf={_num(d, 'confidence'):.2f} zone={d.get('zone_id')}"
    )


def _detect_upd(d: Data) -> str:
    return (
        f"DETECT    {_short(d, 'detection_id')} now {d.get('observation_count')} obs, "
        f"max conf {_num(d, 'max_confidence'):.2f}"
    )


def _operator(d: Data) -> str:
    target = f" {d.get('target_id')}" if d.get("target_id") else ""
    return f"OPERATOR  {d.get('action_type')}{target}"


FORMATTERS: dict[str, Formatter] = {
    "MissionStarted": lambda d: "MISSION   started",
    "MissionPaused": lambda d: "MISSION   paused",
    "MissionResumed": lambda d: "MISSION   resumed",
    "MissionAborted": lambda d: f"MISSION   aborted {d.get('reason', '')}".rstrip(),
    "MissionCompleted": lambda d: f"MISSION   {d.get('final_status')}",
    "DroneRegistered": lambda d: f"FLEET     {d.get('drone_id')} registered",
    "ZoneAssigned": _assign,
    "ZoneStatusChanged": lambda d: (
        f"ZONE      {d.get('zone_id')} {d.get('previous')} -> {d.get('current')}"
    ),
    "ZoneReassignmentRequested": _handoff,
    "DroneReturning": lambda d: f"RTB       {d.get('drone_id')}: {d.get('reason')}",
    "DroneLinkStateChanged": _link,
    "DroneDisconnected": lambda d: f"LINK      {d.get('drone_id')} LOST",
    "DecisionCompleted": _decision,
    "SafetyOverrideTriggered": _safety,
    "HumanReviewRequested": lambda d: (
        f"REVIEW    {d.get('subject_type')} {d.get('subject_id')}: {d.get('reason')}"
    ),
    "DetectionCreated": _detect_new,
    "DetectionUpdated": _detect_upd,
    "CandidateEscalated": lambda d: (
        f"CANDIDATE {_short(d, 'candidate_id')} possible survivor: HUMAN CONFIRMATION REQUIRED"
    ),
    "SurvivorConfirmed": lambda d: f"OPERATOR  confirmed survivor {_short(d, 'candidate_id')}",
    "SurvivorRejected": lambda d: f"OPERATOR  rejected candidate {_short(d, 'candidate_id')}",
    "OperatorActionReceived": _operator,
}


def describe(event_type: str, d: Data) -> str | None:
    """Return a line for the event, or None if it is not worth showing."""
    if event_type in HIDDEN:
        return None
    formatter = FORMATTERS.get(event_type)
    if formatter is None:
        return f"{event_type:9s} {dict(d)}"
    return formatter(d)
