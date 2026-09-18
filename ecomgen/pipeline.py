"""End-to-end deterministic dataset generation."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
from faker import Faker

from ecomgen.config import load_markets, load_preset
from ecomgen.generators import (
    generate_customers,
    generate_marketing,
    generate_orders,
    generate_products,
    generate_returns,
)
from ecomgen.generators.orders import check_stockouts, order_window
from ecomgen.generators.returns import RETURN_DELAY_MAX_DAYS, RETURN_DELAY_MIN_DAYS
from ecomgen.schemas import Dataset

DEFAULT_PRESET = "garden-decor"
DEFAULT_MARKETS = ("de", "at", "fr")
DEFAULT_CUSTOMERS = 5_000
DEFAULT_MONTHS = 12
DEFAULT_START_DATE = date(2024, 1, 1)
DEFAULT_SEED = 42
SCHEMA_VERSION = 1
PRICING_MODE = "gross_vat_inclusive"


def manifest_metadata(start_date: date, months: int) -> dict[str, object]:
    """Build deterministic metadata describing one generation window."""

    order_start, order_end = order_window(start_date, months)
    return_cutoff = order_end + timedelta(days=RETURN_DELAY_MAX_DAYS)
    return {
        "schema_version": SCHEMA_VERSION,
        "pricing_mode": PRICING_MODE,
        "order_window_start": order_start.date().isoformat(),
        "order_window_end": order_end.date().isoformat(),
        "return_cutoff": return_cutoff.date().isoformat(),
        "return_delay_days": {
            "min": RETURN_DELAY_MIN_DAYS,
            "max": RETURN_DELAY_MAX_DAYS,
        },
    }


def _faker_seed(seed: int, market: str) -> int:
    """Derive a stable Faker seed without touching any random state."""

    digest = hashlib.blake2s(
        f"{seed}:{market}".encode(),
        digest_size=4,
        person=b"ecomgen",
    ).digest()
    return int.from_bytes(digest, "big")


def _base_daily_demand(customer_count: int, months: int, market_weight: Decimal) -> Decimal:
    """Scale demand to the requested population while retaining calendar effects.

    Demand is strictly proportional to the population: a fixed floor would let a
    handful of customers place dozens of orders a year.
    """

    approximate_days = Decimal(months) * Decimal("30.4375")
    population_rate = Decimal(customer_count) / approximate_days / market_weight
    return population_rate * Decimal("0.80")


def generate_dataset(
    preset: str = DEFAULT_PRESET,
    markets: tuple[str, ...] | list[str] = DEFAULT_MARKETS,
    customers: int = DEFAULT_CUSTOMERS,
    months: int = DEFAULT_MONTHS,
    start_date: date = DEFAULT_START_DATE,
    seed: int = DEFAULT_SEED,
    progress: Callable[[int, int], None] | None = None,
) -> Dataset:
    """Generate all dataset tables from one NumPy random stream.

    ``progress``, if given, is called as ``progress(completed, total)`` while the
    dataset is generated; ``completed`` reaches ``total`` when generation ends.
    """

    if customers < 0:
        raise ValueError("customers must be non-negative")
    if months <= 0:
        raise ValueError("months must be positive")
    _, order_end = order_window(start_date, months)
    return_cutoff = order_end + timedelta(days=RETURN_DELAY_MAX_DAYS)

    market_codes = tuple(dict.fromkeys(code.strip().lower() for code in markets if code.strip()))
    if not market_codes:
        raise ValueError("at least one market is required")

    config = load_preset(preset)
    all_markets = load_markets()
    unknown = sorted(set(market_codes) - set(all_markets))
    if unknown:
        choices = ", ".join(sorted(all_markets))
        raise ValueError(f"unknown markets {unknown}; available markets: {choices}")
    selected_markets = {code: all_markets[code] for code in market_codes}

    rng = np.random.default_rng(seed)
    fakers: dict[str, Faker] = {}
    for code, market in selected_markets.items():
        faker = Faker(market.faker_locale)
        faker.seed_instance(_faker_seed(seed, code))
        fakers[code] = faker

    # Order simulation dominates the runtime, so progress is one step per
    # simulated day plus a final step for returns and marketing.
    order_days = 0

    def _order_progress(days_done: int, total_days: int) -> None:
        nonlocal order_days
        order_days = total_days
        if progress is not None:
            progress(days_done, total_days + 1)

    products, variants = generate_products(config, selected_markets, rng)
    # Marketing comes first: spend buys the customers who then place orders.
    marketing = generate_marketing(config, selected_markets, customers, start_date, months, rng)
    customer_records = generate_customers(
        config,
        selected_markets,
        customers,
        start_date,
        months,
        rng,
        fakers,
        acquisitions=marketing.acquisitions,
    )
    total_weight = sum(
        (market.demand_weight for market in selected_markets.values()),
        Decimal(0),
    )
    order_result = generate_orders(
        config,
        selected_markets,
        products,
        variants,
        customer_records,
        start_date,
        months,
        rng,
        base_daily_orders=_base_daily_demand(customers, months, total_weight),
        progress=_order_progress,
    )
    check_stockouts(order_result.intended_orders, order_result.dropped_orders)
    remaining_variants = [
        variant.model_copy(update={"inventory": order_result.inventory[variant.id]})
        for variant in variants
    ]
    return_records = generate_returns(
        config,
        products,
        remaining_variants,
        order_result.orders,
        order_result.order_items,
        rng,
        return_cutoff=return_cutoff,
    )

    if progress is not None:
        progress(order_days + 1, order_days + 1)
    return Dataset(
        products=products,
        variants=remaining_variants,
        customers=customer_records,
        orders=order_result.orders,
        order_items=order_result.order_items,
        returns=return_records,
        marketing_spend=marketing.records,
    )
