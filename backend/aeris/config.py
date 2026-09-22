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
    """Which decision provider answers bounded AI questions.

    ``openrouter`` reaches TypeSafe's Jev through OpenRouter as a model gateway. The gateway
    is not the decision-maker; Jev is. ``mock`` and ``rules`` are offline.
    """

    MOCK = "mock"
    RULES = "rules"
    OPENROUTER = "openrouter"

    @property
    def is_hosted(self) -> bool:
        return self is DecisionProviderKind.OPENROUTER


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
    separation_exempt_radius_m: float = Field(
        100.0, ge=0, description="Around base, launch/recovery traffic is exempt from separation"
    )
    geofence_buffer_m: float = Field(
        50.0, ge=0, description="How far outside the search polygon a waypoint may lie"
    )
    restricted_clearance_m: float = Field(
        20.0, ge=0, description="Standoff kept between search paths and restricted regions"
    )
    plan_energy_safety_factor: float = Field(
        1.15, ge=1, description="Multiplier on estimated plan energy before checking reachability"
    )
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
    """Timeouts, resilience and cadence for decision providers."""

    timeout_s: float = Field(3.0, gt=0)
    max_retries: int = Field(1, ge=0)
    circuit_breaker_failures: int = Field(3, ge=1)
    circuit_breaker_reset_s: float = Field(30.0, gt=0)
    human_review_probability_threshold: float = Field(0.6, ge=0, le=1)
    disposition_interval_s: float = Field(
        15.0, gt=0, description="Minimum simulated seconds between disposition decisions per drone"
    )
    zone_priority_interval_s: float = Field(
        60.0, gt=0, description="Minimum simulated seconds between priority scores per zone"
    )
    openrouter_base_url: str = "https://openrouter.ai/api/v1"


class DetectionSettings(BaseModel):
    """How candidate detections are clustered and investigated."""

    cluster_radius_m: float = Field(30.0, gt=0)
    investigate_altitude_m: float = Field(30.0, gt=0)
    investigate_box_m: float = Field(
        40.0, gt=0, description="Side of the box flown around a detection"
    )


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

    # Gateway/model credentials are not prefixed with AERIS_ so they match vendor conventions.
    openrouter_api_key: SecretStr | None = Field(
        default=None, validation_alias="OPENROUTER_API_KEY"
    )
    jev_model: str = Field(default="typesafe/jev-latest", validation_alias="JEV_MODEL")

    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")

    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")

    safety: SafetySettings = Field(default_factory=SafetySettings)
    decision: DecisionSettings = Field(default_factory=DecisionSettings)
    detection: DetectionSettings = Field(default_factory=DetectionSettings)

    @model_validator(mode="after")
    def _hosted_provider_requires_credentials(self) -> Settings:
        if self.decision_provider is DecisionProviderKind.OPENROUTER and not (
            self.openrouter_api_key and self.openrouter_api_key.get_secret_value()
        ):
            msg = "AERIS_DECISION_PROVIDER=openrouter requires OPENROUTER_API_KEY"
            raise ValueError(msg)
        if self.decision_fallback.is_hosted:
            msg = "AERIS_DECISION_FALLBACK must be an offline provider (mock or rules)"
            raise ValueError(msg)
        return self

    @property
    def persistence_enabled(self) -> bool:
        """True when a database is configured; otherwise the in-memory repository is used."""
        return bool(self.database_url)


def load_settings(**overrides: object) -> Settings:
    """Build ``Settings`` from the environment, with explicit overrides for tests."""
    return Settings(**overrides)  # type: ignore[arg-type]
