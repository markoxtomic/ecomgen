"""Pydantic models for market and preset configuration."""

from decimal import Decimal
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

CHANNELS = {"meta", "google", "organic", "email", "direct"}


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class MarketConfig(ConfigModel):
    code: str = Field(pattern=r"^[a-z]{2}$")
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    faker_locale: str
    vat_rate: Decimal = Field(ge=0, le=1)
    fx_rate_from_eur: Decimal = Field(gt=0)
    shipping_cost: Decimal = Field(ge=0)
    demand_weight: Decimal = Field(gt=0)


class CategoryConfig(ConfigModel):
    price_range: tuple[Decimal, Decimal]
    cost_ratio_range: tuple[Decimal, Decimal]
    variant_options: dict[str, list[str]]
    return_rate: Decimal = Field(ge=0, le=1)
    return_reasons: dict[str, Decimal]

    @model_validator(mode="after")
    def validate_ranges_and_weights(self) -> Self:
        if self.price_range[0] < 0 or self.price_range[0] > self.price_range[1]:
            raise ValueError("price_range must be non-negative and ordered")
        low, high = self.cost_ratio_range
        if low < 0 or high > 1 or low > high:
            raise ValueError("cost_ratio_range must be between 0 and 1 and ordered")
        if not self.variant_options or any(not values for values in self.variant_options.values()):
            raise ValueError("variant_options must contain non-empty value lists")
        if not self.return_reasons or any(weight <= 0 for weight in self.return_reasons.values()):
            raise ValueError("return_reasons must contain positive weights")
        return self


class SpecialSpikeConfig(ConfigModel):
    name: str
    start: str = Field(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
    end: str = Field(pattern=r"^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
    multiplier: Decimal = Field(gt=0)


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


class TitleWordsConfig(ConfigModel):
    adjectives: list[str] = Field(min_length=1)
    materials: list[str] = Field(min_length=1)
    nouns: list[str] = Field(min_length=1)


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
    title_words: TitleWordsConfig

    @model_validator(mode="after")
    def validate_mix_and_seasonality(self) -> Self:
        if any(value <= 0 for value in self.seasonality):
            raise ValueError("seasonality multipliers must be positive")
        if set(self.channel_mix) != CHANNELS:
            raise ValueError(f"channel_mix must contain exactly {sorted(CHANNELS)}")
        total = sum((channel.weight for channel in self.channel_mix.values()), Decimal(0))
        if abs(total - Decimal(1)) > Decimal("0.0001"):
            raise ValueError("channel_mix weights must sum to 1")
        return self
