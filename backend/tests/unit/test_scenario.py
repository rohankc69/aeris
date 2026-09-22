import pytest
from pydantic import ValidationError

from aeris.simulation import Scenario, list_scenarios, load_scenario


def test_bundled_scenarios_load_and_validate() -> None:
    names = list_scenarios()
    assert "basic_search" in names
    for name in names:
        scenario = load_scenario(name)
        assert scenario.name == name
        assert scenario.to_mission().search_area.polygon.vertices


def test_event_targeting_unknown_drone_is_rejected() -> None:
    base = load_scenario("basic_search").model_dump()
    base["events"] = [{"at_s": 1, "type": "link_set", "drone_id": "ghost", "value": False}]
    with pytest.raises(ValidationError, match="unknown drone"):
        Scenario.model_validate(base)


def test_duplicate_drone_ids_are_rejected() -> None:
    base = load_scenario("basic_search").model_dump()
    base["drones"][1]["drone_id"] = base["drones"][0]["drone_id"]
    with pytest.raises(ValidationError, match="unique"):
        Scenario.model_validate(base)


def test_unknown_fields_are_rejected() -> None:
    base = load_scenario("basic_search").model_dump()
    base["weapons"] = True
    with pytest.raises(ValidationError):
        Scenario.model_validate(base)


def test_default_drain_derives_from_endurance() -> None:
    drone = load_scenario("basic_search").drones[0]
    assert drone.drain_per_s == pytest.approx(100 / drone.capability.nominal_endurance_s)
