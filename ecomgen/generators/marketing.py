"""Deterministic daily marketing spend and the customer acquisitions it buys.

Causality runs from budget to customers: daily spend per paid channel is drawn
from a budget first, spend buys acquisitions at a CAC that rises with daily
spend (diminishing returns), and the resulting acquisition plan decides when
and through which channel each customer is created. Orders never feed back into
spend, and repeat orders are never charged acquisition cost.
"""

from __future__ import annotations

import calendar
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import NamedTuple

import numpy as np

from ecomgen.config.models import CHANNELS, MarketConfig, PresetConfig
from ecomgen.generators.customers import allocate_market_counts
from ecomgen.generators.orders import _WEEKDAY_MULTIPLIERS, _spike_multiplier
from ecomgen.schemas import MarketingSpend

_CENT = Decimal("0.01")
PAID_CHANNELS = ("email", "google", "meta")
UNPAID_CHANNELS = ("direct", "organic")
# Cost per 1,000 impressions (EUR) and click-through rate ranges per paid channel.
# Email "impressions" are delivered messages.
_PAID_FUNNEL = {
    "email": ((0.80, 2.00), (0.015, 0.040)),
    "google": ((18.0, 35.0), (0.030, 0.070)),
    "meta": ((6.0, 12.0), (0.008, 0.020)),
}
# Daily CAC scales with (daily spend / average planned daily spend) ** SPEND_ELASTICITY,
# so acquisitions grow like spend ** (1 - SPEND_ELASTICITY): diminishing returns.
SPEND_ELASTICITY = 0.35
_SPEND_NOISE_SIGMA = 0.30
_CAC_NOISE_SIGMA = 0.15
_UNPAID_NOISE_SIGMA = 0.20


class AcquisitionPlan(NamedTuple):
    """Daily marketing rows and the customers they acquired.

    ``acquisitions`` maps ``(market, date, channel)`` to the number of customers
    to create on that date; it sums exactly to the requested customer count.
    """

    records: list[MarketingSpend]
    acquisitions: dict[tuple[str, date, str], int]


def _window(start_date: date | datetime, months: int) -> tuple[datetime, datetime]:
    if months <= 0:
        raise ValueError("months must be positive")
    if isinstance(start_date, datetime):
        start = start_date
    else:
        start = datetime.combine(start_date, time.min, tzinfo=UTC)
    month_index = start.month - 1 + months
    end_year = start.year + month_index // 12
    end_month = month_index % 12 + 1
    end_day = min(start.day, calendar.monthrange(end_year, end_month)[1])
    return start, start.replace(year=end_year, month=end_month, day=end_day)


def _market_values(
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig],
) -> tuple[MarketConfig, ...]:
    if isinstance(markets, Mapping):
        mismatches = [key for key, market in markets.items() if key != market.code]
        if mismatches:
            raise ValueError(f"market keys must match market codes: {mismatches}")
        values = tuple(markets.values())
    else:
        values = tuple(markets)
    if not values:
        raise ValueError("at least one market is required")
    ordered = tuple(sorted(values, key=lambda market: market.code))
    if len({market.code for market in ordered}) != len(ordered):
        raise ValueError("market codes must be unique")
    return ordered


def _days(start: datetime, end: datetime) -> list[date]:
    days = []
    day = start.date()
    while datetime.combine(day, time.min, tzinfo=start.tzinfo) < end:
        days.append(day)
        day += timedelta(days=1)
    return days


def _day_factors(config: PresetConfig, days: Sequence[date]) -> np.ndarray:
    return np.array(
        [
            float(
                config.seasonality[day.month - 1]
                * _WEEKDAY_MULTIPLIERS[day.weekday()]
                * _spike_multiplier(config, day)
            )
            for day in days
        ],
        dtype=float,
    )


