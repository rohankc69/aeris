"""Simple energy model shared by safety rules and decision inputs."""

from __future__ import annotations

RETURN_SAFETY_FACTOR = 1.2


def estimate_return_battery_percent(
    *, distance_to_base_m: float, cruise_speed_mps: float, nominal_endurance_s: float
) -> float:
    """Battery percent needed to fly straight home, with a headwind/approach safety factor."""
    if cruise_speed_mps <= 0 or nominal_endurance_s <= 0:
        return 100.0
    seconds = distance_to_base_m / cruise_speed_mps * RETURN_SAFETY_FACTOR
    return min(100.0, 100.0 * seconds / nominal_endurance_s)
