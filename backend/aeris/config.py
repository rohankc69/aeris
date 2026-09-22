"""Environment-based configuration for AERIS.

Every tunable threshold in the system lives here with a descriptive name and a unit suffix,
so nothing in mission, planning, or safety logic carries a magic number. Values come from
environment variables (optionally via a ``.env`` file); nested groups use ``__`` as the
delimiter, e.g. ``AERIS_SAFETY__MIN_RETURN_BATTERY_PERCENT=25``.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"
    PRODUCTION = "production"


class FleetProvider(StrEnum):
    """Which fleet adapter to use. ``fake`` is simulated and is the default."""

    FAKE = "fake"
    PX4 = "px4"


class DecisionProviderKind(StrEnum):
    """Which decision provider answers bounded AI questions."""

    MOCK = "mock"
    RULES = "rules"
    JEV = "jev"


class SafetySettings(BaseModel):
    """Deterministic safety thresholds. These are enforced by code, never by a model."""

    min_return_battery_percent: float = Field(
        25.0, ge=0, le=100, description="Mandatory return-to-base threshold."
    )
    critical_battery_percent: float = Field(
        15.0, ge=0, le=100, description="Below this the drone must land or return immediately."
    )
    return_battery_margin_percent: float = Field(
        5.0, ge=0, description="Extra battery kept above the estimated return cost."
    )
    max_altitude_m: float = Field(120.0, gt=0)
    min_separation_m: float = Field(30.0, gt=0)
    link_degraded_after_s: float = Field(2.0, gt=0)
    link_stale_after_s: float = Field(5.0, gt=0)
    link_lost_after_s: float = Field(10.0, gt=0)

    @model_validator(mode="after")
    def _ordered_thresholds(self) -> SafetySettings:
        if self.critical_battery_percent > self.min_return_battery_percent:
            msg = "critical_battery_percent must not exceed min_return_battery_percent"
            raise ValueError(msg)
        if not (self.link_degraded_after_s < self.link_stale_after_s < self.link_lost_after_s):
            msg = "link thresholds must satisfy degraded < stale < lost"
            raise ValueError(msg)
        return self


class DecisionSettings(BaseModel):
    """Timeouts and resilience for decision providers."""

    timeout_s: float = Field(3.0, gt=0)
    max_retries: int = Field(1, ge=0)
    circuit_breaker_failures: int = Field(3, ge=1)
    circuit_breaker_reset_s: float = Field(30.0, gt=0)
    human_review_probability_threshold: float = Field(0.6, ge=0, le=1)


class Settings(BaseSettings):
    """Top-level AERIS settings, loaded from the environment."""

    model_config = SettingsConfigDict(
        env_prefix="AERIS_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    env: Environment = Environment.DEVELOPMENT
    fleet_provider: FleetProvider = FleetProvider.FAKE
    decision_provider: DecisionProviderKind = DecisionProviderKind.MOCK
    decision_fallback: DecisionProviderKind = DecisionProviderKind.RULES

    # Jev credentials are not prefixed with AERIS_ so they match the vendor's conventions.
    typesafe_api_key: SecretStr | None = Field(default=None, validation_alias="TYPESAFE_API_KEY")
    jev_model: str | None = Field(default=None, validation_alias="JEV_MODEL")

    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    safety: SafetySettings = Field(default_factory=SafetySettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)

    @model_validator(mode="after")
    def _jev_requires_credentials(self) -> Settings:
        if self.decision_provider is DecisionProviderKind.JEV and (
            self.typesafe_api_key is None or not self.typesafe_api_key.get_secret_value()
        ):
            msg = "AERIS_DECISION_PROVIDER=jev requires TYPESAFE_API_KEY"
            raise ValueError(msg)
        if self.decision_fallback is DecisionProviderKind.JEV:
            msg = "AERIS_DECISION_FALLBACK cannot be jev; fallback must work offline"
            raise ValueError(msg)
        return self

    @property
    def persistence_enabled(self) -> bool:
        """True when a database is configured; otherwise the in-memory repository is used."""
        return bool(self.database_url)


def load_settings(**overrides: object) -> Settings:
    """Build ``Settings`` from the environment, with explicit overrides for tests."""
    return Settings(**overrides)  # type: ignore[arg-type]
