"""Deterministic order and order-item generation."""

from __future__ import annotations

import calendar
import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import NamedTuple

import numpy as np

from ecomgen.config.models import MarketConfig, PresetConfig
from ecomgen.schemas import Customer, Order, OrderItem, Product, Variant

_CENT = Decimal("0.01")
_WHOLE = Decimal(1)
_WEEKDAY_MULTIPLIERS = (
    Decimal("0.90"),
    Decimal("0.95"),
    Decimal("1.00"),
    Decimal("1.03"),
    Decimal("1.12"),
    Decimal("1.18"),
    Decimal("0.82"),
)


class OrderGenerationResult(NamedTuple):
    """Generated records and immutable-by-convention remaining stock."""

    orders: list[Order]
    order_items: list[OrderItem]
    inventory: dict[str, int]


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
    codes = [market.code for market in ordered]
    if len(codes) != len(set(codes)):
        raise ValueError("market codes must be unique")
    return ordered


def _validate_records(
    products: Sequence[Product],
    variants: Sequence[Variant],
    customers: Sequence[Customer],
    inventory: Mapping[str, int] | None,
) -> tuple[dict[str, Product], dict[str, int]]:
    product_by_id = {product.id: product for product in products}
    if len(product_by_id) != len(products):
        raise ValueError("product ids must be unique")
    variant_ids = {variant.id for variant in variants}
    if len(variant_ids) != len(variants):
        raise ValueError("variant ids must be unique")
    missing_products = sorted({variant.product_id for variant in variants} - set(product_by_id))
    if missing_products:
        raise ValueError(f"variants reference missing products: {missing_products}")
    if len({customer.id for customer in customers}) != len(customers):
        raise ValueError("customer ids must be unique")

    remaining = {variant.id: variant.inventory for variant in variants}
    if inventory is not None:
        unknown = sorted(set(inventory) - variant_ids)
        if unknown:
            raise ValueError(f"inventory contains unknown variants: {unknown}")
        if any(quantity < 0 for quantity in inventory.values()):
            raise ValueError("inventory quantities must be non-negative")
        remaining.update(inventory)
    return product_by_id, remaining


def _same_datetime_kind(left: datetime, right: datetime) -> bool:
    return (left.tzinfo is None) == (right.tzinfo is None)


def _spike_multiplier(config: PresetConfig, day: date) -> Decimal:
    month_day = day.strftime("%m-%d")
    multiplier = Decimal(1)
    for spike in config.special_spikes:
        if spike.start <= spike.end:
            applies = spike.start <= month_day <= spike.end
        else:
            applies = month_day >= spike.start or month_day <= spike.end
        if applies:
            multiplier *= spike.multiplier
    return multiplier


def _daily_order_count(
    config: PresetConfig,
    market: MarketConfig,
    day: date,
    base_daily_orders: Decimal,
    rng: np.random.Generator,
) -> int:
    expected = (
        base_daily_orders
        * market.demand_weight
        * config.seasonality[day.month - 1]
        * _WEEKDAY_MULTIPLIERS[day.weekday()]
        * _spike_multiplier(config, day)
    )
    noise = Decimal(str(float(np.clip(rng.normal(1.0, 0.10), 0.75, 1.25))))
    return int(rng.poisson(float(expected * noise)))


def _local_price(price_eur: Decimal, market: MarketConfig) -> Decimal:
    converted = price_eur * market.fx_rate_from_eur
    if converted < Decimal(100):
        rounded = (converted + Decimal("0.10")).quantize(_WHOLE, rounding=ROUND_HALF_UP) - Decimal(
            "0.10"
        )
        return max(Decimal("0.90"), rounded).quantize(_CENT)
    return converted.quantize(_WHOLE, rounding=ROUND_HALF_UP).quantize(_CENT)


def _weighted_index(weights: Sequence[float], rng: np.random.Generator) -> int:
    probabilities = np.asarray(weights, dtype=float)
    probabilities /= probabilities.sum()
    return int(rng.choice(len(probabilities), p=probabilities))


