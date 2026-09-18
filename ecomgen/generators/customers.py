"""Deterministic customer generation."""

from __future__ import annotations

import calendar
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from typing import TypeAlias

import numpy as np
from faker import Faker

from ecomgen.config.models import MarketConfig, PresetConfig
from ecomgen.schemas import Customer

MarketMapping: TypeAlias = Mapping[str, MarketConfig]
FakerMapping: TypeAlias = Mapping[str, Faker]

_NON_EMAIL_COMPONENT = re.compile(r"[^a-z0-9]+")


def _validated_inputs(
    markets: MarketMapping,
    fakers: FakerMapping,
) -> tuple[tuple[str, MarketConfig, Faker], ...]:
    if not markets:
        raise ValueError("at least one market is required")

    mismatches = [key for key, market in markets.items() if key != market.code]
    if mismatches:
        raise ValueError(f"market keys must match market codes: {mismatches}")
    if set(fakers) != set(markets):
        missing = sorted(set(markets) - set(fakers))
        extra = sorted(set(fakers) - set(markets))
        raise ValueError(f"faker keys must match market keys; missing={missing}, extra={extra}")

    return tuple((code, markets[code], fakers[code]) for code in sorted(markets))


def allocate_market_counts(markets: Sequence[MarketConfig], count: int) -> dict[str, int]:
    """Allocate by largest remainder, with market code as the stable tie-breaker."""

    total_weight = sum((market.demand_weight for market in markets), Decimal(0))
    quotas = {
        market.code: Decimal(count) * market.demand_weight / total_weight for market in markets
    }
    allocations = {
        code: int(quota.to_integral_value(rounding=ROUND_FLOOR)) for code, quota in quotas.items()
    }
    remainder = count - sum(allocations.values())
    ranked_codes = sorted(
        quotas,
        key=lambda code: (-(quotas[code] - allocations[code]), code),
    )
    for code in ranked_codes[:remainder]:
        allocations[code] += 1
    return allocations


def _window(start_date: date | datetime, months: int) -> tuple[datetime, datetime]:
    if months <= 0:
        raise ValueError("months must be positive")

    if isinstance(start_date, datetime):
        start = start_date
    else:
        start = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)

    month_index = start.month - 1 + months
    end_year = start.year + month_index // 12
    end_month = month_index % 12 + 1
    end_day = min(start.day, calendar.monthrange(end_year, end_month)[1])
    end = start.replace(year=end_year, month=end_month, day=end_day)
    return start, end


def _created_at(start: datetime, end: datetime, rng: np.random.Generator) -> datetime:
    span_microseconds = (end - start) // datetime.resolution
    offset = int(rng.integers(0, span_microseconds))
    return start + offset * datetime.resolution


def _channel(config: PresetConfig, rng: np.random.Generator) -> str:
    channels = tuple(sorted(config.channel_mix))
    weights = np.array(
        [float(config.channel_mix[channel].weight) for channel in channels],
        dtype=float,
    )
    return channels[int(rng.choice(len(channels), p=weights / weights.sum()))]


def _acquired_customers(
    market_values: tuple[tuple[str, MarketConfig, Faker], ...],
    count: int,
    start_date: date | datetime,
    months: int,
    rng: np.random.Generator,
    acquisitions: Mapping[tuple[str, date, str], int],
) -> list[Customer]:
    if sum(acquisitions.values()) != count:
        raise ValueError("acquisitions must sum to the customer count")
    codes = {code for code, _, _ in market_values}
    unknown = sorted({market for market, _, _ in acquisitions} - codes)
    if unknown:
        raise ValueError(f"acquisitions reference unknown markets: {unknown}")
    start, end = _window(start_date, months)
    customers: list[Customer] = []
    for market_code, _, faker in market_values:
        planned: list[tuple[datetime, str]] = []
        for (market, day, channel), quantity in sorted(acquisitions.items()):
            if market != market_code or quantity <= 0:
                continue
            day_start = datetime.combine(day, datetime.min.time(), tzinfo=start.tzinfo)
            lower = max(day_start, start)
            upper = min(day_start + timedelta(days=1), end)
            if lower >= upper:
                raise ValueError(f"acquisition date {day} is outside the generation window")
            planned.extend((_created_at(lower, upper, rng), channel) for _ in range(quantity))
        planned.sort()
        for market_number, (created_at, channel) in enumerate(planned, start=1):
            customer_id = f"cust-{market_code}-{market_number:06d}"
            customers.append(
                Customer(
                    id=customer_id,
                    market=market_code,
                    email=_email(faker, customer_id),
                    first_name=faker.first_name(),
                    last_name=faker.last_name(),
                    city=faker.city(),
                    created_at=created_at,
                    acquisition_channel=channel,
                )
            )
    return customers


def _email(faker: Faker, customer_id: str) -> str:
    local_part = faker.user_name().encode("ascii", "ignore").decode().casefold()
    local_part = _NON_EMAIL_COMPONENT.sub(".", local_part).strip(".") or "customer"
    return f"{local_part}.{customer_id}@example.test"


def generate_customers(
    config: PresetConfig,
    markets: MarketMapping,
    count: int,
    start_date: date | datetime,
    months: int,
    rng: np.random.Generator,
    fakers: FakerMapping,
    *,
    acquisitions: Mapping[tuple[str, date, str], int] | None = None,
) -> list[Customer]:
    """Generate customers without using global random state.

    ``markets`` and ``fakers`` must be mappings keyed by the same lowercase
    market codes. Each Faker should be configured for that market's
    ``faker_locale`` and seeded by the caller.

    ``acquisitions``, typically from ``generate_marketing``, maps
    ``(market, date, channel)`` to the number of customers acquired that day;
    it must sum to ``count``. Each such customer is created at a uniform time
    on that date (within the window) with that acquisition channel. Without it,
    creation times are uniform over the window and channels follow the
    preset's channel weights.
    """

    if count < 0:
        raise ValueError("count must be non-negative")

    market_values = _validated_inputs(markets, fakers)
    if acquisitions is not None:
        return _acquired_customers(market_values, count, start_date, months, rng, acquisitions)
    allocations = allocate_market_counts([market for _, market, _ in market_values], count)
    start, end = _window(start_date, months)
    customers: list[Customer] = []

    for market_code, _, faker in market_values:
        for market_number in range(1, allocations[market_code] + 1):
            customer_id = f"cust-{market_code}-{market_number:06d}"
            customers.append(
                Customer(
                    id=customer_id,
                    market=market_code,
                    email=_email(faker, customer_id),
                    first_name=faker.first_name(),
                    last_name=faker.last_name(),
                    city=faker.city(),
                    created_at=_created_at(start, end, rng),
                    acquisition_channel=_channel(config, rng),
                )
            )

    return customers
