from aeris.eventfeed import clock, describe


def test_clock_formats_minutes_and_seconds() -> None:
    assert clock(0) == "T+00:00"
    assert clock(542.9) == "T+09:02"


def test_describe_hides_noise_and_renders_decisions() -> None:
    assert describe("TelemetryReceived", {}) is None
    line = describe(
        "DecisionCompleted",
        {
            "drone_id": "drone-02",
            "decision_type": "drone_disposition",
            "provider": "openrouter",
            "selected_value": "CONTINUE_SEARCH",
            "final_action": "RETURN_TO_BASE",
            "safety_override": True,
        },
    )
    expected = (
        "DECISION  drone-02 drone_disposition [openrouter] "
        "CONTINUE_SEARCH -> RETURN_TO_BASE | SAFETY OVERRIDE"
    )
    assert line == expected
    assert describe(
        "CandidateEscalated", {"candidate_id": "abcdef0123"}
    ) is not None and "HUMAN CONFIRMATION" in str(
        describe("CandidateEscalated", {"candidate_id": "abcdef0123"})
    )
    assert (
        describe("ZoneAssigned", {"drone_id": "drone-01", "zone_id": "B2", "start_fraction": 0.58})
        == "ASSIGN    drone-01 -> zone B2 (resume at 58%)"
    )
    assert describe("SomethingNew", {"x": 1}) is not None
