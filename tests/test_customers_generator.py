from collections import Counter
from datetime import UTC, date, datetime

import numpy as np
from faker import Faker

from ecomgen.config import load_markets, load_preset
from ecomgen.generators import generate_customers


def _fakers(seed: int):
    fakers = {}
    for index, (code, market) in enumerate(sorted(load_markets().items())):
        faker = Faker(market.faker_locale)
        faker.seed_instance(seed + index)
        fakers[code] = faker
    return fakers


def _generated(seed: int = 42, faker_seed: int = 100, count: int = 137):
    return generate_customers(
        config=load_preset("fashion"),
        markets=load_markets(),
        count=count,
        start_date=date(2025, 1, 1),
        months=3,
        rng=np.random.default_rng(seed),
        fakers=_fakers(faker_seed),
    )


def test_exact_count_and_unique_ids_and_emails() -> None:
    customers = _generated(count=137)

    assert len(customers) == 137
    assert len({customer.id for customer in customers}) == 137
    assert len({customer.email for customer in customers}) == 137

    markets = load_markets()
    total_weight = sum(float(market.demand_weight) for market in markets.values())
    actual = Counter(customer.market for customer in customers)
    for code, market in markets.items():
        ideal = 137 * float(market.demand_weight) / total_weight
        assert abs(actual[code] - ideal) < 1


def test_market_channels_and_created_at_follow_configuration() -> None:
    preset = load_preset("fashion")
    market_codes = set(load_markets())
    start = datetime(2025, 1, 1, tzinfo=UTC)
    end = datetime(2025, 4, 1, tzinfo=UTC)

    customers = _generated()

    assert all(customer.market in market_codes for customer in customers)
    assert all(customer.acquisition_channel in preset.channel_mix for customer in customers)
    assert all(start <= customer.created_at < end for customer in customers)


def test_generation_is_deterministic_for_seeded_rng_and_fakers() -> None:
    first = _generated(seed=7, faker_seed=11)
    second = _generated(seed=7, faker_seed=11)
    different = _generated(seed=8, faker_seed=12)

    assert first == second
    assert first != different
