"""Deterministic daily marketing performance generation."""

from __future__ import annotations

import calendar
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

import numpy as np

from ecomgen.config.models import CHANNELS, MarketConfig, PresetConfig
from ecomgen.schemas import Customer, MarketingSpend, Order

_CENT = Decimal("0.01")
_CHANNEL_FUNNEL_RANGES = {
    "meta": ((0.008, 0.025), (0.015, 0.060)),
    "google": ((0.015, 0.050), (0.025, 0.100)),
    "email": ((0.040, 0.120), (0.050, 0.180)),
    "organic": ((0.015, 0.060), (0.030, 0.140)),
    "direct": ((0.050, 0.180), (0.080, 0.250)),
}
_NO_PAID_SPEND = {"direct", "organic"}


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


def _funnel(channel: str, attributed_orders: int, rng: np.random.Generator) -> tuple[int, int]:
    if attributed_orders == 0:
        return 0, 0
    ctr_range, conversion_range = _CHANNEL_FUNNEL_RANGES[channel]
    conversion_rate = float(rng.uniform(*conversion_range))
    clicks = max(attributed_orders, math.ceil(attributed_orders / conversion_rate))
    ctr = float(rng.uniform(*ctr_range))
    impressions = max(clicks, math.ceil(clicks / ctr))
    return impressions, clicks


def _spend(
    config: PresetConfig,
    market: MarketConfig,
    channel: str,
    attributed_orders: int,
    rng: np.random.Generator,
) -> Decimal:
    if attributed_orders == 0 or channel in _NO_PAID_SPEND:
        return Decimal("0.00")
    low, high = config.channel_mix[channel].cac_range
    cac_eur = Decimal(str(float(rng.uniform(float(low), float(high)))))
    return (cac_eur * market.fx_rate_from_eur * attributed_orders).quantize(
        _CENT, rounding=ROUND_HALF_UP
    )


def generate_marketing(
    config: PresetConfig,
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig],
    customers: Sequence[Customer],
    orders: Sequence[Order],
    start_date: date | datetime,
    months: int,
    rng: np.random.Generator,
) -> list[MarketingSpend]:
    """Generate one daily record for every configured market and channel.

    Orders are attributed to their customer's acquisition channel. Meta,
    Google, and email receive spend based on configured CAC bounds; direct and
    organic remain unpaid. Spend is expressed in each order market's currency.
    """

    market_values = _market_values(markets)
    market_codes = {market.code for market in market_values}
    start, end = _window(start_date, months)

    customer_by_id = {customer.id: customer for customer in customers}
    if len(customer_by_id) != len(customers):
        raise ValueError("customer ids must be unique")
    if len({order.id for order in orders}) != len(orders):
        raise ValueError("order ids must be unique")

    unknown_customers = sorted(
        {order.customer_id for order in orders if order.customer_id not in customer_by_id}
    )
    if unknown_customers:
        raise ValueError(f"orders reference missing customers: {unknown_customers}")
    invalid_customer_markets = sorted(
        order.id for order in orders if customer_by_id[order.customer_id].market != order.market
    )
    if invalid_customer_markets:
        raise ValueError(f"order market must match customer market: {invalid_customer_markets[:5]}")
    invalid_channels = sorted(
        {
            customer.acquisition_channel
            for customer in customers
            if customer.acquisition_channel not in CHANNELS
        }
    )
    if invalid_channels:
        raise ValueError(f"customers use unsupported channels: {invalid_channels}")
    incompatible = [
        order.id
        for order in orders
        if order.market in market_codes
        and (order.created_at.tzinfo is None) != (start.tzinfo is None)
    ]
    if incompatible:
        raise ValueError(
            f"order created_at timezone awareness must match start_date: {incompatible[:5]}"
        )

    attribution = Counter(
        (
            order.created_at.date(),
            order.market,
            customer_by_id[order.customer_id].acquisition_channel,
        )
        for order in orders
        if order.market in market_codes and start <= order.created_at < end
    )

    records: list[MarketingSpend] = []
    day = start.date()
    while datetime.combine(day, time.min, tzinfo=start.tzinfo) < end:
        for market in market_values:
            for channel in sorted(CHANNELS):
                attributed_orders = attribution[(day, market.code, channel)]
                impressions, clicks = _funnel(channel, attributed_orders, rng)
                records.append(
                    MarketingSpend(
                        date=day,
                        market=market.code,
                        channel=channel,
                        spend=_spend(
                            config,
                            market,
                            channel,
                            attributed_orders,
                            rng,
                        ),
                        impressions=impressions,
                        clicks=clicks,
                        attributed_orders=attributed_orders,
                    )
                )
        day += timedelta(days=1)
    return records