def _choose_customer(
    first_candidates: Sequence[Customer],
    repeat_candidates: Sequence[Customer],
    last_order_at: Mapping[str, datetime],
    config: PresetConfig,
    day_start: datetime,
    rng: np.random.Generator,
) -> tuple[Customer, bool] | None:
    choose_repeat = bool(
        repeat_candidates and rng.random() < float(config.repeat_purchase.probability)
    )
    if choose_repeat:
        average_days = config.repeat_purchase.average_days
        weights = [
            math.exp(
                -max(1.0, (day_start - last_order_at[customer.id]).total_seconds() / 86400)
                / average_days
            )
            for customer in repeat_candidates
        ]
        return repeat_candidates[_weighted_index(weights, rng)], True
    if first_candidates:
        return first_candidates[int(rng.integers(len(first_candidates)))], False
    # Do not force repeats merely to fill demand: doing so would destroy the
    # configured repeat-order rate when the supplied customer pool is exhausted.
    return None


def _order_timestamp(
    customer: Customer,
    day_start: datetime,
    day_end: datetime,
    window_start: datetime,
    rng: np.random.Generator,
) -> datetime:
    lower = max(day_start, window_start, customer.created_at)
    span_microseconds = (day_end - lower) // datetime.resolution
    if span_microseconds <= 1:
        return lower
    return lower + int(rng.integers(0, span_microseconds)) * datetime.resolution


def _discount(
    config: PresetConfig,
    subtotal: Decimal,
    rng: np.random.Generator,
) -> tuple[Decimal, str | None]:
    if rng.random() >= float(config.discount.usage_rate):
        return Decimal("0.00"), None
    low, high = config.discount.depth_range
    low_bps = int((low * 10000).to_integral_value(rounding=ROUND_CEILING))
    high_bps = int((high * 10000).to_integral_value(rounding=ROUND_FLOOR))
    depth = Decimal(int(rng.integers(low_bps, high_bps + 1))) / Decimal(10000)
    amount = (subtotal * depth).quantize(_CENT, rounding=ROUND_HALF_UP)
    percent = int((depth * 100).to_integral_value(rounding=ROUND_HALF_UP))
    return amount, f"SAVE{percent}"