def _paid_spend_and_intensity(
    expected_customers: float,
    cac_range: tuple[Decimal, Decimal],
    day_factors: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """Return daily EUR spend and the relative acquisitions it buys."""

    low, high = (float(value) for value in cac_range)
    base_cac = float(rng.uniform(low, high)) if high > low else low
    spend_noise = rng.lognormal(0.0, _SPEND_NOISE_SIGMA, size=len(day_factors))
    cac_noise = rng.lognormal(0.0, _CAC_NOISE_SIGMA, size=len(day_factors))
    budget = expected_customers * base_cac
    if budget <= 0:
        return np.zeros(len(day_factors)), np.zeros(len(day_factors))
    planned = budget * day_factors / day_factors.sum()
    spend = planned * spend_noise
    daily_cac = base_cac * (spend / planned.mean()) ** SPEND_ELASTICITY * cac_noise
    return spend, spend / daily_cac


def _allocate(count: int, intensity: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    if count == 0:
        return np.zeros(len(intensity), dtype=np.int64)
    return rng.multinomial(count, intensity / intensity.sum())


def _funnel(
    channel: str,
    spend_eur: float,
    new_customers: int,
    rng: np.random.Generator,
) -> tuple[int, int]:
    cpm_range, ctr_range = _PAID_FUNNEL[channel]
    cpm = float(rng.uniform(*cpm_range))
    ctr = float(rng.uniform(*ctr_range))
    impressions = round(spend_eur / cpm * 1000)
    clicks = int(rng.binomial(impressions, ctr)) if impressions else 0
    clicks = max(clicks, new_customers)
    return max(impressions, clicks), clicks


def generate_marketing(
    config: PresetConfig,
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig],
    customer_count: int,
    start_date: date | datetime,
    months: int,
    rng: np.random.Generator,
) -> AcquisitionPlan:
    """Generate daily spend first, then the customer acquisitions it buys.

    Customers are split across markets by demand weight (largest remainder) and
    across channels by a multinomial draw on the preset channel weights. Each
    paid channel gets a budget of ``expected customers x CAC``, where CAC is
    drawn once per market and channel from ``cac_range``. The budget follows
    the preset seasonality, weekday and spike multipliers with log-normal
    noise; daily CAC rises with daily spend. The channel's customers are then
    placed on days by a multinomial draw proportional to what each day's spend
    bought. Unpaid channels (direct, organic) acquire customers in proportion
    to the calendar multipliers with noise and have zero spend, impressions and
    clicks. There is one row per day, selected market and channel.
    """

    if customer_count < 0:
        raise ValueError("customer_count must be non-negative")
    market_values = _market_values(markets)
    start, end = _window(start_date, months)
    days = _days(start, end)
    day_factors = _day_factors(config, days)
    channels = tuple(sorted(CHANNELS))
    weights = np.array([float(config.channel_mix[channel].weight) for channel in channels])
    shares = weights / weights.sum()
    market_counts = allocate_market_counts(market_values, customer_count)

    acquisitions: dict[tuple[str, date, str], int] = {}
    rows: dict[tuple[str, str], tuple[np.ndarray, np.ndarray]] = {}
    for market in market_values:
        market_customers = market_counts[market.code]
        channel_counts = rng.multinomial(market_customers, shares)
        for index, channel in enumerate(channels):
            if channel in PAID_CHANNELS:
                spend, intensity = _paid_spend_and_intensity(
                    market_customers * float(shares[index]),
                    config.channel_mix[channel].cac_range,
                    day_factors,
                    rng,
                )
            else:
                spend = np.zeros(len(days))
                intensity = day_factors * rng.lognormal(0.0, _UNPAID_NOISE_SIGMA, len(days))
            if intensity.sum() <= 0:
                intensity = day_factors
            allocated = _allocate(int(channel_counts[index]), intensity, rng)
            rows[(market.code, channel)] = (spend, allocated)
            for day, count in zip(days, allocated.tolist(), strict=True):
                if count:
                    acquisitions[(market.code, day, channel)] = count

    records: list[MarketingSpend] = []
    for day_index, day in enumerate(days):
        for market in market_values:
            for channel in channels:
                spend, allocated = rows[(market.code, channel)]
                new_customers = int(allocated[day_index])
                spend_eur = float(spend[day_index])
                if channel in PAID_CHANNELS:
                    impressions, clicks = _funnel(channel, spend_eur, new_customers, rng)
                else:
                    impressions, clicks = 0, 0
                local_spend = (Decimal(repr(spend_eur)) * market.fx_rate_from_eur).quantize(
                    _CENT, rounding=ROUND_HALF_UP
                )
                records.append(
                    MarketingSpend(
                        date=day,
                        market=market.code,
                        channel=channel,
                        currency=market.currency,
                        spend=local_spend,
                        impressions=impressions,
                        clicks=clicks,
                        new_customers=new_customers,
                    )
                )
    return AcquisitionPlan(records, acquisitions)
