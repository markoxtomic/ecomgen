"""Market-local timestamp sampling with UTC output."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np

from ecomgen.config.models import MarketConfig


def as_utc(value: datetime) -> datetime:
    """Return an aware UTC datetime, treating legacy naive values as UTC."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def local_day_bounds(day: date, market: MarketConfig) -> tuple[datetime, datetime]:
    """Return one market-local calendar day's bounds in UTC."""

    zone = ZoneInfo(market.timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


class LocalDaySampler:
    """Sample a configured local-hour profile within one calendar day."""

    def __init__(
        self,
        market: MarketConfig,
        day: date,
        *,
        lower: datetime,
        upper: datetime,
    ) -> None:
        self.market = market
        self.day = day
        day_start, day_end = local_day_bounds(day, market)
        self.lower = max(day_start, as_utc(lower))
        self.upper = min(day_end, as_utc(upper))
        self._intervals: list[tuple[datetime, datetime, Decimal]] = []
        zone = ZoneInfo(market.timezone)
        for hour, weight in enumerate(market.order_hour_weights):
            if weight <= 0:
                continue
            local_start = datetime.combine(day, time(hour), tzinfo=zone)
            if hour == 23:
                local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
            else:
                local_end = datetime.combine(day, time(hour + 1), tzinfo=zone)
            start = max(local_start.astimezone(UTC), self.lower)
            end = min(local_end.astimezone(UTC), self.upper)
            if start < end:
                self._intervals.append((start, end, weight))

    @property
    def has_weighted_time(self) -> bool:
        return bool(self._intervals)

    def sample(
        self,
        rng: np.random.Generator,
        *,
        not_before: datetime | None = None,
    ) -> datetime:
        """Sample an instant, conditionally restricted to ``not_before``."""

        threshold = self.lower if not_before is None else max(self.lower, as_utc(not_before))
        eligible: list[tuple[datetime, int, float]] = []
        for interval_start, interval_end, weight in self._intervals:
            start = max(interval_start, threshold)
            duration = (interval_end - start) // datetime.resolution
            if duration > 0:
                eligible.append((start, duration, float(weight) * duration))
        if not eligible:
            if threshold < self.upper:
                return threshold
            raise ValueError(
                f"no valid local timestamp remains on {self.day} for market {self.market.code!r}"
            )
        masses = np.array([mass for _, _, mass in eligible], dtype=float)
        index = int(rng.choice(len(eligible), p=masses / masses.sum()))
        start, duration, _ = eligible[index]
        offset = int(rng.integers(0, duration)) if duration > 1 else 0
        return start + offset * datetime.resolution


def window_day_samplers(
    market: MarketConfig,
    lower: datetime,
    upper: datetime,
) -> tuple[LocalDaySampler, ...]:
    """Build all nonempty local-day samplers intersecting a UTC window."""

    start = as_utc(lower)
    end = as_utc(upper)
    if start >= end:
        raise ValueError("timestamp window must have positive duration")
    zone = ZoneInfo(market.timezone)
    day = start.astimezone(zone).date()
    final_day = (end - datetime.resolution).astimezone(zone).date()
    samplers: list[LocalDaySampler] = []
    while day <= final_day:
        sampler = LocalDaySampler(market, day, lower=start, upper=end)
        if sampler.has_weighted_time:
            samplers.append(sampler)
        day += timedelta(days=1)
    if not samplers:
        raise ValueError(
            f"market {market.code!r} has no positive-weight local hours in the timestamp window"
        )
    return tuple(samplers)
