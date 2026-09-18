"""M6 regression: repeat behaviour stays plausible regardless of population size."""

import math
from collections import Counter
from datetime import date

import pytest

from ecomgen.config import load_preset
from ecomgen.pipeline import generate_dataset

# garden-decor: repeat probability 0.24, average_days 105; the default window is 366 days.
PRESET = load_preset("garden-decor")
WINDOW_DAYS = (date(2025, 1, 1) - date(2024, 1, 1)).days
# The per-customer ceiling is derived from the inter-purchase interval only.
MAX_REPEATS = math.ceil(2 * WINDOW_DAYS / PRESET.repeat_purchase.average_days)


@pytest.mark.parametrize(
    ("customers", "seeds"),
    [(1, range(30)), (5, range(20)), (50, range(8)), (5000, range(1))],
)
def test_repeat_rate_and_orders_per_customer_stay_plausible(customers: int, seeds: range) -> None:
    orders = 0
    repeats = 0
    per_customer: Counter[str] = Counter()
    for seed in seeds:
        dataset = generate_dataset(markets=("de",), customers=customers, months=12, seed=seed)
        orders += len(dataset.orders)
        repeats += sum(order.is_repeat for order in dataset.orders)
        per_customer.update(f"{seed}:{order.customer_id}" for order in dataset.orders)
    population = customers * len(seeds)

    # Hard ceiling: no single customer exceeds the interval-derived repeat cap (7 repeats in a
    # year for a 105-day interval). Before the fix one customer placed 24 orders a year.
    assert max(per_customer.values(), default=0) <= 1 + MAX_REPEATS
    # Orders per customer: the demand model targets roughly 0.8-1.0 orders per customer
    # per year; 2.0 leaves ample room for sampling noise in tiny populations.
    assert orders / population <= 2.0
    # Repeat share: configured 0.24. Tiny populations cannot sustain more repeats than
    # first orders, so aggregated over seeds the share must stay well below 50%
    # (it was 96% for one customer before the fix).
    if orders:
        assert repeats / orders <= 0.45
    if customers >= 5000:
        assert 0.15 <= repeats / orders <= 0.33
        assert 0.6 <= orders / population <= 1.3
