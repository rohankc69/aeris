import pytest
from pydantic import ValidationError

from aeris.config import DecisionProviderKind, FleetProvider, SafetySettings, load_settings


def test_defaults_are_offline_and_simulated() -> None:
    settings = load_settings(_env_file=None)
    assert settings.fleet_provider is FleetProvider.FAKE
    assert settings.decision_provider is DecisionProviderKind.MOCK
    assert settings.decision_fallback is DecisionProviderKind.RULES
    assert settings.persistence_enabled is False


def test_openrouter_provider_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AERIS_DECISION_PROVIDER", "openrouter")
    with pytest.raises(ValidationError, match="OPENROUTER_API_KEY"):
        load_settings(_env_file=None)


def test_openrouter_provider_with_key_loads_and_redacts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AERIS_DECISION_PROVIDER", "openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "not-a-real-key")
    monkeypatch.setenv("JEV_MODEL", "typesafe/jev-test")
    settings = load_settings(_env_file=None)
    assert settings.jev_model == "typesafe/jev-test"
    assert "not-a-real-key" not in settings.model_dump_json()


def test_model_id_has_a_default_but_is_configurable() -> None:
    assert load_settings(_env_file=None).jev_model == "typesafe/jev-latest"


def test_fallback_cannot_be_hosted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AERIS_DECISION_FALLBACK", "openrouter")
    with pytest.raises(ValidationError, match="offline"):
        load_settings(_env_file=None)


def test_nested_safety_override_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AERIS_SAFETY__MIN_RETURN_BATTERY_PERCENT", "30")
    settings = load_settings(_env_file=None)
    assert settings.safety.min_return_battery_percent == 30.0


def test_critical_battery_cannot_exceed_return_threshold() -> None:
    with pytest.raises(ValidationError, match="critical_battery_percent"):
        SafetySettings(min_return_battery_percent=20, critical_battery_percent=25)


def test_link_thresholds_must_be_ordered() -> None:
    with pytest.raises(ValidationError, match="degraded < stale < lost"):
        SafetySettings(link_degraded_after_s=5, link_stale_after_s=5, link_lost_after_s=10)
