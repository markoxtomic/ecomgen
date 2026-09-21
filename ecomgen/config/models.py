"""Pydantic models for market and preset configuration."""

import re
from datetime import date
from decimal import Decimal
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
    model_validator,
)

CHANNELS = {"meta", "google", "organic", "email", "direct"}


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TitleWordsConfig(ConfigModel):
    adjectives: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    nouns: list[str] = Field(min_length=1)


class MarketConfig(ConfigModel):
    code: str = Field(pattern=r"^[a-z]{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    faker_locale: str
    vat_rate: Decimal = Field(ge=0, le=1)
    fx_rate_from_eur: Decimal = Field(gt=0)
    shipping_cost: Decimal = Field(ge=0)
    demand_weight: Decimal = Field(gt=0)
    timezone: str
    order_hour_weights: tuple[Decimal, ...] = Field(min_length=24, max_length=24)
    # Behaviour differences between markets. 1 means "same as the reference market";
    # without them every market behaved identically apart from its size.
    cac_multiplier: Decimal = Field(default=Decimal(1), gt=0, le=5)
    return_rate_multiplier: Decimal = Field(default=Decimal(1), gt=0, le=5)
    repeat_multiplier: Decimal = Field(default=Decimal(1), gt=0, le=5)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError(f"timezone {value!r} must be a valid IANA timezone") from None
        return value

    @field_validator("order_hour_weights")
    @classmethod
    def validate_hour_weights(cls, value: tuple[Decimal, ...]) -> tuple[Decimal, ...]:
        if any(not weight.is_finite() or weight < 0 for weight in value):
            raise ValueError("order_hour_weights must contain 24 finite nonnegative weights")
        if sum(value, Decimal(0)) <= 0:
            raise ValueError("order_hour_weights must have a positive sum")
        return value


class CategoryConfig(ConfigModel):
    price_range: tuple[Decimal, Decimal]
    cost_ratio_range: tuple[Decimal, Decimal]
    variant_options: dict[str, list[str]]
    return_rate: Decimal = Field(ge=0, le=1)
    return_reasons: dict[str, Decimal]
    title_words: TitleWordsConfig

    @model_validator(mode="after")
    def validate_ranges_and_weights(self) -> Self:
        if self.price_range[0] <= 0 or self.price_range[0] > self.price_range[1]:
            raise ValueError("price_range must be strictly positive and ordered")
        low, high = self.cost_ratio_range
        if low < 0 or high > 1 or low > high:
            raise ValueError("cost_ratio_range must be between 0 and 1 and ordered")
        if not self.variant_options or any(not values for values in self.variant_options.values()):
            raise ValueError("variant_options must contain non-empty value lists")
        if not self.return_reasons or any(weight <= 0 for weight in self.return_reasons.values()):
            raise ValueError("return_reasons must contain positive weights")
        return self


class SpecialSpikeConfig(ConfigModel):
    """A recurring demand spike between two `MM-DD` dates, inclusive.

    `02-29` is accepted; in non-leap years that day simply does not occur, so a
    spike covering only `02-29` is skipped rather than rejected. `start` may be
    later than `end` for spikes that wrap across the new year (e.g. `12-20` to
    `01-05`).
    """

    name: str
    start: str
    end: str
    multiplier: Decimal = Field(gt=0)

    @field_validator("start", "end")
    @classmethod
    def validate_calendar_date(cls, value: str, info: ValidationInfo) -> str:
        spike = info.data.get("name", "<unnamed>")
        if not re.fullmatch(r"\d{2}-\d{2}", value):
            raise ValueError(f"spike {spike!r} {info.field_name} {value!r} must use MM-DD format")
        try:
            # 2000 is a leap year, so 02-29 parses; see the class docstring.
            date.fromisoformat(f"2000-{value}")
        except ValueError:
            raise ValueError(
                f"spike {spike!r} {info.field_name} {value!r} is not a valid calendar date"
            ) from None
        return value


class ChannelConfig(ConfigModel):
    weight: Decimal = Field(ge=0, le=1)
    cac_range: tuple[Decimal, Decimal]

    @model_validator(mode="after")
    def validate_cac_range(self) -> Self:
        if self.cac_range[0] < 0 or self.cac_range[0] > self.cac_range[1]:
            raise ValueError("cac_range must be non-negative and ordered")
        return self


class RepeatPurchaseConfig(ConfigModel):
    probability: Decimal = Field(ge=0, le=1)
    average_days: int = Field(gt=0)


class DiscountConfig(ConfigModel):
    usage_rate: Decimal = Field(ge=0, le=1)
    depth_range: tuple[Decimal, Decimal]

    @model_validator(mode="after")
    def validate_depth_range(self) -> Self:
        low, high = self.depth_range
        if low < 0 or high > 1 or low > high:
            raise ValueError("depth_range must be between 0 and 1 and ordered")
        return self


class InventoryConfig(ConfigModel):
    """Per-variant replenishment policy, reviewed at the start of every day.

    A purchase order is placed when a variant's on-hand plus on-order stock
    falls to or below ``max(reorder_point, recent daily sales x lead_time_days)``.
    It orders ``max(restock_quantity, recent daily sales x cover_days)`` units,
    which arrive ``lead_time_days`` later. Recent daily sales are the units sold
    over the trailing 28 days (or fewer at the start) divided by those days.
    """

    reorder_point: int = Field(default=30, ge=0, le=1_000_000)
    restock_quantity: int = Field(default=120, ge=1, le=1_000_000)
    lead_time_days: int = Field(default=10, ge=1, le=3650)
    cover_days: int = Field(default=45, ge=0, le=3650)


class PresetConfig(ConfigModel):
    name: str
    categories: dict[str, CategoryConfig] = Field(min_length=1)
    seasonality: tuple[
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
        Decimal,
    ]
    special_spikes: list[SpecialSpikeConfig]
    channel_mix: dict[str, ChannelConfig]
    repeat_purchase: RepeatPurchaseConfig
    discount: DiscountConfig
    inventory: InventoryConfig = Field(default_factory=InventoryConfig)

    @field_validator("special_spikes", mode="wrap")
    @classmethod
    def name_preset_in_spike_errors(
        cls, value: Any, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> list[SpecialSpikeConfig]:
        try:
            return handler(value)
        except ValidationError as exc:
            preset = info.data.get("name", "<unnamed>")
            details = "; ".join(
                f"special_spikes.{'.'.join(map(str, error['loc']))}: "
                f"{error['msg'].removeprefix('Value error, ')}"
                for error in exc.errors(include_url=False)
            )
            raise ValueError(f"preset {preset!r}: {details}") from None

    @model_validator(mode="after")
    def validate_mix_and_seasonality(self) -> Self:
        if any(value <= 0 for value in self.seasonality):
            raise ValueError("seasonality multipliers must be positive")
        if set(self.channel_mix) != CHANNELS:
            raise ValueError(f"channel_mix must contain exactly {sorted(CHANNELS)}")
        total = sum((channel.weight for channel in self.channel_mix.values()), Decimal(0))
        if abs(total - Decimal(1)) > Decimal("0.0001"):
            raise ValueError("channel_mix weights must sum to 1")
        for name in ("email", "google", "meta"):
            channel = self.channel_mix[name]
            if channel.weight > 0 and channel.cac_range[0] <= 0:
                raise ValueError(
                    f"paid channel {name!r} must have a positive minimum CAC "
                    "when its weight is positive"
                )
        return self