def _basket(
    market: MarketConfig,
    eligible_variants: Sequence[Variant],
    remaining: dict[str, int],
    rng: np.random.Generator,
) -> list[tuple[Variant, int, Decimal]]:
    in_stock = [variant for variant in eligible_variants if remaining[variant.id] > 0]
    if not in_stock:
        return []

    prices = sorted(_local_price(variant.price_eur, market) for variant in in_stock)
    median_price = prices[len(prices) // 2]
    mean_units = 1.0 + 2.0 / (1.0 + float(median_price) / 60.0)
    target_units = min(6, 1 + int(rng.poisson(max(0.0, mean_units - 1.0))))
    quantities: dict[str, int] = defaultdict(int)
    variant_by_id = {variant.id: variant for variant in in_stock}

    for _ in range(target_units):
        available = [variant for variant in in_stock if remaining[variant.id] > 0]
        if not available:
            break
        weights = [
            1.0 / math.sqrt(max(float(_local_price(variant.price_eur, market)), 0.01))
            for variant in available
        ]
        selected = available[_weighted_index(weights, rng)]
        remaining[selected.id] -= 1
        quantities[selected.id] += 1

    return [
        (
            variant_by_id[variant_id],
            quantity,
            _local_price(variant_by_id[variant_id].price_eur, market),
        )
        for variant_id, quantity in sorted(quantities.items())
    ]


def generate_orders(
    config: PresetConfig,
    markets: Mapping[str, MarketConfig] | Sequence[MarketConfig],
    products: Sequence[Product],
    variants: Sequence[Variant],
    customers: Sequence[Customer],
    start_date: date | datetime,
    months: int,
    rng: np.random.Generator,
    *,
    base_daily_orders: Decimal | float = Decimal(2),
    inventory: Mapping[str, int] | None = None,
) -> OrderGenerationResult:
    """Generate orders using the supplied catalog, customers, stock, and RNG.

    ``base_daily_orders`` is demand for a market with weight 1.0 before
    calendar multipliers. Passing a subset of markets selects only those
    markets. ``inventory`` can continue generation from a previous result.
    Input variants are never mutated.
    """

    base_demand = Decimal(str(base_daily_orders))
    if not base_demand.is_finite() or base_demand < 0:
        raise ValueError("base_daily_orders must be finite and non-negative")
    market_values = _market_values(markets)
    market_codes = {market.code for market in market_values}
    product_by_id, remaining = _validate_records(products, variants, customers, inventory)
    start, end = _window(start_date, months)
    incompatible = [
        customer.id
        for customer in customers
        if customer.market in market_codes and not _same_datetime_kind(customer.created_at, start)
    ]
    if incompatible:
        raise ValueError(
            f"customer created_at timezone awareness must match start_date: {incompatible[:5]}"
        )

    customers_by_market = {
        market.code: sorted(
            (customer for customer in customers if customer.market == market.code),
            key=lambda customer: customer.id,
        )
        for market in market_values
    }
    variants_by_market = {
        market.code: sorted(
            (
                variant
                for variant in variants
                if market.code in product_by_id[variant.product_id].markets
            ),
            key=lambda variant: variant.id,
        )
        for market in market_values
    }

    orders: list[Order] = []
    order_items: list[OrderItem] = []
    last_order_at: dict[str, datetime] = {}
    day = start.date()
    final_day = end.date()

    while day <= final_day:
        day_start = datetime.combine(day, time.min, tzinfo=start.tzinfo)
        day_end = min(day_start + timedelta(days=1), end)
        segment_start = max(day_start, start)
        if segment_start >= day_end:
            day += timedelta(days=1)
            continue

        for market in market_values:
            for _ in range(_daily_order_count(config, market, day, base_demand, rng)):
                if not any(
                    remaining[variant.id] > 0 for variant in variants_by_market[market.code]
                ):
                    break
                market_customers = customers_by_market[market.code]
                first_candidates = [
                    customer
                    for customer in market_customers
                    if customer.id not in last_order_at and customer.created_at < day_end
                ]
                repeat_candidates = [
                    customer
                    for customer in market_customers
                    if customer.id in last_order_at and last_order_at[customer.id] < day_start
                ]
                choice = _choose_customer(
                    first_candidates,
                    repeat_candidates,
                    last_order_at,
                    config,
                    day_start,
                    rng,
                )
                if choice is None:
                    continue
                customer, is_repeat = choice
                basket = _basket(market, variants_by_market[market.code], remaining, rng)
                if not basket:
                    break

                order_number = len(orders) + 1
                order_id = f"ord-{order_number:09d}"
                created_at = _order_timestamp(customer, day_start, day_end, start, rng)
                subtotal = sum(
                    (unit_price * quantity for _, quantity, unit_price in basket),
                    Decimal("0.00"),
                ).quantize(_CENT)
                discount, discount_code = _discount(config, subtotal, rng)
                shipping = market.shipping_cost.quantize(_CENT, rounding=ROUND_HALF_UP)
                taxable = subtotal - discount + shipping
                tax = (taxable * market.vat_rate).quantize(_CENT, rounding=ROUND_HALF_UP)
                total = (taxable + tax).quantize(_CENT)
                orders.append(
                    Order(
                        id=order_id,
                        customer_id=customer.id,
                        market=market.code,
                        created_at=created_at,
                        currency=market.currency,
                        subtotal=subtotal,
                        discount=discount,
                        shipping=shipping,
                        tax=tax,
                        total=total,
                        discount_code=discount_code,
                        is_repeat=is_repeat,
                    )
                )
                for line_number, (variant, quantity, unit_price) in enumerate(basket, start=1):
                    order_items.append(
                        OrderItem(
                            id=f"item-{order_number:09d}-{line_number:02d}",
                            order_id=order_id,
                            variant_id=variant.id,
                            quantity=quantity,
                            unit_price=unit_price,
                        )
                    )
                last_order_at[customer.id] = created_at
        day += timedelta(days=1)

    return OrderGenerationResult(orders, order_items, remaining)
